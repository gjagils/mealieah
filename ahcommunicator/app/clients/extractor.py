"""Turn a recipe URL, pasted text or photos into a structured recipe with Claude."""
import base64
import io
import ipaddress
import json
import socket
from urllib.parse import urljoin, urlparse

import anthropic
import httpx
from bs4 import BeautifulSoup
from PIL import Image, ImageOps

from app.config import settings
from app.logging_config import logger

MAX_IMAGE_BYTES = 4_800_000  # Claude API limit is 5MB, keep margin
MAX_TEXT_CHARS = 30_000

SYSTEM_PROMPT = """Je bent een expert in het lezen van recepten uit websites, tekst en foto's.
Zet het recept om naar gestructureerd JSON. Antwoord ALLEEN met valid JSON, geen tekst eromheen:
{
    "name": "Naam van het recept",
    "description": "Korte beschrijving (1-2 zinnen)",
    "recipe_yield": "4 personen",
    "total_time": "30 minuten",
    "ingredients": [
        {"text": "200 g kipfilet", "search": "kipfilet"},
        {"text": "1 ui, gesnipperd", "search": "ui"}
    ],
    "instructions": ["Verwarm de oven voor op 180 graden. Snijd de kip in blokjes.", "..."],
    "food_photo_index": null
}

Regels:
- "text": het ingrediënt zoals in het recept, met hoeveelheid en eenheid.
- "search": korte Nederlandse zoekterm voor een supermarkt (Albert Heijn): alleen het product,
  zonder hoeveelheid, bereidingswijze of bijvoeglijke woorden zoals "gesnipperd" of "vers".
  Gebruik "" voor dingen die je niet koopt (water, "zout en peper naar smaak").
- Bij meerdere foto's van hetzelfde recept (voor- en achterkant): combineer ze. Gebruik voor het
  aantal porties het getal bij de gedetailleerde ingrediëntenlijst.
- Behoud de originele stap-indeling; maak niet van elke zin een aparte stap.
- Houd de taal van het origineel aan (meestal Nederlands). Als iets onleesbaar is: beste gok.
- Bij meerdere recepten: alleen het meest prominente.
- "food_photo_index": 0-gebaseerd nummer van de foto die het eindresultaat toont, anders null.
"""


def _resize_for_api(image_data: bytes, media_type: str) -> tuple[bytes, str]:
    if len(image_data) <= MAX_IMAGE_BYTES:
        return image_data, media_type
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_data)))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    quality, max_dim = 85, 2048
    while True:
        resized = img.copy()
        resized.thumbnail((max_dim, max_dim), Image.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format="JPEG", quality=quality)
        if len(buf.getvalue()) <= MAX_IMAGE_BYTES:
            return buf.getvalue(), "image/jpeg"
        if quality > 50:
            quality -= 10
        else:
            max_dim = int(max_dim * 0.75)


def _check_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Ongeldige URL (alleen http/https).")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        raise ValueError("Website niet gevonden.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("Die URL is niet toegestaan.")


def _find_recipe_jsonld(soup: BeautifulSoup) -> dict | None:
    def walk(node):
        if isinstance(node, list):
            for item in node:
                found = walk(item)
                if found:
                    return found
        elif isinstance(node, dict):
            node_type = node.get("@type")
            types = node_type if isinstance(node_type, list) else [node_type]
            if "Recipe" in types:
                return node
            return walk(node.get("@graph", []))
        return None

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            found = walk(json.loads(tag.string or ""))
        except (ValueError, TypeError):
            continue
        if found:
            return found
    return None


def parse_page(html: str, base_url: str) -> tuple[str, str]:
    """Return (text for Claude, image url). Prefers schema.org Recipe data."""
    soup = BeautifulSoup(html, "html.parser")
    image_url = ""
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        image_url = urljoin(base_url, og["content"])

    recipe = _find_recipe_jsonld(soup)
    if recipe:
        img = recipe.get("image")
        if isinstance(img, list) and img:
            img = img[0]
        if isinstance(img, dict):
            img = img.get("url")
        if isinstance(img, str) and img:
            image_url = urljoin(base_url, img)
        return json.dumps(recipe, ensure_ascii=False)[:MAX_TEXT_CHARS], image_url

    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
        tag.decompose()
    text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    return text[:MAX_TEXT_CHARS], image_url


async def fetch_url(url: str) -> tuple[str, str]:
    _check_public_url(url)
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        resp = await client.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AHRecepten/0.1)", "Accept-Language": "nl,en;q=0.8"},
        )
        resp.raise_for_status()
        _check_public_url(str(resp.url))
    return parse_page(resp.text, str(resp.url))


