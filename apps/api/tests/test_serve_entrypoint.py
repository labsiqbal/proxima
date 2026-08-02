"""The serve.py entrypoint must be side-effect free at import time.

The server spawns multiprocessing workers with the "spawn" start method
(graph builds), and a spawn child RE-IMPORTS the parent's __main__ module
(scripts/serve.py). A module-level create_app() therefore made every graph
build re-run the whole boot sequence - DB migrations, settle sweeps,
provisioning - against the LIVE database with whatever code was on disk at
that moment (version-skewed against the running parent). The app may only be
built inside the __main__ guard; importing the module assembles config and
nothing else.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

SERVE = Path(__file__).resolve().parents[1] / "scripts" / "serve.py"


def test_importing_serve_builds_no_app_and_touches_no_database(tmp_path: Path):
    """Import serve.py the way a multiprocessing spawn child does (as a plain
    module, not __main__): no app object may exist and no database file may be
    created anywhere under the process HOME."""
    home = tmp_path / "home"
    home.mkdir()
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('serve_import_probe', {str(SERVE)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "assert not hasattr(module, 'app'), 'serve.py built the app at import time'\n"
        "assert isinstance(module.config, dict) and module.config, 'serve.py config missing'\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    created = sorted(str(p) for p in home.rglob("*.db"))
    assert created == [], f"importing serve.py created database files: {created}"


def test_serve_config_matches_module_shape():
    """The module still exposes the config dict the __main__ path consumes."""
    spec = importlib.util.spec_from_file_location("serve_shape_probe", SERVE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert "database_path" in module.config
    assert "workspace_root" in module.config
