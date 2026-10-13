"""Turning USDA and Open Food Facts records into foods. No network: these use sample records."""

import pytest

from jim import food_search
from jim.food_search import ASSUMED_SERVING


def test_usda_search_hit():
    food = food_search.usda_food({"fdcId": 168878, "description": "Rice, white, cooked", "foodNutrients": [
        {"nutrientNumber": "208", "value": 130}, {"nutrientNumber": "203", "value": 2.7},
        {"nutrientNumber": "205", "value": 28.2}, {"nutrientNumber": "204", "value": 0.3},
        {"nutrientNumber": "307", "value": 1}]})
    assert (food.id, food.name, food.calories, food.protein, food.carbs, food.sodium) == (
        "usda:168878", "Rice, white, cooked", 130, 2.7, 28.2, 1)
    assert food.portions == [] and food.default_unit() == "g"


def test_usda_full_food_has_portions():
    food = food_search.usda_food({"fdcId": 1, "description": "Jollof rice", "foodNutrients": [
        {"nutrient": {"number": "208"}, "amount": 150}], "foodPortions": [
        {"gramWeight": 200, "portionDescription": "1 cup"},
        {"gramWeight": 100, "portionDescription": "1/2 cup"},                 # the same cup: listed once
        {"gramWeight": 30, "amount": 2, "measureUnit": {"name": "tbsp"}, "modifier": "heaped"},
        {"gramWeight": 150, "portionDescription": "Quantity not specified"},
        {"gramWeight": 0, "portionDescription": "1 slice"}]})
    assert [(p.label, p.grams) for p in food.portions] == [("cup", 200), ("tbsp, heaped", 15)]
    assert food.default_unit() == "cup"


def test_usda_energy_falls_back_to_the_macros():
    food = food_search.usda_food({"fdcId": 2, "description": "Beans", "foodNutrients": [
        {"nutrientNumber": "203", "value": 10}, {"nutrientNumber": "205", "value": 20}, {"nutrientNumber": "204", "value": 2}]})
    assert food.calories == 4 * 10 + 4 * 20 + 9 * 2
    assert food_search.usda_food({"fdcId": 3, "description": "Mystery"}) is None


def test_usda_ranking_prefers_foods_named_by_the_query():
    names = ["Rice crackers", "Rice, white, cooked", "Chicken and rice soup", "Rice"]
    foods = [food_search.usda_food({"fdcId": n, "description": name, "foodNutrients": [{"nutrientNumber": "208", "value": 1}]})
             for n, name in enumerate(names)]
    assert [food.name for food in food_search.usda_rank(foods, "rice")] == [
        "Rice", "Rice, white, cooked", "Rice crackers", "Chicken and rice soup"]


def test_open_food_facts_product():
    food = food_search.off_food({"code": "0064900000102", "product_name": "Liquid Honey", "brands": "Billy Bee, McCormick",
                                 "serving_size": "1 tbsp (21 g)", "serving_quantity": 21, "product_quantity": "500",
                                 "product_quantity_unit": "g",
                                 "nutriments": {"energy-kcal_100g": 304, "carbohydrates_100g": 82, "sodium_100g": 0.004}})
    assert (food.id, food.brand, food.calories, food.sodium) == ("off:0064900000102", "Billy Bee", 304, 4.0)
    assert [(p.label, p.grams) for p in food.portions] == [("serving (1 tbsp (21 g))", 21), ("package (500 g)", 500)]
    assert food.density == pytest.approx(1.42, abs=0.01)        # from the label: 21 g per tablespoon
    assert food_search.has_real_serving(food)


def test_open_food_facts_search_hit_without_a_serving_assumes_100_grams():
    food = food_search.off_food({"code": "123", "product_name": "Chin Chin", "brands": ["Mama's"],      # a list here
                                 "quantity": "2 x 250 g", "nutriments": {"energy_100g": 2092}})
    assert food.brand == "Mama's"
    assert food.calories == pytest.approx(500)                  # kilojoules converted
    assert [(p.label, p.grams) for p in food.portions] == [(ASSUMED_SERVING, 100), ("package (500 g)", 500)]
    assert not food_search.has_real_serving(food)
    assert food.default_unit() == ASSUMED_SERVING


def test_open_food_facts_drinks_are_sold_by_volume():
    food = food_search.off_food({"code": "9", "product_name": "Malt Drink", "serving_size": "330 ml", "serving_quantity": 330,
                                 "serving_quantity_unit": "ml", "nutriments": {"energy-kcal_100g": 60}})
    assert food.sold_by_volume and food.density is None
    assert food.grams(330, "ml") == 330
    assert food_search.off_food({"code": "9", "product_name": "No energy", "nutriments": {}}) is None
    assert food_search.off_food({"product_name": "No code", "nutriments": {"energy-kcal_100g": 1}}) is None


def test_package_sizes_from_label_text():
    size = lambda text: food_search.off_package_grams({"quantity": text})
    assert [size("500 g"), size("1 lb"), size("1,5 L"), size("6 x 330 ml"), size("a dozen")] == [
        500, pytest.approx(453.59, abs=0.01), 1500, 1980, None]
