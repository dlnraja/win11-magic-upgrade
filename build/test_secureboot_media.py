"""CA 2023 reset warning and revoked rescue media. No firmware writes."""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
if "engine" not in sys.modules:
    pkg = types.ModuleType("engine")
    pkg.__path__ = [str(ROOT / "python" / "engine")]  # type: ignore[attr-defined]
    pkg.__package__ = "engine"
    sys.modules["engine"] = pkg

from engine.secureboot_media import rescue_decision, reset_keys_unsafe, reset_keys_warning  # noqa: E402


def test_ca_and_media() -> None:
    assert reset_keys_unsafe(db_has_2023=True, dbdefault_has_2023=False) is True
    assert reset_keys_unsafe(db_has_2023=True, dbdefault_has_2023=True) is False
    assert "Do not reset Secure Boot keys" in reset_keys_warning()
    blocked = rescue_decision(signed_by_2011=True, dbx_has_pca2011=True, svn_too_low=False, has_2023_loader=False)
    assert blocked["offer"] is False
    assert blocked["rebuild_with_bootex"] is True
    ok = rescue_decision(signed_by_2011=True, dbx_has_pca2011=True, svn_too_low=False, has_2023_loader=True)
    assert ok["offer"] is True
    module = (ROOT / "python" / "engine" / "secureboot_media.py").read_text(encoding="utf-8")
    assert "0x80" not in module and "0x200" not in module


if __name__ == "__main__":
    test_ca_and_media()
    print("secure boot media OK")
