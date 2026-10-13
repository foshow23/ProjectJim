"""Foods, the units they can be measured in, and weight <-> volume conversion."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

# --- Units ------------------------------------------------------------------------------------

WEIGHT_UNITS = {"g": 1.0, "kg": 1000.0, "mg": 0.001, "oz": 28.349523125, "lb": 453.59237}       # grams
VOLUME_UNITS = {"ml": 1.0, "L": 1000.0, "tsp": 4.92892159375, "tbsp": 14.78676478125,           # milliliters
                "fl oz": 29.5735295625, "cup": 236.5882365, "pint": 473.176473, "quart": 946.352946}

# Volume measures that can appear at the start of a portion label ("cup, chopped", "tbsp").
_VOLUME_LABELS = [("fl oz", 29.5735295625), ("cup", 236.5882365), ("tablespoon", 14.78676478125),
                  ("tbsp", 14.78676478125), ("teaspoon", 4.92892159375), ("tsp", 4.92892159375),
                  ("milliliter", 1.0), ("ml", 1.0), ("liter", 1000.0), ("pint", 473.176473), ("quart", 946.352946)]


def milliliters_in_portion_label(label: str) -> float | None:
    """ "cup, chopped" -> 236.6, "tbsp" -> 14.8; None if the label isn't a volume measure."""
    text = label.lower()
    for name, milliliters in _VOLUME_LABELS:
        if text == name or text.startswith(name + ",") or text.startswith(name + " "):
            return milliliters
    return None


# --- Densities --------------------------------------------------------------------------------
# Grams per milliliter for common foods, so a tablespoon of honey weighs 21 g, not 15.
# From King Arthur Baking's ingredient weight chart (grams per cup or tbsp, divided by the US cup
# of 236.6 ml), except thin liquids, where that chart rounds a cup to 8 oz; those use standard
# physical densities (water 1.0, milk 1.03, oils 0.92).
_DENSITIES = [
    # Syrups and sweeteners
    ("maple syrup", 1.32), ("corn syrup", 1.32), ("agave", 1.42), ("honey", 1.42), ("molasses", 1.44),
    ("syrup", 1.32), ("sweetened condensed", 1.32), ("jam", 1.44), ("preserves", 1.44), ("jelly", 1.44),
    ("marmalade", 1.44),
    # Spreads, fats and oils
    ("peanut butter", 1.14), ("almond butter", 1.15), ("cookie butter", 1.22), ("coconut oil", 0.96),
    ("butter", 0.96), ("oil", 0.92), ("mayonnaise", 0.96), ("mayo", 0.96), ("cream cheese", 0.96),
    ("sour cream", 0.96),
    # Dairy and drinks
    ("milk", 1.03), ("buttermilk", 1.03), ("cream", 1.0), ("yogurt", 1.03), ("yoghurt", 1.03),
    ("juice", 1.04), ("soda", 1.04), ("cola", 1.04), ("beer", 1.01), ("wine", 0.99),
    ("coffee", 1.0), ("tea", 1.0), ("water", 1.0),
    # Flours, starches and sugars
    ("rice flour", 0.60), ("almond flour", 0.41), ("flour", 0.51), ("cornstarch", 0.47), ("corn starch", 0.47),
    ("brown sugar", 0.90), ("powdered sugar", 0.48), ("icing sugar", 0.48), ("confectioners sugar", 0.48),
    ("coconut sugar", 0.65), ("sugar", 0.84), ("cocoa", 0.36),
    # Grains and other dry goods
    ("rice cooked", 0.72), ("oats cooked", 0.62), ("oatmeal cooked", 0.62), ("rice", 0.84),
    ("oats", 0.38), ("oatmeal", 0.38), ("salt", 1.22), ("chocolate chips", 0.72), ("raisins", 0.63),
]
# Most specific first, so "peanut butter" wins over "butter".
_DENSITIES_BY_WORDS = sorted(((name.split(), density) for name, density in _DENSITIES), key=lambda e: -len(e[0]))


def typical_density(name: str) -> float | None:
    """Grams per milliliter if the name matches a known food (whole words, any order)."""
    words = set(re.findall(r"[a-z]+", name.lower()))
    return next((density for needed, density in _DENSITIES_BY_WORDS if all(w in words for w in needed)), None)


