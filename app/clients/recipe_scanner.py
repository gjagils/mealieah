import base64
import json

import anthropic

from app.config import settings
from app.logging_config import logger

SYSTEM_PROMPT = """Je bent een expert in het lezen van recepten uit foto's.
Analyseer de foto en extraheer het recept in een gestructureerd JSON formaat.

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
        "Verwarm de oven voor op 180 graden.",
        "Snijd de kipfilet in blokjes.",
        "Bak de ui glazig in een pan."
    ]
}

Regels:
- Schrijf ingrediënten zoals ze in het recept staan (met hoeveelheid en eenheid)
- Schrijf elke bereidingsstap als een aparte zin
- Als iets niet leesbaar is, doe je beste gok op basis van context
- Houd de taal van het originele recept aan (meestal Nederlands)
- Als je meerdere recepten ziet, neem alleen het meest prominente recept
"""


async def scan_recipe_image(image_data: bytes, media_type: str) -> dict:
    """Send a recipe photo to Claude Vision and get structured recipe data."""
    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY is niet ingesteld. Ga naar Instellingen.")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    image_b64 = base64.b64encode(image_data).decode("utf-8")

    logger.info("Sending recipe image to Claude Vision (%d bytes, %s)", len(image_data), media_type)

    message = await client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Lees dit recept en geef het terug als JSON.",
                    },
                ],
            }
        ],
    )

    response_text = message.content[0].text.strip()
    logger.debug("Claude Vision response: %s", response_text[:200])

    # Strip markdown code fences if present
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        # Remove first line (```json) and last line (```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        response_text = "\n".join(lines)

    recipe = json.loads(response_text)

    # Validate required fields
    if not recipe.get("name"):
        raise ValueError("Claude kon geen receptnaam vinden in de foto.")
    if not recipe.get("ingredients"):
        raise ValueError("Claude kon geen ingrediënten vinden in de foto.")

    return recipe
