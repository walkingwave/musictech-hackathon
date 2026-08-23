"""Shared, timestamped artifact directories for developer validation tools.

Artifacts intentionally live beside the test tools rather than production
sessions.  Callers own the files inside a run directory; this module only
creates and resolves the directory safely.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

TEST_PACKAGE_DIR = Path(__file__).resolve().parent
TEST_RUNS_DIR = TEST_PACKAGE_DIR / "test_run"


def create_run_directory(
    test_name: str,
    *,
    output: str | Path | None = None,
    force: bool = False,
) -> Path:
    """Create a unique artifact directory for one validation-tool invocation.

    Default directories are named ``<test_name>_YYYY-MM-DD_HH-MM-SS``. A
    numeric suffix prevents same-second collisions without overwriting an
    earlier run.  A caller may supply ``output`` for an explicit location;
    non-empty explicit directories require ``force=True``.
    """
    if not test_name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in test_name):
        raise ValueError("test_name must contain only lowercase letters, digits, '_' or '-'")

    if output is not None:
        destination = Path(output)
        if destination.exists() and any(destination.iterdir()) and not force:
            raise FileExistsError(
                f"output directory is not empty: {destination}; use --force to overwrite"
            )
        destination.mkdir(parents=True, exist_ok=True)
        return destination

    TEST_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base = f"{test_name}_{stamp}"
    for suffix in range(1000):
        name = base if suffix == 0 else f"{base}-{suffix:02d}"
        destination = TEST_RUNS_DIR / name
        try:
            destination.mkdir()
            return destination
        except FileExistsError:
            continue

    raise RuntimeError(f"could not allocate a unique test-run directory for {base}")
