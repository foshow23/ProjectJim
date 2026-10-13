"""Online food databases: USDA FoodData Central and Open Food Facts."""

from __future__ import annotations

import os
import re

import requests

from .foods import Food, Portion, density_from_label

TIMEOUT = 15


class FoodSearchError(Exception):
    """A message that's safe to show to the user."""


# --- USDA FoodData Central --------------------------------------------------------------------
# Whole foods (Foundation, SR Legacy) and everyday mixed dishes from many cuisines (FNDDS).
# Free key at api.data.gov/signup, in USDA_API_KEY. Without one, the shared DEMO_KEY allows only
# a few searches an hour.

USDA_DATA_TYPES = "Foundation,SR Legacy,Survey (FNDDS)"


def usda_key() -> str:
    return os.environ.get("USDA_API_KEY", "").strip() or "DEMO_KEY"


def _usda_get(url: str, params: dict) -> dict:
    try:
        response = requests.get(url, params=params | {"api_key": usda_key()}, timeout=TIMEOUT)
    except requests.RequestException as error:
        raise FoodSearchError("Couldn't reach USDA. Your Foods still work offline.") from error
    if response.status_code == 429:
        raise FoodSearchError(
            "USDA's shared demo key is out of searches for this hour. Add your free key as USDA_API_KEY in .env."
            if usda_key() == "DEMO_KEY" else "USDA's search limit was reached. Try again in a few minutes.")
    if not response.ok:
        raise FoodSearchError(f"USDA FoodData Central isn't responding right now ({response.status_code}).")
    return response.json()


def usda_search(query: str, limit: int = 25) -> list[Food]:
    """Search results. They don't include portions (cup, slice, ...); load those with usda_details."""
    data = _usda_get("https://api.nal.usda.gov/fdc/v1/foods/search",
                     {"query": query, "dataType": USDA_DATA_TYPES, "pageSize": 50})
    foods = [food for hit in data.get("foods", []) if (food := usda_food(hit))]
    return usda_rank(foods, query)[:limit]


def usda_details(food: Food) -> Food:
    """The full food, with its measured portions."""
    data = _usda_get(f"https://api.nal.usda.gov/fdc/v1/food/{food.id.split(':')[-1]}", {})
    return usda_food(data) or food


def usda_food(data: dict) -> Food | None:
    """Parses a search hit ({nutrientNumber, value}) or a full food ({nutrient: {number}, amount}).
    Values are per 100 g."""
    if "fdcId" not in data or not data.get("description"):
        return None
    amounts: dict[str, float] = {}
    for entry in data.get("foodNutrients") or []:
        number = entry.get("nutrientNumber") or (entry.get("nutrient") or {}).get("number")
        value = entry.get("value", entry.get("amount"))
        if number and value is not None:
            amounts.setdefault(str(number), float(value))
    protein, carbs, fat = amounts.get("203"), amounts.get("205"), amounts.get("204")
    # Foundation foods often only have the Atwater energy values.
    calories = amounts.get("208", amounts.get("958", amounts.get("957")))
    if calories is None and None not in (protein, carbs, fat):
        calories = 4 * protein + 4 * carbs + 9 * fat
    if calories is None:
        return None
    return Food(id=f"usda:{data['fdcId']}", name=data["description"], source="usda", calories=calories,
                protein=protein, carbs=carbs, fat=fat, fiber=amounts.get("291"),
                sugars=amounts.get("269", amounts.get("2000")), saturated_fat=amounts.get("606"),
                sodium=amounts.get("307"), cholesterol=amounts.get("601"),
                portions=usda_portions(data.get("foodPortions") or []))


