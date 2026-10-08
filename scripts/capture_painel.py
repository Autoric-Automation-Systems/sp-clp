"""Capture the two screen shots the flyer shows.

A drawing of a screen is not the screen the customer will see, so both images come
from the panel itself, running on a temporary database with a simulated PLC
(``ip = "fake"``): the values look like a real machine and no hardware is needed.

The TV panel is a whole screen, so it is one screenshot. The machine card is a block
inside a larger page: the page is loaded in an iframe that is shifted by the card's
own position and the window is sized to the card, which is what turns a viewport
capture into a crop without an image library.

Run it again whenever the panel's look changes:

    .venv/bin/python scripts/capture_painel.py
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_rede_pdf import chrome_binary  # noqa: E402  (same folder, one browser lookup)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "scripts" / "rede"
DRIVER = ROOT / "app" / "static" / "_capture.html"
PLANT = "Linha 1"
AREA = "Envase"
MACHINE = "Envasadora 1"
PASSWORD = "senha-do-flyer"

TV_SCREEN = (1600, 900)
# A primeira passada mede o card dentro de um iframe do tamanho de uma tela de
# trabalho: numa largura de celular o card sai estreito, que não é o que o cliente vê.
CARD_WINDOW = (1280, 900)
CARD_PADDING = 16

DRIVER_HTML = """<!doctype html>
<meta charset="utf-8">
<body style="margin:0;overflow:hidden;background:#fff">
<script>
const params = new URLSearchParams(location.search);
const pad = Number(params.get('pad') || 0);
const frame = document.createElement('iframe');
frame.style.cssText = 'border:0;position:fixed;left:0;top:0;width:{w}px;height:{h}px';
document.body.appendChild(frame);

// O card cresce quando o status chega, então o mesmo retângulo é lido três
// vezes e o dump fica com a última, já com os dados na tela.
// O seletor olha dentro da página da planta: existe um .machine-card sem layout
// no documento (um modelo), e ele seria medido como 0x0.
function box() {
  const card = frame.contentDocument.querySelector('#plant-page .machine-card');
  if (!card) return null;
  const rect = card.getBoundingClientRect();
  if (rect.width === 0) return null;
  return {x: rect.left, y: rect.top, w: rect.width, h: rect.height};
}

function report(size) {
  let out = document.getElementById('out');
  if (!out) {
    out = document.createElement('pre');
    out.id = 'out';
    document.body.appendChild(out);
  }
  out.textContent = size ? JSON.stringify(size) : 'sem card';
}

frame.onload = function () {
  const given = ['x', 'y', 'w', 'h'].map(function (key) { return Number(params.get(key)); });
  if (given[0] && given[2]) {
    // Segunda passada: posiciona a página para o card começar na origem.
    frame.style.left = (pad - given[0]) + 'px';
    frame.style.top = (pad - given[1]) + 'px';
    frame.style.width = (given[0] + given[2] + 40) + 'px';
    frame.style.height = (given[1] + given[3] + 40) + 'px';
    report(null);
    return;
  }
  // O card cresce quando o status chega, então o mesmo retângulo é lido três
  // vezes e o dump fica com a última, já com os dados na tela.
  report(box());
  setTimeout(function () { report(box()); }, 2000);
  setTimeout(function () { report(box()); }, 5000);
};
frame.src = '/{slug}';
</script>
"""


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def seed(database: Path) -> str:
    """A plant, an area and one machine, and the slug the page answers on."""
    os.environ["SP_CLP_DB"] = str(database)
    from app.slugs import slugify
    from app.storage import Storage

    store = Storage(database)
    area_id = store.add_area(PLANT, AREA)
    store.add_machine(area_id, MACHINE, "fake", 53, "America/Sao_Paulo")
    store.set_setting("password_hash", "")
    return slugify(PLANT)


def wait_for(url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as answer:
                if answer.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.3)
    raise SystemExit(f"o painel não respondeu em {url}")


def chrome(*args: str) -> str:
    result = subprocess.run(
        [chrome_binary(), "--headless=new", "--disable-gpu", "--no-sandbox", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def shoot(url: str, path: Path, size: tuple[int, int]) -> None:
    chrome(
        "--hide-scrollbars",
        f"--window-size={size[0]},{size[1]}",
        "--virtual-time-budget=9000",
        "--run-all-compositor-stages-before-draw",
        f"--screenshot={path}",
        url,
    )


def card_box(base: str) -> dict[str, float]:
    dom = chrome("--virtual-time-budget=9000", "--dump-dom", f"{base}/static/_capture.html")
    match = re.search(r'<pre id="out">(.*?)</pre>', dom, re.S)
    if not match:
        raise SystemExit("o card da máquina não foi encontrado na página")
    return json.loads(match.group(1))


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sp-clp-flyer-") as folder:
        database = Path(folder) / "flyer.sqlite3"
        slug = seed(database)
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=ROOT,
            env={**os.environ, "SP_CLP_DB": str(database)},
        )
        try:
            wait_for(f"{base}/api/machines")
            # The TV panel fetches the machine list and paints its first slide.
            time.sleep(2.5)
            shoot(f"{base}/tvpanel", OUTPUT / "painel-tv.png", TV_SCREEN)

            DRIVER.write_text(
                DRIVER_HTML.replace("{slug}", slug).replace("{w}", str(CARD_WINDOW[0])).replace("{h}", str(CARD_WINDOW[1])),
                encoding="utf-8",
            )
            box = card_box(base)
            print(f"card medido: {round(box['w'])}x{round(box['h'])} em ({round(box['x'])},{round(box['y'])})")
            measured = f"x={round(box['x'])}&y={round(box['y'])}&w={round(box['w'])}&h={round(box['h'])}"
            width = round(box["w"]) + 2 * CARD_PADDING
            height = round(box["h"]) + 2 * CARD_PADDING
            shoot(
                f"{base}/static/_capture.html?{measured}&pad={CARD_PADDING}",
                OUTPUT / "painel-card.png",
                (width, height),
            )
        finally:
            server.terminate()
            server.wait(timeout=10)
            DRIVER.unlink(missing_ok=True)

    for name in ("painel-tv.png", "painel-card.png"):
        path = OUTPUT / name
        print(f"{path.relative_to(ROOT)}: {path.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
