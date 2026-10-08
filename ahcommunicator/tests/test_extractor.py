import json

import pytest

from app.clients.extractor import _parse_json, normalize_recipe, parse_page


def test_parse_page_prefers_jsonld_recipe():
    recipe = {"@type": "Recipe", "name": "Pasta", "image": ["/img/pasta.jpg"], "recipeIngredient": ["200 g pasta"]}
    html = f'<html><script type="application/ld+json">{json.dumps({"@graph": [recipe]})}</script><body>noise</body></html>'
    text, image = parse_page(html, "https://example.com/r/pasta")
    assert "200 g pasta" in text and "noise" not in text
    assert image == "https://example.com/img/pasta.jpg"


def test_parse_page_falls_back_to_visible_text():
    html = '<html><meta property="og:image" content="https://x.nl/a.jpg"><nav>menu</nav><body><p>Kook de rijst</p></body></html>'
    text, image = parse_page(html, "https://x.nl/")
    assert "Kook de rijst" in text and "menu" not in text
    assert image == "https://x.nl/a.jpg"


def test_parse_json_strips_fences():
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_normalize_marks_unsearchable_ingredients_as_skipped():
    out = normalize_recipe({
        "name": "Soep",
        "ingredients": [{"text": "1 ui", "search": "ui"}, {"text": "water", "search": ""}, "2 tenen knoflook"],
        "instructions": ["Kook.", " "],
    })
    assert [i["skip"] for i in out["ingredients"]] == [False, True, True]
    assert out["instructions"] == ["Kook."]


def test_normalize_requires_name_and_ingredients():
    with pytest.raises(ValueError):
        normalize_recipe({"name": "", "ingredients": ["x"]})
    with pytest.raises(ValueError):
        normalize_recipe({"name": "x", "ingredients": []})
