"""Plant names as URL segments.

Each plant answers on its own address, so "Rio Verde" has to become something
safe to put in a path. Slugs keep the original spelling where possible and only
replace what a URL cannot carry, which is why "RioVerde" stays "RioVerde".

Matching is case insensitive so a typed address still resolves.
"""

from __future__ import annotations

import unicodedata

# Menu pages share the same single segment namespace as the plant addresses.
RESERVED_PAGES = {"Ajuda", "Configuracoes", "Dashboard"}


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
