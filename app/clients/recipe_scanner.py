import base64
import io
import json

import anthropic
from PIL import Image, ImageOps

from app.config import settings
from app.logging_config import logger

MAX_IMAGE_BYTES = 4_800_000  # Claude API limit is 5MB, keep margin


def _resize_for_api(image_data: bytes, media_type: str) -> tuple[bytes, str]:
    """Resize and compress image to fit within Claude's 5MB limit."""
    if len(image_data) <= MAX_IMAGE_BYTES:
        return image_data, media_type

    img = Image.open(io.BytesIO(image_data))
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # Progressively reduce size until under limit
    quality = 85
    max_dim = 2048
    while True:
        img_resized = img.copy()
        img_resized.thumbnail((max_dim, max_dim), Image.LANCZOS)
        buf = io.BytesIO()
        img_resized.save(buf, format="JPEG", quality=quality)
        result = buf.getvalue()
        if len(result) <= MAX_IMAGE_BYTES:
            logger.info("Resized image: %d -> %d bytes (max_dim=%d, q=%d)",
                        len(image_data), len(result), max_dim, quality)
            return result, "image/jpeg"
        if quality > 50:
            quality -= 10
        else:
            max_dim = int(max_dim * 0.75)

SYSTEM_PROMPT = """Je bent een expert in het lezen van recepten uit foto's.
Analyseer alle foto's en extraheer het recept in een gestructureerd JSON formaat.

Het kunnen meerdere foto's zijn van hetzelfde recept (bijv. voorkant en achterkant
van een receptenkaart, of een kookboekpagina). Combineer alle informatie uit alle
foto's tot één compleet recept.

Antwoord ALLEEN met valid JSON, geen tekst eromheen. Gebruik dit formaat:
{
    "name": "Naam van het recept",
    "description": "Korte beschrijving (1-2 zinnen)",
    "recipe_yield": "4 porties",
    "total_time": "30 minuten",
    "ingredients": [
        "200g kipfilet",
        "1 ui, gesnipperd",
        "2 teentjes knoflook"
    ],
    "instructions": [
        "Verwarm de oven voor op 180 graden. Snijd de kipfilet in blokjes en kruid met peper en zout.",
        "Bak de kipfilet in een pan met olie. Voeg de ui toe en bak kort mee.",
        "Meng de rucola met munt, citroensap en olijfolie. Verdeel over de borden."
    ],
    "food_photo_index": 0
}

Regels:
- Schrijf ingrediënten zoals ze in het recept staan (met hoeveelheid en eenheid)
- BELANGRIJK voor bereidingsstappen: behoud de originele stap-indeling van het recept.
  Als het recept genummerde stappen heeft (1, 2, 3...) of stappen met titels, gebruik
  dan dezelfde stappen. Eén stap kan meerdere zinnen bevatten. Maak NIET van elke zin
  een aparte stap. Voorbeeld: als stap 1 drie zinnen bevat, dan is dat één item in de
  instructions lijst met alle drie de zinnen.
- Als iets niet leesbaar is, doe je beste gok op basis van context
- Houd de taal van het originele recept aan (meestal Nederlands)
- Als je meerdere recepten ziet, neem alleen het meest prominente recept
- "food_photo_index": het 0-gebaseerde nummer van de foto die het eindresultaat
  (het gerecht op een bord) het best toont. Als er geen foto van het gerecht is,
  gebruik dan null.
"""


async def scan_recipe_images(images: list[tuple[bytes, str]]) -> dict:
    """Send one or more recipe photos to Claude Vision and get structured recipe data.

    Args:
        images: List of (image_data, media_type) tuples
    """
    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY is niet ingesteld. Ga naar Instellingen.")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    # Build content blocks: all images (resized if needed) + one text prompt
    content = []
    for i, (image_data, media_type) in enumerate(images):
        image_data, media_type = _resize_for_api(image_data, media_type)
        image_b64 = base64.b64encode(image_data).decode("utf-8")
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": image_b64,
            },
        })
        logger.info("Image %d: %d bytes, %s", i + 1, len(image_data), media_type)

    prompt = "Lees dit recept en geef het terug als JSON."
    if len(images) > 1:
        prompt = f"Dit zijn {len(images)} foto's van hetzelfde recept. Combineer alle informatie en geef het terug als JSON."

    content.append({"type": "text", "text": prompt})

    logger.info("Sending %d recipe image(s) to Claude Vision", len(images))

    message = await client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )

    response_text = message.content[0].text.strip()
    logger.debug("Claude Vision response: %s", response_text[:200])

    # Strip markdown code fences if present
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        response_text = "\n".join(lines)

    recipe = json.loads(response_text)

    # Validate required fields
    if not recipe.get("name"):
        raise ValueError("Claude kon geen receptnaam vinden in de foto.")
    if not recipe.get("ingredients"):
        raise ValueError("Claude kon geen ingrediënten vinden in de foto.")

    return recipe
