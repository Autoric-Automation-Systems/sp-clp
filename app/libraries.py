"""The PLC blocks that ship with the panel and are handed out on the help page.

They live beside the static folder rather than inside it, so they are not reachable
by guessing a URL, and scripts/build_windows.ps1 carries them into the executable.
One folder per PLC family, because an installation may serve more than one brand
over time:

    app/library/s7/FB_SP-CLP.zal17
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

BUNDLED_DIR = Path(__file__).resolve().parent / "library"

# Friendly names for the family folders. Anything not listed shows its own folder
# name, so adding a brand is dropping a folder in, not editing this file.
FAMILY_LABELS = {"s7": "Siemens S7"}


@dataclass(frozen=True)
class LibraryFile:
    family: str
    label: str
    path: Path

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def size_bytes(self) -> int:
        return self.path.stat().st_size

    @property
    def url(self) -> str:
        return f"/api/library/{quote(self.family)}/{quote(self.filename)}"


def available_files() -> list[LibraryFile]:
    """Every file on offer, families and names in alphabetical order.

    Anything in a family folder counts, whatever the extension: the PLC tools name
    their exports after their own version, and TIA Portal alone writes .zal for
    older releases and .zal17 for V17.
    """
    if not BUNDLED_DIR.is_dir():
        return []
    found: list[LibraryFile] = []
    for folder in sorted(path for path in BUNDLED_DIR.iterdir() if path.is_dir()):
        label = FAMILY_LABELS.get(folder.name, folder.name)
        for path in sorted(folder.iterdir()):
            # A dotfile is how an empty folder survives a checkout; it is not a
            # library, and neither is anything else an operator hides by name.
            if path.is_file() and not path.name.startswith("."):
                found.append(LibraryFile(family=folder.name, label=label, path=path))
    return found


def lookup(family: str, filename: str) -> LibraryFile | None:
    """Find one offered file by exact match against the scanned folders.

    The requested strings are compared, never joined into a path, so a name like
    ../../etc/passwd matches nothing instead of escaping the folder.
    """
    for item in available_files():
        if item.family == family and item.filename == filename:
            return item
    return None

