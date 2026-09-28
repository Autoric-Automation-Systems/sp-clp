"""The PLC block that ships with the panel, offered for download from the help page.

The file lives beside the static folder rather than inside it, so it is not
reachable by guessing a /static path, and so the packaging script can carry it
into the executable. See scripts/build_windows.ps1.
"""

from __future__ import annotations

from pathlib import Path

BUNDLED_DIR = Path(__file__).resolve().parent / "biblioteca"

# TIA Portal exports a global library as a .zal, which is what the customer opens
# under Options > Global libraries. Nothing else is offered.
SUFFIX = ".zal"

DOWNLOAD_URL = "/api/library/download"


def library_file() -> Path | None:
    """Return the block to offer, or None when this build carries none.

    The help page names the file it is about to hand out, so a folder holding more
    than one is not silent about it: the first in alphabetical order wins and its
    name shows next to the button.
    """
    if not BUNDLED_DIR.is_dir():
        return None
    found = sorted(path for path in BUNDLED_DIR.glob(f"*{SUFFIX}") if path.is_file())
    return found[0] if found else None
