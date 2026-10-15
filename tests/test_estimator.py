"""The AI macro estimator: what's sent to Claude and how replies and failures are handled.
No network: a stand-in client plays Claude."""

import base64
import io
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from PIL import Image

from jim import estimator
from jim.estimator import Estimate, Question, Reply


class FakeClient:
    """Records each request and returns (or raises) what the test queued up."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []
        self.messages = SimpleNamespace(parse=self.parse)

    def parse(self, **request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def response(reply: Reply | None, stop_reason="end_turn"):
    return SimpleNamespace(parsed_output=reply, stop_reason=stop_reason, content=[{"type": "text", "text": "raw"}])


ESTIMATE = Estimate(name="Jollof rice with chicken", portion="1 plate (about 450 g)", grams=450, calories=720, protein=38,
                    carbs=85, fat=24, fiber=4, sugars=6, confidence="medium", assumptions=["Fried in about 2 tbsp oil"])


@pytest.fixture(autouse=True)
def default_model(monkeypatch):
    monkeypatch.delenv("PROJECTJIM_MODEL", raising=False)


def test_uses_the_cost_efficient_model_by_default(monkeypatch):
    assert estimator.model() == "claude-haiku-5-5"
    assert estimator.request_options("claude-haiku-5-5") == {}       # Haiku: no fallbacks, default effort
    monkeypatch.setenv("PROJECTJIM_MODEL", "claude-sonnet-5-5")
    assert estimator.model() == "claude-sonnet-5-5"
    options = estimator.request_options("claude-sonnet-5-5")
    assert options["output_config"] == {"effort": "low"}
    assert options["extra_body"] == {"fallbacks": "default"}


def test_a_message_carries_the_photo_then_the_text():
    message = estimator.user_message(" jollof and chicken ", image=b"\xff\xd8photo")
    assert message["role"] == "user"
    image, text = message["content"]
    assert image["source"] == {"type": "base64", "media_type": "image/jpeg",
                               "data": base64.standard_b64encode(b"\xff\xd8photo").decode()}
    assert text == {"type": "text", "text": "jollof and chicken"}
    # A photo with no words still asks for something.
    assert estimator.user_message("", image=b"x")["content"][-1]["text"] == "Estimate the nutrition of this meal."
    assert len(estimator.user_message("just words")["content"]) == 1


def test_photos_are_shrunk_and_turned_into_jpeg():
    original = io.BytesIO()
    Image.new("RGBA", (4000, 3000), "orange").save(original, format="PNG")
    prepared = Image.open(io.BytesIO(estimator.prepare_photo(original.getvalue())))
    assert (prepared.format, prepared.mode, prepared.size) == ("JPEG", "RGB", (1568, 1176))


def test_a_question_turn_then_an_estimate():
    asking = Reply(message="How oily was it?", estimate=None,
                   questions=[Question(question="How oily?", options=["Very", "A little", "Not at all"])])
    client = FakeClient(response(asking), response(Reply(message="Here you go.", questions=[], estimate=ESTIMATE)))

    messages = [estimator.user_message("jollof rice with chicken")]
    reply, assistant = estimator.ask(messages, client=client)
    assert reply.questions[0].options == ["Very", "A little", "Not at all"] and reply.estimate is None
    # The assistant turn goes back into the history exactly as it came.
    assert assistant == {"role": "assistant", "content": [{"type": "text", "text": "raw"}]}

    messages += [assistant, estimator.user_message("How oily? A little")]
    reply, _ = estimator.ask(messages, client=client)
    assert reply.estimate.calories == 720

    request = client.requests[1]
    assert request["model"] == "claude-haiku-5-5"
    assert request["output_format"] is Reply
    assert request["messages"] == messages and len(messages) == 3
    assert "Nigerian" in request["system"] and "oil" in request["system"]


def test_an_estimate_becomes_a_food_you_can_log():
    food = ESTIMATE.to_food()
    assert (food.name, food.brand, food.source) == ("Jollof rice with chicken", "AI estimate", "custom")
    assert food.id.startswith("custom:ai-")
    assert (food.default_unit(), food.default_amount()) == ("1 plate (about 450 g)", 1.0)
    whole = food.nutrients(food.grams(1, food.default_unit()))
    assert (whole["calories"], whole["protein"], whole["fat"]) == pytest.approx((720, 38, 24))
    assert food.nutrients(225)["calories"] == pytest.approx(360)      # and it can be logged by weight


def test_failures_become_messages_safe_to_show():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

    def status(code):
        return httpx2.Response(code, request=request)

    cases = [
        (anthropic.AuthenticationError("no", response=status(401), body=None), "wasn't accepted"),
        (anthropic.RateLimitError("slow", response=status(429), body=None), "busy"),
        (anthropic.InternalServerError("down", response=status(529), body=None), "529"),
        (anthropic.APIConnectionError(request=request), "internet"),
        (response(None, stop_reason="refusal"), "couldn't help"),
        (response(None), "couldn't be read"),
    ]
    for outcome, expected in cases:
        with pytest.raises(estimator.EstimatorError, match=expected):
            estimator.ask([estimator.user_message("food")], client=FakeClient(outcome))


def test_without_a_key_it_says_how_to_add_one(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert not estimator.is_configured()
    with pytest.raises(estimator.EstimatorError, match="ANTHROPIC_API_KEY"):
        estimator.ask([estimator.user_message("food")])
