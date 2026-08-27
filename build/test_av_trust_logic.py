"""Unit checks for KIS/Kaspersky trust helpers (no AV mutation)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.av_trust import (  # noqa: E402
    _is_kis_product,
    _run_ok,
    app_paths,
)


def test_kis_product_detection() -> None:
    assert _is_kis_product("Kaspersky Internet Security 21.3") is True
    assert _is_kis_product("Kaspersky Total Security 21.2") is True
    assert _is_kis_product("Kaspersky Anti-Virus 21.0") is True
    assert _is_kis_product("Kaspersky Endpoint Security 12.4") is True
    assert _is_kis_product("Some Other Product") is False
    print("kis product detection OK")


def test_run_ok() -> None:
    assert _run_ok("OK") is True
    assert _run_ok("") is False
    assert _run_ok("error: access denied") is False
    assert _run_ok("completed successfully") is True
    print("run_ok OK")


def test_app_paths_nonempty() -> None:
    paths = app_paths()
    assert len(paths) >= 1
    assert all(isinstance(p, Path) for p in paths)
    print("app_paths OK")


if __name__ == "__main__":
    test_kis_product_detection()
    test_run_ok()
    test_app_paths_nonempty()
    print("ALL av_trust logic tests passed")
