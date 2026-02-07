#!/usr/bin/env python3
"""Debug script to test Mealie API and find the cause of 500 errors.

Run inside the container:
  sudo docker exec Mealieah python3 /app/debug_mealie.py
"""
import json
import sys
import uuid

import httpx

BASE_URL = "http://mealie:9000"
HEADERS = {"Content-Type": "application/json"}


def pp(label, data):
    """Pretty-print JSON data with a label."""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    if isinstance(data, (dict, list)):
        print(json.dumps(data, indent=2, default=str)[:3000])
    else:
        print(str(data)[:3000])
    print()


def test_patch(client, slug, label, payload):
    """Test a PATCH and print result."""
    print(f"\n  [{label}]")
    resp = client.patch(f"{BASE_URL}/api/recipes/{slug}", headers=HEADERS, json=payload)
    status = "OK" if resp.status_code < 400 else "FAILED"
    print(f"    PATCH → {resp.status_code} ({status})")
    if resp.status_code >= 400:
        print(f"    Error: {resp.text[:300]}")
    return resp


def main():
    client = httpx.Client(timeout=30)

    # Step 1: Create a test recipe
    print("\n[1] Creating test recipe...")
    resp = client.post(f"{BASE_URL}/api/recipes", headers=HEADERS, json={"name": "Debug Test Recept"})
    print(f"  POST /api/recipes -> {resp.status_code}")
    if resp.status_code >= 400:
        pp("CREATE FAILED", resp.text)
        sys.exit(1)
    slug = resp.json()
    if isinstance(slug, dict):
        slug = slug.get("slug", slug)
    print(f"  Slug: {slug}")

    # Step 2: GET the full recipe
    print("\n[2] Fetching full recipe...")
    resp = client.get(f"{BASE_URL}/api/recipes/{slug}", headers=HEADERS)
    full_recipe = resp.json()
    print(f"  GET -> {resp.status_code}")
    pp("FULL RECIPE", full_recipe)

    # Test A: Minimal PATCH - just name
    test_patch(client, slug, "A: name only", {
        "name": "Debug Test Updated",
    })

    # Test B: Basic text fields
    test_patch(client, slug, "B: name + description + recipeYield + totalTime", {
        "name": "Debug Test Recept",
        "description": "Test beschrijving",
        "recipeYield": "4 porties",
        "totalTime": "30 minuten",
    })

    # Test C: Simple ingredient - just note (OUR NEW FIX)
    test_patch(client, slug, "C: ingredient with note only (our fix)", {
        "name": "Debug Test Recept",
        "recipeIngredient": [
            {
                "referenceId": str(uuid.uuid4()),
                "quantity": 0,
                "unit": None,
                "food": None,
                "note": "200g kipfilet",
                "isFood": False,
                "originalText": "200g kipfilet",
            }
        ],
    })

    # Test D: Minimal note ingredient
    test_patch(client, slug, "D: ingredient with just note field", {
        "name": "Debug Test Recept",
        "recipeIngredient": [
            {"note": "200g kipfilet"},
        ],
    })

    # Test E: OLD format - food object with name only (likely the cause of 500)
    test_patch(client, slug, "E: ingredient with food={name:...} (OLD format)", {
        "name": "Debug Test Recept",
        "recipeIngredient": [
            {
                "referenceId": str(uuid.uuid4()),
                "quantity": 200.0,
                "unit": {"name": "g"},
                "food": {"name": "kipfilet"},
                "note": "",
                "originalText": "200g kipfilet",
                "display": "200g kipfilet",
            }
        ],
    })

    # Test F: null quantity + food object (another old variant)
    test_patch(client, slug, "F: null qty + food={name:...}", {
        "name": "Debug Test Recept",
        "recipeIngredient": [
            {
                "referenceId": str(uuid.uuid4()),
                "quantity": None,
                "unit": None,
                "food": {"name": "peper en zout"},
                "note": "",
                "originalText": "peper en zout",
            }
        ],
    })

    # Test G: Instructions only
    test_patch(client, slug, "G: instructions only", {
        "name": "Debug Test Recept",
        "recipeInstructions": [
            {
                "id": str(uuid.uuid4()),
                "title": "",
                "text": "Verwarm de oven voor op 180 graden.",
                "ingredientReferences": [],
            },
        ],
    })

    # Test H: Full realistic payload with note-based ingredients (our new approach)
    test_patch(client, slug, "H: FULL payload with note-based ingredients (NEW FIX)", {
        "name": "Debug Test Recept",
        "description": "Een heerlijk testgerecht",
        "recipeYield": "3 porties",
        "totalTime": "25 minuten",
        "recipeIngredient": [
            {
                "referenceId": str(uuid.uuid4()),
                "quantity": 0,
                "unit": None,
                "food": None,
                "note": "200g kipfilet",
                "isFood": False,
                "originalText": "200g kipfilet",
            },
            {
                "referenceId": str(uuid.uuid4()),
                "quantity": 0,
                "unit": None,
                "food": None,
                "note": "1 ui, gesnipperd",
                "isFood": False,
                "originalText": "1 ui, gesnipperd",
            },
            {
                "referenceId": str(uuid.uuid4()),
                "quantity": 0,
                "unit": None,
                "food": None,
                "note": "peper en zout",
                "isFood": False,
                "originalText": "peper en zout",
            },
        ],
        "recipeInstructions": [
            {
                "id": str(uuid.uuid4()),
                "title": "",
                "text": "Verwarm de oven voor op 180 graden. Snijd de kipfilet in blokjes.",
                "ingredientReferences": [],
            },
            {
                "id": str(uuid.uuid4()),
                "title": "",
                "text": "Bak de kipfilet goudbruin en voeg de ui toe.",
                "ingredientReferences": [],
            },
        ],
    })

    # Test I: Full payload with food/unit objects (OLD, likely broken)
    test_patch(client, slug, "I: FULL payload with food/unit objects (OLD CODE)", {
        "name": "Debug Test Recept",
        "description": "Een heerlijk testgerecht",
        "recipeYield": "3 porties",
        "totalTime": "25 minuten",
        "recipeIngredient": [
            {
                "referenceId": str(uuid.uuid4()),
                "quantity": 200.0,
                "unit": {"name": "g"},
                "food": {"name": "kipfilet"},
                "note": "",
                "originalText": "200g kipfilet",
                "display": "200g kipfilet",
            },
            {
                "referenceId": str(uuid.uuid4()),
                "quantity": 1.0,
                "unit": None,
                "food": {"name": "ui, gesnipperd"},
                "note": "",
                "originalText": "1 ui, gesnipperd",
                "display": "1 ui, gesnipperd",
            },
        ],
        "recipeInstructions": [
            {
                "id": str(uuid.uuid4()),
                "title": "",
                "text": "Verwarm de oven voor op 180 graden.",
                "ingredientReferences": [],
            },
        ],
    })

    # Cleanup
    print("\n[CLEANUP] Deleting test recipe...")
    resp = client.delete(f"{BASE_URL}/api/recipes/{slug}", headers=HEADERS)
    print(f"  DELETE -> {resp.status_code}")

    print("\n" + "="*60)
    print("  DEBUG COMPLETE")
    print("="*60)
    print("\nSummary: Tests C, D, H use note-based format (our fix).")
    print("Tests E, F, I use food/unit objects (old code, likely broken).")
    print("If C/D/H pass and E/F/I fail, our fix is correct.\n")


if __name__ == "__main__":
    main()