def _parse_json(response_text: str) -> dict:
    start, end = response_text.find("{"), response_text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Claude gaf geen geldig recept terug.")
    return json.loads(response_text[start : end + 1])


def normalize_recipe(raw: dict) -> dict:
    ingredients = []
    for ing in raw.get("ingredients", []):
        if isinstance(ing, str):
            ing = {"text": ing, "search": ""}
        text = str(ing.get("text", "")).strip()
        if text:
            search = str(ing.get("search", "")).strip()
            ingredients.append({"text": text, "search": search, "skip": not search, "quantity": 1, "product": None})
    recipe = {
        "name": str(raw.get("name", "")).strip(),
        "description": str(raw.get("description") or "").strip(),
        "servings": str(raw.get("recipe_yield") or "").strip(),
        "total_time": str(raw.get("total_time") or "").strip(),
        "ingredients": ingredients,
        "instructions": [str(s).strip() for s in raw.get("instructions", []) if str(s).strip()],
        "food_photo_index": raw.get("food_photo_index"),
    }
    if not recipe["name"]:
        raise ValueError("Geen receptnaam gevonden.")
    if not recipe["ingredients"]:
        raise ValueError("Geen ingrediënten gevonden.")
    return recipe


async def extract_recipe(
    text: str | None = None, images: list[tuple[bytes, str]] | None = None
) -> dict:
    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY is niet ingesteld.")
    content: list[dict] = []
    for image_data, media_type in images or []:
        image_data, media_type = _resize_for_api(image_data, media_type)
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type,
                       "data": base64.b64encode(image_data).decode()},
        })
    if text:
        content.append({"type": "text", "text": f"Recept:\n\n{text}"})
    if images:
        n = len(images)
        content.append({"type": "text", "text": "Lees dit recept en geef het terug als JSON."
                        if n == 1 else f"Dit zijn {n} foto's van hetzelfde recept. Combineer ze en geef JSON."})
    if not content:
        raise ValueError("Geef een URL, tekst of foto's op.")

    logger.info("Extracting recipe (%d image(s), text=%s)", len(images or []), bool(text))
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    message = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    return normalize_recipe(_parse_json(message.content[0].text))


GF_PROMPT = """Je helpt een gezin in Nederland dat elke maaltijd glutenvrij wil kunnen eten voor minstens 1 persoon.
Je krijgt een recept met genummerde ingrediënten. Beoordeel welke ingrediënten gluten bevatten
(tarwe, rogge, gerst, spelt, couscous, pasta, brood, wraps, bloem, paneermeel, sojasaus, bouillonblokjes, enz.).
Antwoord ALLEEN met valid JSON:
{
  "mode": "extra" of "replace",
  "note": "1-2 zinnen uitleg, bijv. 'Maak voor 1 persoon aparte glutenvrije pasta.'",
  "ingredients": [{"i": 0, "gluten": true, "gf_search": "glutenvrije wraps"}]
}
Regels:
- Neem alleen ingrediënten op die gluten bevatten.
- "gf_search": korte Nederlandse zoekterm voor een glutenvrij alternatief bij Albert Heijn.
- "extra": het glutenvrije product wordt er voor 1 persoon bij gekocht (typisch voor pasta, wraps, brood).
- "replace": het ingrediënt wordt voor iedereen vervangen (typisch voor sojasaus -> tamari, bloem, bouillon),
  omdat dat makkelijk is en niemand het merkt. Kies "replace" alleen als ALLE gluten-ingrediënten zo vervangbaar zijn.
- Geen ingrediënt met gluten: geef een lege "ingredients" lijst.
"""


def normalize_gf(raw: dict, count: int) -> dict:
    mode = raw.get("mode") if raw.get("mode") in ("extra", "replace") else "extra"
    items = {}
    for item in raw.get("ingredients", []):
        i = item.get("i")
        if isinstance(i, int) and 0 <= i < count and item.get("gluten", True):
            items[i] = str(item.get("gf_search", "")).strip()
    return {"mode": mode, "note": str(raw.get("note", "")).strip(), "items": items}


async def suggest_gluten_free(name: str, ingredient_texts: list[str]) -> dict:
    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY is niet ingesteld.")
    listing = "\n".join(f"{i}: {t}" for i, t in enumerate(ingredient_texts))
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    message = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=2000,
        system=GF_PROMPT,
        messages=[{"role": "user", "content": f"Recept: {name}\n\nIngrediënten:\n{listing}"}],
    )
    return normalize_gf(_parse_json(message.content[0].text), len(ingredient_texts))