def usda_portions(portions: list[dict]) -> list[Portion]:
    """USDA portions as one-unit portions: "1 cup, chopped = 160 g" -> ("cup, chopped", 160)."""
    result: list[Portion] = []
    for portion in portions:
        grams = portion.get("gramWeight")
        if not grams or grams <= 0:
            continue
        amount = portion.get("amount") or 1
        unit = ((portion.get("measureUnit") or {}).get("name") or "").strip()
        modifier = (portion.get("modifier") or "").strip()
        description = (portion.get("portionDescription") or "").strip()
        # FNDDS writes the amount into the description: "1 cup", "1/2 cup, cooked".
        if match := re.match(r"^(\d+/\d+|\d+(?:\.\d+)?)\s+(.+)$", description):
            number = match.group(1)
            amount = float(number.split("/")[0]) / float(number.split("/")[1]) if "/" in number else float(number)
            description = match.group(2)
        if amount <= 0:
            continue
        if unit == "RACC":
            label = "serving"
        elif unit and unit != "undetermined":
            label = f"{unit}, {modifier}" if modifier else unit
        else:
            label = description or modifier
        if not label or label.lower().startswith("quantity not specified"):
            continue
        item = Portion(label, round(grams / amount, 2))
        if item not in result:
            result.append(item)
    return result


def usda_rank(foods: list[Food], query: str) -> list[Food]:
    """USDA's own relevance puts oddities first ("rice" -> "Rice crackers"). Prefer foods named by
    the query ("Rice, white, cooked"), then shorter descriptions."""
    terms = re.findall(r"[a-z0-9]+", query.lower())
    if not terms:
        return foods

    def score(food: Food):
        words = re.findall(r"[a-z0-9]+", food.name.lower())
        head = re.findall(r"[a-z0-9]+", food.name.lower().split(",")[0])
        head_hits = sum(1 for term in terms if any(word.startswith(term) for word in head))
        missing = sum(1 for term in terms if not any(word.startswith(term) for word in words))
        value = len(words) + (len(terms) - head_hits) * 20 + missing * 50
        if len(head) == len(terms) == head_hits:
            value -= 30
        return (value, food.name)

    return sorted(foods, key=score)


# --- Open Food Facts --------------------------------------------------------------------------
# A free, open database of about 3 million packaged products, searched live (it's far too big
# to store). No key needed, but it asks apps to identify themselves.

OFF_HEADERS = {"User-Agent": "ProjectJim/2.0 (personal nutrition tracker)"}
OFF_FIELDS = ("code,product_name,brands,nutriments,serving_size,serving_quantity,serving_quantity_unit,"
              "product_quantity,product_quantity_unit,quantity")
ASSUMED_SERVING = "serving (100 g, none listed)"


def _off_get(url: str, params: dict) -> dict:
    try:
        response = requests.get(url, params=params, headers=OFF_HEADERS, timeout=TIMEOUT)
    except requests.RequestException as error:
        raise FoodSearchError("Couldn't reach Open Food Facts. Your Foods still work offline.") from error
    if response.status_code == 404:
        raise FoodSearchError("That barcode isn't in Open Food Facts yet.")
    if not response.ok:
        raise FoodSearchError(f"Open Food Facts isn't responding right now ({response.status_code}). Try again shortly.")
    return response.json()


def off_search(query: str, limit: int = 40) -> list[Food]:
    """Search results. The search index has the package size but not the serving size; load that
    with off_product when a food is picked."""
    data = _off_get("https://search.openfoodfacts.org/search", {"q": query, "page_size": limit, "fields": OFF_FIELDS})
    return [food for hit in data.get("hits", []) if (food := off_food(hit))]


