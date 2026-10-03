"""Render the one-page network flyer to ``SP-CLP-rede.pdf`` at the repository root.

The subject is the shape of the network, so the page is drawn once, in millimetres,
in ``scripts/rede-sp-clp.html`` and printed by Chrome, which is the browser this
project already measures with. Two things are injected before printing:

- the glyphs, read from ``app/static/icons.js``, so an icon on paper cannot drift
  from the same icon on the screen;
- the two logos, as data URIs, so the PDF does not depend on where it is opened.

Chrome is the only requirement. Point ``SP_CLP_CHROME`` at the binary when it is
not on PATH under one of the usual names.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "rede-sp-clp.html"
OUTPUT = ROOT / "SP-CLP-rede.pdf"
ICONS = ROOT / "app" / "static" / "icons.js"
# The same mark the panel puts in its header and its tab, at the size that still
# prints sharp: the 1080 px master is three times heavier for no visible gain.
APP_LOGO = ROOT / "app" / "static" / "assets" / "favicon_io" / "android-chrome-512x512.png"
DEV_LOGO = ROOT / "app" / "static" / "assets" / "logo" / "logo-dev.png"

# The names of the browser binaries worth trying, in the order they are tried.
CHROME_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
)


def chrome_binary() -> str:
    named = os.environ.get("SP_CLP_CHROME")
    if named:
        return named
    for name in CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("Chrome não encontrado; aponte SP_CLP_CHROME para o executável")


def icon_bodies() -> dict[str, str]:
    """The inner markup of every vendored icon, keyed by name.

    ``icons.js`` is a generated module, not JSON, so the object is read with a
    pattern anchored at the start of its entries.
    """
    text = ICONS.read_text(encoding="utf-8")
    return {
        name: body.replace('\\"', '"')
        for name, body in re.findall(r'^  "([a-z0-9-]+)": "((?:[^"\\]|\\.)*)",$', text, re.M)
    }


def data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def render(markup: str) -> str:
    """Fill the placeholders and return the printable HTML."""
    icons = icon_bodies()
    wanted = set(re.findall(r'data-icon="([a-z0-9-]+)"', markup))
    missing = sorted(wanted - icons.keys())
    if missing:
        raise SystemExit(f"ícones que não existem em icons.js: {', '.join(missing)}")

    def glyph(match: re.Match[str]) -> str:
        return (
            f'<svg class="icon" data-icon="{match.group(1)}" viewBox="0 0 24 24" '
            'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round">{icons[match.group(1)]}</svg>'
        )

    markup = re.sub(r'<svg class="icon" data-icon="([a-z0-9-]+)" viewBox="0 0 24 24"></svg>', glyph, markup)
    markup = markup.replace("{{APP_LOGO}}", data_uri(APP_LOGO))
    markup = markup.replace("{{DEV_LOGO}}", data_uri(DEV_LOGO))
    return markup


def page_count(pdf: bytes) -> int:
    """How many pages the print produced, read from the page tree.

    The flyer is meant to be one page, and a line of text that wraps into a second
    one is invisible in the source, so the count is checked rather than assumed.
    """
    counts = re.findall(rb"/Type\s*/Pages[^>]*?/Count\s+(\d+)", pdf, re.S)
    if counts:
        return int(counts[0])
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf))


def main() -> int:
    markup = render(SOURCE.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="sp-clp-rede-") as folder:
        page = Path(folder) / "rede.html"
        page.write_text(markup, encoding="utf-8")
        subprocess.run(
            [
                chrome_binary(),
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--no-pdf-header-footer",
                f"--print-to-pdf={OUTPUT}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
        )

    pdf = OUTPUT.read_bytes()
    pages = page_count(pdf)
    print(f"{OUTPUT.name}: {pages} página(s), {len(pdf) / 1024:.0f} KB")
    if pages != 1:
        raise SystemExit("a folha deixou de caber em uma página")
    return 0


if __name__ == "__main__":
    sys.exit(main())
