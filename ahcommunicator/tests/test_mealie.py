from app.clients.mealie import convert_recipe, search_term


def test_search_term_prefers_food_name():
    assert search_term("250 g kipfilet, in blokjes", "kipfilet") == "kipfilet"


def test_search_term_strips_quantity_and_unit():
    assert search_term("2 el olijfolie") == "olijfolie"
    assert search_term("1 ui, gesnipperd") == "ui"
    assert search_term("200g bloem") == "bloem"


def test_convert_recipe_maps_fields_and_skips_empty():
    full = {
        "name": "Pasta", "recipeYield": "4 porties", "totalTime": "30 minuten",
        "recipeIngredient": [
            {"display": "250 g pasta", "food": {"name": "pasta"}},
            {"quantity": 2, "unit": {"name": "el"}, "food": {"name": "olie"}, "note": ""},
            {"display": "", "food": None},
        ],
        "recipeInstructions": [{"text": "Kook de pasta."}, {"text": " "}],
    }
    out = convert_recipe(full)
    assert [i["text"] for i in out["ingredients"]] == ["250 g pasta", "2 el olie"]
    assert out["ingredients"][0]["search"] == "pasta" and out["ingredients"][0]["skip"] is False
    assert out["instructions"] == ["Kook de pasta."]
    assert out["servings"] == "4 porties"
