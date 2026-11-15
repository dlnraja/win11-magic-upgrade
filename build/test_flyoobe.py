"""FlyOOBE-style helpers stay read-only until a flag is set."""
from __future__ import annotations

import os
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

from engine.flyoobe import (  # noqa: E402
    accept_dropped_iso,
    append_change,
    checklist,
    debloat_commands,
    disclaimer,
    export_recipe,
    go_blocked,
    import_recipe,
    oobe_choices,
    pause_update_commands,
    winget_install_commands,
)


def test_flyoobe(tmp: Path) -> None:
    os.environ.pop("MAGIC_UNSUPPORTED_ACK", None)
    os.environ.pop("MAGIC_PAUSE_WINDOWS_UPDATE", None)
    os.environ.pop("MAGIC_DEBLOAT", None)
    os.environ.pop("MAGIC_OOBE_CHOICES", None)
    os.environ.pop("MAGIC_WINGET_CATALOG", None)
    assert "Back up" in disclaimer()
    assert go_blocked(unsupported=False) is False
    assert go_blocked(unsupported=True) is True
    os.environ["MAGIC_UNSUPPORTED_ACK"] = "1"
    assert go_blocked(unsupported=True) is False
    os.environ.pop("MAGIC_UNSUPPORTED_ACK", None)
    rows = {row["name"]: row for row in checklist({"tpm": "2.0", "ram_gb": 8, "free_gb": 3, "arch": "x64"})}
    assert rows["TPM"]["ok"] is True
    assert rows["Free storage"]["ok"] is False
    assert rows["Secure Boot"]["ok"] is None
    iso = tmp / "win11.iso"
    iso.write_bytes(b"iso")
    assert accept_dropped_iso(str(iso)) == str(iso)
    assert accept_dropped_iso(str(tmp / "notes.txt")) == ""
    assert pause_update_commands() == []
    assert debloat_commands() == []
    assert oobe_choices()["apply"] is False
    assert winget_install_commands() == []
    os.environ["MAGIC_DEBLOAT"] = "1"
    assert any("winget uninstall" in cmd for cmd in debloat_commands())
    os.environ.pop("MAGIC_DEBLOAT", None)
    log = tmp / "changes.jsonl"
    append_change(log, "registry", "example")
    assert "registry" in log.read_text(encoding="utf-8")
    recipe = {"iso": "win11.iso", "keep_apps": True}
    assert import_recipe(export_recipe(recipe))["keep_apps"] is True


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as name:
        test_flyoobe(Path(name))
    print("flyoobe OK")
