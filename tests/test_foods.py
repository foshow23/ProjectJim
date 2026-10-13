"""Units, portions and weight <-> volume conversion."""

import pytest

from jim.foods import Food, Portion, density_from_label, food_from_amount, singular, typical_density


def food(name="Oats", **values) -> Food:
    return Food(id="t:1", name=name, source="usda", calories=380.0, protein=13.0, **values)


def test_weight_units():
    oats = food()
    assert oats.grams(50, "g") == 50
    assert oats.grams(1, "kg") == 1000
    assert oats.grams(1, "oz") == pytest.approx(28.35, abs=0.01)
    assert oats.grams(1, "lb") == pytest.approx(453.59, abs=0.01)
    half = oats.nutrients(50)
    assert (half["calories"], half["protein"]) == (190.0, 6.5)
    assert half["carbs"] is None        # unknown stays unknown rather than becoming 0


def test_a_food_with_no_serving_defaults_to_100_grams():
    assert (food().default_unit(), food().default_amount()) == ("g", 100.0)
    sliced = food(portions=[Portion("package (500 g)", 500), Portion("slice", 30)])
    assert (sliced.default_unit(), sliced.default_amount()) == ("slice", 1.0)     # never the whole package
    assert sliced.grams(2, "slice") == 60


def test_volume_uses_the_food_s_density():
    honey = food("Honey")                       # typical density 1.42 g/ml
    assert honey.density_info() == (1.42, "typical")
    assert honey.grams(1, "tbsp") == pytest.approx(21.0, abs=0.05)
    assert honey.milliliters(142) == pytest.approx(100)
    assert food("Maple syrup").grams(100, "ml") == pytest.approx(132)
    assert food("All-purpose flour").grams(1, "cup") == pytest.approx(120.7, abs=0.1)


def test_density_sources_most_reliable_first():
    labeled = food("Honey", density=1.5, portions=[Portion("cup", 300)])
    assert labeled.density_info() == (1.5, "label")                                 # the product's own label
    measured = food("Mystery stew", portions=[Portion("cup, cooked", 250)])
    assert measured.density_info() == (pytest.approx(1.057, abs=0.001), "portion")     # a measured cup
    assert food("Sports drink", sold_by_volume=True).density_info() == (1.0, "liquid")
    unknown = food("Mystery stew")
    assert unknown.density_info() == (1.0, "assumed")
    assert "isn't known" in unknown.density_explanation()


def test_typical_density_matches_whole_words_most_specific_first():
    assert typical_density("Peanut Butter, smooth") == 1.14       # not plain butter (0.96)
    assert typical_density("Butter, salted") == 0.96
    assert typical_density("Olive oil") == 0.92
    assert typical_density("Boiled egg") is None                  # "oil" inside "boiled" doesn't count


def test_density_from_a_serving_label():
    assert density_from_label("1 tbsp (21 g)", 21) == pytest.approx(1.42, abs=0.01)
    assert density_from_label("1/4 cup (30 g)", 30) == pytest.approx(0.507, abs=0.001)
    assert density_from_label("2 cookies (30 g)", 30) is None     # no volume in the label
    assert density_from_label("1 cup (2000 g)", 2000) is None     # impossible, so ignored


def test_food_from_a_logged_amount():
    # Logged by weight: a real per-100 g food that starts from the amount usually eaten.
    chicken = food_from_amount(id="x", name="Chicken", quantity=145, units="Grams", nutrients={"calories": 290, "protein": 43.5})
    assert chicken.calories == pytest.approx(200) and chicken.protein == pytest.approx(30)
    assert (chicken.default_unit(), chicken.grams(1, chicken.default_unit())) == ("serving (145 g)", 145)

    # Logged by volume: weighed using the food's density.
    honey = food_from_amount(id="x", name="Honey", quantity=1, units="Tablespoon", nutrients={"calories": 64})
    assert honey.grams(1, "tbsp") == pytest.approx(21.0, abs=0.05)
    assert honey.nutrients(honey.grams(2, "tbsp"))["calories"] == pytest.approx(128)

    # Logged in pieces: weight unknown, so it's only measured in that piece.
    bread = food_from_amount(id="x", name="Agege Bread", quantity=2, units="Slices", nutrients={"calories": 140})
    assert bread.portion_only and bread.unit_labels() == ["slice"]
    assert bread.nutrients(bread.grams(3, "slice"))["calories"] == pytest.approx(210)

    assert food_from_amount(id="x", name="Nothing", quantity=0, units="Grams", nutrients={"calories": 10}) is None


def test_singular_units():
    assert [singular(word) for word in ("Slices", "Servings", "Pouches", "Boxes", "Glass", "Each")] == [
        "slice", "serving", "pouch", "box", "glass", "each"]


def test_saving_and_loading():
    original = food("Honey", brand="Billy Bee", portions=[Portion("tbsp", 21)], density=1.42)
    assert Food.from_json(original.to_json()) == original
    assert Food.from_json("not json") is None and Food.from_json(None) is None
    assert original.display_name == "Honey · Billy Bee"