def off_product(barcode: str) -> Food:
    """Looks a product up by barcode. The full record includes the serving size."""
    code = re.sub(r"\D", "", barcode)
    data = _off_get(f"https://world.openfoodfacts.org/api/v2/product/{code}", {"fields": OFF_FIELDS})
    food = off_food(data.get("product") or {}, code) if data.get("status") == 1 else None
    if food is None:
        raise FoodSearchError("That barcode isn't in Open Food Facts yet.")
    return food


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def off_food(product: dict, code: str | None = None) -> Food | None:
    """Converts a product. Nutriments are per 100 g (or 100 ml for drinks); sodium and
    cholesterol are in grams there."""
    code = code or product.get("code")
    name = (product.get("product_name") or "").strip()
    nutriments = product.get("nutriments") or {}
    if not code or not name:
        return None

    def value(key: str) -> float | None:
        return _number(nutriments.get(f"{key}_100g"))

    calories = value("energy-kcal")
    if calories is None and (kilojoules := value("energy")) is not None:
        calories = kilojoules / 4.184
    if calories is None:
        return None

    brands = product.get("brands")
    brand = (brands[0] if isinstance(brands, list) and brands else (brands or "").split(",")[0] if brands else "").strip()
    serving_grams = _number(product.get("serving_quantity"))
    serving_size = product.get("serving_size")
    portions: list[Portion] = []
    if serving_grams and serving_grams > 0:
        portions.append(Portion("serving" + (f" ({serving_size})" if serving_size else ""), serving_grams))
    else:
        # No serving listed: a 100 g serving, the basis nutrition labels use.
        portions.append(Portion(ASSUMED_SERVING, 100.0))
    package = off_package_grams(product)
    if package and 0 < package < 20_000 and not any(abs(p.grams - package) < 0.5 for p in portions):
        portions.append(Portion(f"package ({package:g} g)", package))

    is_liquid = "ml" in ((product.get("serving_quantity_unit") or "").lower(),
                         (product.get("product_quantity_unit") or "").lower())
    # "250 ml" servings of drinks report 250 as the quantity; that's ml, not a measured density.
    density = None
    if not is_liquid and serving_size and serving_grams:
        density = density_from_label(serving_size, serving_grams)
    sodium, cholesterol = value("sodium"), value("cholesterol")
    return Food(id=f"off:{code}", name=name, brand=brand or None, source="off", calories=calories,
                protein=value("proteins"), carbs=value("carbohydrates"), fat=value("fat"), fiber=value("fiber"),
                sugars=value("sugars"), saturated_fat=value("saturated-fat"),
                sodium=sodium * 1000 if sodium is not None else None,
                cholesterol=cholesterol * 1000 if cholesterol is not None else None,
                portions=portions, density=density, sold_by_volume=is_liquid)


def off_package_grams(product: dict) -> float | None:
    """The whole package's weight in grams (or ml for drinks): from product_quantity, or by
    reading the label text in "quantity" ("500 g", "1 lb", "1,5 L", "6 x 330 ml")."""
    unit = (product.get("product_quantity_unit") or "").lower()
    quantity = _number(product.get("product_quantity"))
    if quantity is not None and unit in ("", "g", "ml"):
        return quantity
    factors = {"g": 1, "gr": 1, "grams": 1, "kg": 1000, "mg": 0.001, "oz": 28.349523125, "lb": 453.59237,
               "lbs": 453.59237, "ml": 1, "cl": 10, "dl": 100, "l": 1000}
    match = re.search(r"(?:(\d+)\s*[x×]\s*)?(\d+(?:[.,]\d+)?)\s*([a-z]+)", (product.get("quantity") or "").lower())
    if not match or match.group(3) not in factors:
        return None
    count = float(match.group(1)) if match.group(1) else 1.0
    return count * float(match.group(2).replace(",", ".")) * factors[match.group(3)]


def has_real_serving(food: Food) -> bool:
    return any(p.label.startswith("serving") and p.label != ASSUMED_SERVING for p in food.portions)


def with_details(food: Food) -> Food:
    """Search results lack portions (USDA) or the serving size (Open Food Facts); fetch the full
    food for them. Returns the food unchanged if there's nothing more to learn or the lookup fails."""
    try:
        if food.source == "usda" and not food.portions:
            full = usda_details(food)
            return full if full.portions else food
        if food.source == "off" and not has_real_serving(food):
            full = off_product(food.id.split(":")[-1])
            return full if has_real_serving(full) or full.density else food
    except FoodSearchError:
        pass
    return food
