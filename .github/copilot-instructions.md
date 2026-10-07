# SP-CLP: how work is done in this repository

Conventions and decisions accumulated while building the panel. Read this before
editing: it is the starting point of the reasoning, so a new session continues from
here instead of rediscovering the project.

## The product, in one paragraph

A local, read-only monitoring panel for Siemens S7-1200 PLCs, installed on a
Windows machine inside the plant. `app/main.py` is a FastAPI application served by
uvicorn; the browser opens the dashboard and the customer bookmarks it. The
hierarchy `Planta` -> `Area` -> `Maquina` is configured behind one password. History
lives in SQLite at `data/sp-clp.sqlite3`, next to the executable, and `SP_CLP_DB`
points it elsewhere. Python 3.11+, dependencies `fastapi`, `uvicorn[standard]`,
`pydantic`, `python-snap7`, `tzdata`; build extra `pyinstaller`.

## Sources of truth

- **`README.md`** - what the product does and why, section by section, in
  Portuguese, for the customer. It is the contract: when behaviour changes, the
  matching section changes in the same commit.
- **`.github/agents/s7-dashboard-engineer.agent.md`** - product scope, engineering
  rules and boundaries. The user owns this file: edit it when the user asks, and
  commit it only when the user asks. Its PLC contract has to stay identical to the
  one implemented below.
- **`tests/test_core.py`** - the behaviour that must not regress. 163 tests, none
  of them needs hardware.

## Where each concept lives

| Concept | Code | README section |
| --- | --- | --- |
| PLC protocol, parser, simulator, Snap7 adapter | `app/plc.py` | Contrato do CLP |
| Sweeping a PLC for the DB by its signature | `app/plc.py`, `POST /api/config/plc/scan` | Varredura do CLP |
| SCL library per PLC family | `app/libraries.py`, `app/library/<family>/` | Biblioteca do CLP |
| Registry of plants, areas and machines | `app/models.py`, `app/storage.py` | Enderecos |
| Password, sessions, local recovery | `app/security.py`, `app/recovery.py`, `app/main.py` | Acesso e senha |
| Company name and logo | `app/branding.py` | Identidade do cliente |
| Hourly counts in the machine timezone | `app/storage.py`, `app/timezones.py` | Contagens por hora |
| 24/7 recorder, retention | `app/recorder.py` | Historico que o painel grava |
| A signal's day, from recorded events | `app/signals.py` | Tendencia dos sinais |
| Access addresses, bind, `sp-clp` alias | `app/access.py`, `app/slugs.py` | Infraestrutura de rede |
| Dashboard, help page, TV panel | `app/static/` | Modo TV |
| Lucide icons, generated offline | `scripts/build_icons.py` -> `app/static/icons.js` | Estrutura |
| Windows executable | `scripts/build_windows.ps1` | Gerar o executavel Windows |
| Network flyer for the customer | `scripts/rede-sp-clp.html` -> `SP-CLP-rede.pdf` | Estrutura |

## The PLC contract, as implemented

Measured from `app/plc.py`; every value below is read from the customer's block.

- Rack `0`, slot `1` (`DEFAULT_RACK` / `DEFAULT_SLOT`). Not a per-machine setting.
- One read of `READ_SIZE = 24` bytes brings state, integers, signature and counter.
- BOOLs fill `0.0` through `1.7`. The five standard signals come first and cannot be
  renamed or retyped: `AUTO` `0.0`, `RUN` `0.1`, `FAULT` `0.2`, `SAFETY` `0.3`,
  `COUNTER` `0.4`.
- `AUTO` 1 = automatic; `RUN` 1 = producing; `FAULT` 1 = **in fault** (inverted);
  `SAFETY` 1 = normal; `COUNTER` 1 = counting now, a rising-edge diagnostic, not a
  quantity.
- `Valor 1`, `Valor 2`, `Valor 3`: big-endian `DInt` at `2.0`, `6.0`, `10.0`.
  Renamable labels; they never enter the hourly totals.
