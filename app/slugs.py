"""Plant names as URL segments.

Each plant answers on its own address, so "Rio Verde" has to become something
safe to put in a path. Slugs keep the original spelling where possible and only
replace what a URL cannot carry, which is why "RioVerde" stays "RioVerde".

Matching is case insensitive so a typed address still resolves.
"""

from __future__ import annotations

import unicodedata

# Menu pages share the same single segment namespace as the plant addresses.
RESERVED_PAGES = {"Ajuda", "Configuracoes", "Dashboard", "TVPanel"}


def slugify(name: str) -> str:
    """Return the URL segment for ``name``; empty when nothing usable is left."""
    decomposed = unicodedata.normalize("NFKD", name.strip())
    folded = "".join(char for char in decomposed if not unicodedata.combining(char))
    cleaned = "".join(char if char.isalnum() else "-" for char in folded)
    return "-".join(part for part in cleaned.split("-") if part)


def plant_slugs(names: list[str] | set[str]) -> dict[str, str]:
    """Map a folded slug to the plant name, skipping reserved and empty ones."""
    mapping: dict[str, str] = {}
    for name in names:
        slug = slugify(name)
        if not slug or slug.casefold() in {page.casefold() for page in RESERVED_PAGES}:
            continue
        mapping[slug.casefold()] = name
    return mapping


def plant_name_error(plant_name: str, taken: set[str]) -> str | None:
    """Explain why ``plant_name`` cannot be used, or return None when it can.

    Two plants that reduce to the same address would fight over it, so the slug
    has to be unique, present, and outside the menu namespace.
    """
    slug = slugify(plant_name)
    if not slug:
        return "O nome da planta precisa de pelo menos uma letra ou número para virar endereço"
    if slug.casefold() in {page.casefold() for page in RESERVED_PAGES}:
        return f"/{slug} é um endereço reservado do sistema; escolha outro nome de planta"
    for other in sorted(taken):
        if other != plant_name and slugify(other).casefold() == slug.casefold():
            return f"O endereço /{slug} já pertence à planta {other}"
    return None
