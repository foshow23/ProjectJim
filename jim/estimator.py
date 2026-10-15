"""AI macro estimates: Claude looks at a photo and/or description of a meal, asks a couple of
quick questions (how oily? how big?), then estimates its nutrition.

Uses the official Anthropic SDK. The key comes from ANTHROPIC_API_KEY (in .env).
"""

from __future__ import annotations

import base64
import io
import os
import uuid
from typing import Literal

import anthropic
from PIL import Image, ImageOps
from pydantic import BaseModel

from .foods import Food, Portion, food_from_amount

# Claude Haiku 5.5: the cheapest current model, with vision and structured outputs.
# Set PROJECTJIM_MODEL (e.g. claude-sonnet-5-5) for more accuracy at a higher price.
DEFAULT_MODEL = "claude-haiku-5-5"

SYSTEM_PROMPT = """\
You help someone log what they eat by estimating the nutrition of a meal from a photo and/or \
a description. They eat a wide range of cuisines, including West African (Nigerian) home \
cooking, so recognize dishes like jollof, egusi, efo riro, ayamase, moi moi, dodo and swallows \
by name.

Before estimating, ask about what most changes the numbers and can't be seen or wasn't said: \
how oily it is, the cooking method (fried, grilled, boiled), sauces or toppings, and portion \
size. Ask at most two questions per turn, each short with 2-4 tap-to-answer options, and \
only when the answer would meaningfully change the estimate. If the photo and description \
are already clear, estimate right away. Once you have answers, estimate even if some \
uncertainty remains; note it in the assumptions.

Give the estimate for the whole portion described or shown, with its weight in grams. Count \
cooking oil, which is often the largest hidden source of calories. Keep "message" to one or \
two friendly sentences; put details in the estimate's assumptions. When you're asking \
questions, set "estimate" to null. If they correct the estimate, return an updated one."""


class Question(BaseModel):
    question: str
    options: list[str]      # tap-to-answer choices, e.g. ["Very oily", "A little", "Not oily"]


class Estimate(BaseModel):
    name: str
    portion: str            # "1 plate (about 350 g)"
    grams: float
    calories: float
    protein: float
    carbs: float
    fat: float
    fiber: float
    sugars: float
    confidence: Literal["low", "medium", "high"]
    assumptions: list[str]

    def to_food(self) -> Food | None:
        """The estimate as a food, so it's logged like any other and lands in Your Foods."""
        food = food_from_amount(
            id=f"custom:ai-{uuid.uuid4()}", name=self.name, brand="AI estimate", quantity=self.grams, units="Grams",
            nutrients={"calories": self.calories, "protein": self.protein, "carbs": self.carbs, "fat": self.fat,
                       "fiber": self.fiber, "sugars": self.sugars},
            source="custom",
        )
        if food:
            food.portions = [Portion(self.portion, self.grams)]
        return food


class Reply(BaseModel):
    """What Claude returns each turn. The estimate is null while it's asking questions."""
    message: str
    questions: list[Question]
    estimate: Estimate | None


class EstimatorError(Exception):
    """A message that's safe to show to the user."""


def model() -> str:
    return os.environ.get("PROJECTJIM_MODEL", "").strip() or DEFAULT_MODEL


def is_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def prepare_photo(data: bytes) -> bytes:
    """A photo ready to send: turned upright, shrunk to at most 1568 px a side (the most Claude
    uses; larger only costs more) and saved as JPEG."""
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    image.thumbnail((1568, 1568))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=85)
    return output.getvalue()


def user_message(text: str, image: bytes | None = None, media_type: str = "image/jpeg") -> dict:
    """A user turn for the conversation: an optional photo, then the text."""
    content: list[dict] = []
    if image:
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type,
                                                    "data": base64.standard_b64encode(image).decode()}})
    content.append({"type": "text", "text": text.strip() or "Estimate the nutrition of this meal."})
    return {"role": "user", "content": content}


def request_options(model_name: str) -> dict:
    """Per-model request settings. Haiku models don't support server-side fallbacks (and Haiku 4.5
    rejects the effort setting), so they get none: Haiku 5.5 runs at its default, medium effort,
    which suits judging portions from a photo. The larger Sonnet and Opus models take both."""
    if model_name.startswith("claude-haiku"):
        return {}
    return {
        # Low effort keeps a chat-style task quick and cheap on the larger models.
        "output_config": {"effort": "low"},
        # If a safety classifier declines, re-run on Anthropic's recommended fallback model.
        "extra_body": {"fallbacks": "default"},
        "extra_headers": {"anthropic-beta": "server-side-fallback-2026-07-01"},
    }


def ask(messages: list[dict], client: anthropic.Anthropic | None = None) -> tuple[Reply, dict]:
    """Sends the conversation and returns Claude's reply plus the assistant message to append
    to the history (kept exactly as returned: the API wants history passed back unchanged)."""
    if client is None:
        if not is_configured():
            raise EstimatorError("Add your Anthropic API key as ANTHROPIC_API_KEY in .env to use the AI estimator "
                                 "(console.anthropic.com → API Keys).")
        client = anthropic.Anthropic()
    try:
        response = client.messages.parse(
            model=model(),
            # Replies are short, but Haiku 5.5 thinks first and that counts toward this limit too.
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=messages,
            output_format=Reply,
            **request_options(model()),
        )
    except anthropic.AuthenticationError as error:
        raise EstimatorError("The Anthropic API key in .env wasn't accepted.") from error
    except anthropic.RateLimitError as error:
        raise EstimatorError("The AI is busy right now. Try again in a moment.") from error
    except anthropic.APIStatusError as error:
        raise EstimatorError(f"The AI isn't available right now ({error.status_code}).") from error
    except anthropic.APIConnectionError as error:
        raise EstimatorError("Couldn't reach the AI. Check your internet connection.") from error

    # Check the stop reason before reading the content: a refusal may not match the schema.
    if response.stop_reason == "refusal":
        raise EstimatorError("The AI couldn't help with that one. Try describing the food differently.")
    if response.parsed_output is None:
        raise EstimatorError("The AI's reply couldn't be read. Try again.")
    return response.parsed_output, {"role": "assistant", "content": response.content}