- Signature `ARRAY[0..4] OF CHAR = 'SPCLP'` at `14.0`, used by the scan.
- `Count`: big-endian `DInt` at `20.0` (`COUNT_OFFSET`). Byte `19` is alignment.
- The big number on the card is `Count`, not the `COUNTER` bit.
- The block must be compiled with **optimised access off**; the panel reads
  absolute addresses, which do not exist in an optimised block. The shipped SCL
  source already declares `S7_Optimized_Access := 'FALSE'`.

**Note.** The specification this project was started from placed `COUNTER` at
`DBX2.0` and `Count` at byte offset `4.0`, and it read `FAULT` the other way round.
All three were corrected to match this contract, which is the customer's block. If
a document, a screenshot or a customer answer says something else, that is a
question for the customer, not a change to make on the spot.

## Rules that must not be broken

- **Never guess** a PLC address, type, byte order, rack/slot or counter policy that
  is not in the contract above. Surface it as a question or as configuration.
- **Read-only.** No code path writes to a PLC. The scan opens its own connection,
  uses it and closes it, so it never fights the polling loop for the session.
- **The dashboard never talks to the PLC.** `app/plc.py` is the boundary; the
  simulator (`ip = "fake"`) makes every flow testable without hardware.
- Never log a password, a token or PLC data. Never return them in an API error.
- Every configuration endpoint requires admin; read endpoints do not, because
  monitoring is open and only configuring is protected.
- Passwords only ever exist as a PBKDF2-SHA256 hash. There is no HTTP password
  reset, on purpose - the panel listens on `0.0.0.0`, so that route would be
  remote access. The only way back is `--reset-password`, locally, with `getpass`.
- A `Count` that decreases starts a new baseline. It must never produce negative
  production.
- Hourly totals close in the timezone configured for that machine.
- Retention is `RETENTION_DAYS = 7` and it only prunes history. The registry,
  labels, password and branding survive every cleanup.
- Route slugs are reserved for `Ajuda`, `Configuracoes`, `Dashboard` and
  `tvpanel`; a plant may not take one.
- **Do not claim the live PLC integration works.** No test on customer equipment
  has happened; the simulator and the unit tests are all that exist.

## Conventions

- **Code, comments, tests and commits are in English.** Everything the customer
  reads - README, help page, UI strings, TV panel, commit-free prose - is in
  Portuguese with accents.
- Comments are ASCII and only explain a decision that is not self-evident. A
  comment repeats the *why*, never the *what*.
- The stylesheet carries one `[hidden]{display:none!important}` rule, because any
  rule that sets `display` beats the attribute and leaves a hidden element on
  screen. Nothing else needs a selector of its own.
- Commit messages: imperative English subject that says what changed, then a body
  that explains why the decision was taken and what was rejected. One concern per
  commit.
- Never commit `data/`, `dist/`, `build/`, `*.spec`, `.venv/`. The mode file
  `.github/agents/*.agent.md` belongs to the user: commit it only when the user asks.
- Keep the module boundaries above. A change that needs a new module needs a
  reason, not a new folder.

## Validation protocol

Run all of this before saying a change works.

```bash
.venv/bin/python -m pytest          # 163 tests, offline
node --check app/static/app.js      # after editing the panel script
```

There is **no linter and no type checker configured** in `pyproject.toml`. Do not
claim one ran. Typing style is `from __future__ import annotations` plus typed
signatures and Pydantic models.

**Write the test first and watch it fail.** For a decision that would be silently
wrong (a colour, a boundary, an authorisation), mutate the code, watch the test
fail, restore the file. A test that cannot fail proves nothing.

**Layout, colour and state changes are proved in a real browser**, never by eye and
never from a description of a screenshot.

