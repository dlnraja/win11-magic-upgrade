"""MBR2GPT layout problems, return codes, and the CSM warning."""
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

from engine.mbr2gpt_checks import allow_conversion, csm_advice, explain_code, layout_problems  # noqa: E402


def test_layout_codes_and_csm() -> None:
    os.environ.pop("MAGIC_CSM_OVERRIDE", None)
    assert allow_conversion(layout_problems()) is True
    problems = layout_problems(primary_count=4, has_logical=True, has_active=False, cross_disk_boot=True)
    assert allow_conversion(problems) is False
    assert any("3 primary" in item for item in problems)
    assert "BitLocker" in explain_code(6)
    assert "BCD" in explain_code(100)
    warning = csm_advice(gop=False, integrated_gpu=True)
    assert "Do not disable CSM" in warning
    assert "motherboard" in warning
    os.environ["MAGIC_CSM_OVERRIDE"] = "1"
    assert "disable csm" in csm_advice(gop=False).lower()
    os.environ.pop("MAGIC_CSM_OVERRIDE", None)


if __name__ == "__main__":
    test_layout_codes_and_csm()
    print("mbr2gpt checks OK")