def density_from_label(serving_size: str, serving_grams: float) -> float | None:
    """A product's own density from a serving label that gives both volume and weight:
    "1 tbsp (21 g)" -> 21 / 14.8 = 1.42 g/ml. None if there's no volume or the ratio is odd."""
    match = re.search(r"(\d+/\d+|\d+(?:[.,]\d+)?)\s*(tbsp|tablespoons?|tsp|teaspoons?|cups?|fl\.?\s?oz|ml|l)\b",
                      serving_size.lower())
    if not match or serving_grams <= 0:
        return None
    number = match.group(1).replace(",", ".")
    amount = (float(number.split("/")[0]) / float(number.split("/")[1])) if "/" in number else float(number)
    unit = match.group(2).replace(".", "").replace(" ", "")
    milliliters = {"tbsp": 14.78676478125, "tablespoon": 14.78676478125, "tablespoons": 14.78676478125,
                   "tsp": 4.92892159375, "teaspoon": 4.92892159375, "teaspoons": 4.92892159375,
                   "cup": 236.5882365, "cups": 236.5882365, "floz": 29.5735295625, "ml": 1.0, "l": 1000.0}[unit]
    if amount <= 0:
        return None
    density = serving_grams / (amount * milliliters)
    return density if 0.2 <= density <= 2.5 else None


# --- Foods ------------------------------------------------------------------------------------

@dataclass
class Portion:
    label: str      # "slice", "serving (30 g)", "cup, chopped"
    grams: float    # grams in one portion


@dataclass
class Food:
    """A food from USDA, Open Food Facts, your Lose It! history or one you created.

    Nutrients are per 100 g. `portions` are the food's own measures.
    """
    id: str                     # "usda:171705", "off:<barcode>", "loseit:agege bread", "custom:<uuid>"
    name: str
    source: str                 # usda, off, loseit, custom
    calories: float
    brand: str | None = None
    protein: float | None = None
    carbs: float | None = None
    fat: float | None = None
    fiber: float | None = None
    sugars: float | None = None
    saturated_fat: float | None = None
    sodium: float | None = None         # mg
    cholesterol: float | None = None    # mg
    portions: list[Portion] = field(default_factory=list)
    density: float | None = None        # g/ml from the product's own label
    sold_by_volume: bool = False        # a drink or liquid sold in ml
    # Only measurable in its own portions ("1 plate"): its weight isn't known, so the portion's
    # grams are a stand-in and weight/volume units aren't offered.
    portion_only: bool = False

    @property
    def display_name(self) -> str:
        return f"{self.name} · {self.brand}" if self.brand else self.name

    @property
    def source_name(self) -> str:
        return {"usda": "USDA", "off": "Open Food Facts", "loseit": "your Lose It! log",
                "custom": "a food you created"}.get(self.source, self.source)

    # Units ----------------------------------------------------------------------------------

    def unit_labels(self) -> list[str]:
        """Every unit this food can be measured in: its own portions, then weight, then volume."""
        own = [p.label for p in self.portions]
        return own if self.portion_only else own + list(WEIGHT_UNITS) + list(VOLUME_UNITS)

    def default_unit(self) -> str:
        """A serving if there's one, else grams (never the whole package)."""
        return next((p.label for p in self.portions if not p.label.startswith("package")), "g")

    def default_amount(self) -> float:
        return 100.0 if self.default_unit() == "g" else 1.0

    def density_info(self) -> tuple[float, str]:
        """Grams per milliliter and where it came from, most reliable first: "label" (the
        product's own serving), "portion" (a measured volume portion), "typical" (this kind of
        food), "liquid" (a drink, treated as water) or "assumed" (a guess: water)."""
        if self.density:
            return self.density, "label"
        for portion in self.portions:
            if milliliters := milliliters_in_portion_label(portion.label):
                return portion.grams / milliliters, "portion"
        if typical := typical_density(self.name):
            return typical, "typical"
        return (1.0, "liquid") if self.sold_by_volume else (1.0, "assumed")

    def density_explanation(self) -> str:
        value, source = self.density_info()
        return {
            "label": f"Volume uses this product's label: {value:.2f} g per ml.",
            "portion": f"Volume uses this food's measured portion: {value:.2f} g per ml.",
            "typical": f"Volume uses the typical density for this food: {value:.2f} g per ml.",
            "liquid": "Volume treats this drink like water: 1 g per ml.",
            "assumed": "This food's density isn't known, so volume is converted as if it weighs like water "
                       "(1 g per ml). Use weight for accuracy.",
        }[source]

    def grams(self, amount: float, unit: str) -> float:
        for portion in self.portions:
            if portion.label == unit:
                return amount * portion.grams
        if unit in WEIGHT_UNITS:
            return amount * WEIGHT_UNITS[unit]
        if unit in VOLUME_UNITS:
            return amount * VOLUME_UNITS[unit] * self.density_info()[0]
        raise ValueError(f"Unknown unit: {unit}")

    def milliliters(self, grams: float) -> float:
        return grams / self.density_info()[0]

    def nutrients(self, grams: float) -> dict[str, float | None]:
        """Nutrition for an amount of this food."""
        def scaled(per_100: float | None) -> float | None:
            return None if per_100 is None else per_100 * grams / 100
        return {"calories": self.calories * grams / 100, "protein": scaled(self.protein), "carbs": scaled(self.carbs),
                "fat": scaled(self.fat), "fiber": scaled(self.fiber), "sugars": scaled(self.sugars),
                "saturated_fat": scaled(self.saturated_fat), "sodium": scaled(self.sodium),
                "cholesterol": scaled(self.cholesterol)}

    # Storage --------------------------------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str | None) -> "Food | None":
        if not text:
            return None
        try:
            data = json.loads(text)
            data["portions"] = [Portion(**p) for p in data.get("portions", [])]
            return cls(**data)
        except (ValueError, TypeError):
            return None