1. Serve the app once - kill the previous server first:

   ```bash
   SP_CLP_DB=/tmp/sp-clp-check.sqlite3 .venv/bin/python -m uvicorn app.main:app \
     --host 127.0.0.1 --port 8000 --log-level warning
   ```

2. Put a throwaway driver in `app/static/_check.html`. It loads the app in an
   `iframe`, reads `getBoundingClientRect()` and `getComputedStyle()`, clicks
   controls, and writes the results into `<pre id="out">`. The iframe is what lets
   one page measure several viewport sizes and drive several routes.

3. Read the result. `sed` and `grep` cannot help: the block is multi-line.

   ```bash
   google-chrome --headless=new --disable-gpu --no-sandbox \
     --virtual-time-budget=45000 --run-all-compositor-stages-before-draw \
     --dump-dom http://127.0.0.1:8000/static/_check.html > /tmp/dom.html
   .venv/bin/python -c "import re,html;d=open('/tmp/dom.html').read();\
   m=re.search(r'<pre id=\"out\">(.*?)</pre>',d,re.S);print(html.unescape(m.group(1)))"
   ```

4. Measure more than one size, always including a short one (`1366x620`,
   `1280x600`): a laptop with the browser open is shorter than the screens a
   developer tests with. `--run-all-compositor-stages-before-draw` is what makes
   the geometry stable.
5. Take a screenshot (`--hide-scrollbars --window-size=W,H --screenshot=...`) and
   look at it, on top of the numbers.
6. **Delete the driver before committing.** `app/static/_*.html` is never part of a
   commit.

Colour and state are read as computed values, not as names: a paused tape bar is
`rgb(255, 183, 3)` with `animation-play-state: paused`, a running one
`rgb(35, 198, 230)` with `running`. Hidden is read the same way, and the attribute
is not proof: a field carrying `hidden` was painted anyway, and only the computed
`display` said so.

`scripts/build_windows.ps1` is the packaging smoke check and needs Windows. The
`--collect-all tzdata` flag in it has never been confirmed in a real build.

## Environment

- `.venv` in the repository root; `python -m pip install -e ".[test]"`.
- `SP_CLP_DB` redirects the database. `tests/conftest.py` sets it to a temporary
  file before the first `app.main` import, so the suite never touches development
  data.
- Server knobs: `SP_CLP_HOST`, `SP_CLP_PORT`, `SP_CLP_POLL_SECONDS` (recorder
  interval, default 5, minimum 1).
- Regenerating icons needs internet: `python scripts/build_icons.py` downloads a
  pinned Lucide version and writes `app/static/icons.js`. The generator list is the
  single source of icons; a name asked for twice is a bug, and a test refuses it.

## Blocked, waiting on the customer

The installer is the open item, and it needs two answers before it can be written:
**Inno Setup or MSI**, and **auto-start with Windows yes or no**. Whatever the
answer, it has to register the `sp-clp` alias in the Windows `hosts` file and open
the firewall for the chosen port. A real build still has to confirm the bundled
`tzdata` and the packaged `app/library` folder.

The two formats differ in what the custom steps cost. MSI is a declarative database
that Windows Installer can roll back and that corporate IT deploys by policy, but
editing `hosts` has no declarative equivalent and the firewall rule is an extension
or a custom action. Inno Setup is a script that drives an installer UI, where both
steps are a few lines; it has neither policy deployment nor repair. One PC in a
plant, handed over on site, points at Inno Setup; a customer whose IT deploys by
GPO points at MSI. The application itself does not change either way.

**Before the installer, one bug has to go.** `DEFAULT_PATH` in `app/storage.py` is
the relative `data/sp-clp.sqlite3`, so the folder follows the working directory and
not the executable. Double-clicking creates it beside the `.exe`, which is what the
README promises, but a shortcut without "Start in", a scheduled task or a service
starts in `C:\Windows\System32` and would write the customer's database there, or
fail for lack of permission. Anchor it to the executable, or to `%PROGRAMDATA%`,
and leave `SP_CLP_DB` as the override.
