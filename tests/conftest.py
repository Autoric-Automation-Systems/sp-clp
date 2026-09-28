"""Shared pytest setup.

The suite drives the real FastAPI application object, and that object builds its
Storage when ``app.main`` is imported. Without this fixture every test would write
areas and machines into the development database at ``data/sp-clp.sqlite3``. The
environment variable has to be set before the first ``app.main`` import, which is
what a conftest module is for.

Tests that want their own file still pass an explicit path to ``Storage``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TEST_DATABASE = Path(tempfile.mkdtemp(prefix="sp-clp-tests-")) / "tests.sqlite3"
os.environ["SP_CLP_DB"] = str(_TEST_DATABASE)