# --- Foods from "quantity units = nutrition" ---------------------------------------------------

_WEIGHT_NAMES = {"gram": 1.0, "grams": 1.0, "ounce": 28.349523125, "ounces": 28.349523125}
_VOLUME_NAMES = {"milliliter": (1.0, "ml"), "milliliters": (1.0, "ml"),
                 "fluid ounce": (29.5735295625, "fl oz"), "fluid ounces": (29.5735295625, "fl oz"),
                 "imperial fluid ounce": (28.413, "imperial fl oz"), "imperial fluid ounces": (28.413, "imperial fl oz"),
                 "cup": (236.5882365, "cup"), "cups": (236.5882365, "cup"),
                 "tablespoon": (14.78676478125, "tbsp"), "tablespoons": (14.78676478125, "tbsp"),
                 "teaspoon": (4.92892159375, "tsp"), "teaspoons": (4.92892159375, "tsp")}


def singular(units: str) -> str:
    """ "Slices" -> "slice", "Servings" -> "serving", "Pouches" -> "pouch"."""
    lower = units.strip().lower()
    if lower.endswith(("ches", "xes")):
        return lower[:-2]
    if lower.endswith("s") and not lower.endswith("ss"):
        return lower[:-1]
    return lower or "serving"


def food_from_amount(*, id: str, name: str, quantity: float, units: str, nutrients: dict, brand: str | None = None,
                     source: str = "loseit") -> Food | None:
    """A food from nutrition recorded for an amount ("2 Slices = 280 kcal"), the way Lose It!
    and food labels work. Weights and volumes become real per-100 g foods; other units ("slice",
    "serving") become portion-only foods measured in that unit."""
    if quantity <= 0 or not name:
        return None
    unit = units.strip().lower()
    portions: list[Portion] = []
    density = None
    portion_only = False
    if unit in _WEIGHT_NAMES:
        grams = quantity * _WEIGHT_NAMES[unit]
        # Start from the amount usually eaten, not 100 g.
        portions = [Portion(f"serving ({grams:g} g)", grams)]
    elif unit in _VOLUME_NAMES:
        milliliters, label = _VOLUME_NAMES[unit]
        density = typical_density(name) or 1.0
        grams = quantity * milliliters * density
        portions = [Portion(label, milliliters * density)]
    else:
        # Weight unknown: one portion stands in as 100 g, so per-100 g values are per portion.
        grams = quantity * 100
        portions = [Portion(singular(units), 100.0)]
        portion_only = True

    def per_100(key: str) -> float | None:
        value = nutrients.get(key)
        return None if value is None else value / grams * 100

    return Food(id=id, name=name, source=source, brand=brand or None, calories=(nutrients.get("calories") or 0) / grams * 100,
                protein=per_100("protein"), carbs=per_100("carbs"), fat=per_100("fat"), fiber=per_100("fiber"),
                sugars=per_100("sugars"), saturated_fat=per_100("saturated_fat"), sodium=per_100("sodium"),
                cholesterol=per_100("cholesterol"), portions=portions, density=density, portion_only=portion_only)
