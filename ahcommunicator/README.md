# AH Recepten

Zet een recept (website-link, geplakte tekst of foto's) om naar een gestructureerd recept,
koppel de ingrediënten aan Albert Heijn-producten en zet ze met één klik op je AH-boodschappenlijstje.
Plan daarnaast een weekmenu en doe de boodschappen voor de hele week in één keer.

Losstaande variant van `mealieah`: geen Mealie en geen Postgres nodig (SQLite). Bestaande recepten haal je in één keer uit Mealie via Instellingen.

## Starten

```
ANTHROPIC_API_KEY=sk-ant-... docker compose up --build
```

Open http://localhost:9927, ga naar **Instellingen** en koppel je AH-account.

## Let op

- Het recept wordt **niet** in je AH-/Allerhande-account opgeslagen: daar is geen (bekende) API voor.
  De recepten staan in deze app; de boodschappen gaan wel naar je AH-lijstje.
- De AH-koppeling gebruikt de onofficiële app-API en kan zonder waarschuwing veranderen.
