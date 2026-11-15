"""SetupDiag rule names map to an action, a message, and a label."""
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

from engine.setupdiag_rules import actions_for_findings, lookup_rule  # noqa: E402


def test_rules() -> None:
    row = lookup_rule("HardblockMismatchedLanguage")
    assert row is not None
    assert row["strategy"] == "iso-install-language"
    assert lookup_rule("gatedcompatblock")["strategy"] == "safeguard-hold"
    actions = actions_for_findings(
        ["RuleName: CompatBlockedApplicationDismissable", "RuleName: PreReleaseWimMountDriverFound"]
    )
    strategies = {item["strategy"] for item in actions}
    assert strategies == {"compat-ignorewarning", "reregister-wimmount"}
    assert actions[0]["label"].startswith("setupdiag-")


if __name__ == "__main__":
    test_rules()
    print("setupdiag rules OK")
