"""Company name and logo shown in the dashboard header.

The client supplies both from the settings page, so the values are stored in the
``settings`` table and the image sits next to the database file. Images are
identified by their signature instead of the declared filename: an SVG would run
its scripts in the dashboard origin, so it is deliberately not accepted.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path

COMPANY_NAME_KEY = "company_name"
LOGO_EXT_KEY = "logo_ext"
DEFAULT_COMPANY_NAME = "SP-CLP"
DEFAULT_LOGO = "/static/assets/favicon_io/android-chrome-192x192.png"
MAX_LOGO_BYTES = 1_000_000

# Magic bytes to extension, in the order they are tried.
SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)

MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


def detect_image(data: bytes) -> str | None:
    """Return the extension implied by the file signature, or None."""
    for signature, extension in SIGNATURES:
        if data.startswith(signature):
            return extension
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def decode_logo(content: str) -> tuple[bytes, str]:
    """Decode an uploaded logo, raising ValueError with a readable message."""
    try:
        data = base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("O arquivo não pôde ser lido; envie uma imagem PNG, JPEG, GIF ou WEBP") from None
    if not data:
        raise ValueError("O arquivo está vazio")
    if len(data) > MAX_LOGO_BYTES:
        raise ValueError(
            f"A imagem tem {len(data) // 1024} KB; o limite é {MAX_LOGO_BYTES // 1024} KB"
        )
    extension = detect_image(data)
    if extension is None:
        raise ValueError("Formato não reconhecido; use PNG, JPEG, GIF ou WEBP")
    return data, extension


def logo_path(directory: Path, extension: str) -> Path:
    return directory / f"logo.{extension}"


def remove_logos(directory: Path) -> None:
    """Delete every stored logo, so only one file can ever be served."""
    for existing in directory.glob("logo.*"):
        if existing.is_file():
            existing.unlink()
