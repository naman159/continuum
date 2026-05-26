from __future__ import annotations

GENRE_PRESETS: dict[str, list[dict[str, str]]] = {
    "litrpg": [
        {
            "name": "realm",
            "description": "A distinct universe, dimension, or world that characters inhabit or travel to.",
        },
        {
            "name": "power_system",
            "description": "A named system of abilities, cultivation, or magic with defined rules and tiers.",
        },
        {
            "name": "species",
            "description": "A distinct race, creature type, or non-human species with collective traits.",
        },
    ],
    "high_fantasy": [
        {
            "name": "deity",
            "description": "A god, divine being, or supernatural entity that characters worship or interact with.",
        },
        {
            "name": "species",
            "description": "A distinct race or non-human species (elves, dwarves, orcs, etc.).",
        },
        {
            "name": "magic_system",
            "description": "A named, rule-governed system of magic with distinct schools or limitations.",
        },
    ],
    "xianxia": [
        {
            "name": "realm",
            "description": "A cultivation realm, spiritual plane, or distinct world with its own power hierarchy.",
        },
        {
            "name": "cultivation_technique",
            "description": "A named cultivation method, martial art, or technique with narrative significance.",
        },
        {
            "name": "species",
            "description": "A distinct cultivator race, demon species, or supernatural being type.",
        },
    ],
    "scifi": [
        {
            "name": "species",
            "description": "An alien race or distinct non-human sapient species.",
        },
        {
            "name": "technology",
            "description": "A named technology, invention, or system with narrative significance beyond setting dressing.",
        },
    ],
    "contemporary": [
        {
            "name": "institution",
            "description": "A named institution, corporation, or organisation larger than a faction with structural importance.",
        },
    ],
}


def list_genres() -> list[dict]:
    """Return genre presets as a list of {id, label, types} for the API."""
    labels = {
        "litrpg": "LitRPG",
        "high_fantasy": "High Fantasy",
        "xianxia": "Xianxia",
        "scifi": "Sci-Fi",
        "contemporary": "Contemporary",
    }
    return [
        {"id": key, "label": labels[key], "types": types}
        for key, types in GENRE_PRESETS.items()
    ]
