# Smoke tests for AV false-positive helpers.
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.av_cloud import FP_COMMENT, declare_virustotal_by_hash, write_vendor_fp_drafts  # noqa: E402


def test_fp_comment_mentions_vendors():
    assert "Trojan.Python.Agent" in FP_COMMENT
    assert "Bkav" in FP_COMMENT or "bkav" in FP_COMMENT.lower()
    assert "SecureAge" in FP_COMMENT or "false_positive" in FP_COMMENT
    assert "dlnraja" in FP_COMMENT


def test_vendor_drafts(tmp_path):
    with patch("engine.av_cloud.STATE_DIR", tmp_path), patch(
        "engine.av_cloud.Path.home", return_value=tmp_path
    ):
        (tmp_path / "Desktop").mkdir(exist_ok=True)
        paths = write_vendor_fp_drafts(
            {"sha256": "a" * 64, "sha1": "b" * 40, "md5": "c" * 32}
        )
    assert len(paths) >= 3
    text = paths[0].read_text(encoding="utf-8")
    assert "FALSE POSITIVE" in text or "false positive" in text.lower()


def test_by_hash_invalid():
    assert declare_virustotal_by_hash("short") == "error"


if __name__ == "__main__":
    import tempfile

    test_fp_comment_mentions_vendors()
    with tempfile.TemporaryDirectory() as td:
        test_vendor_drafts(Path(td))
    test_by_hash_invalid()
    print("OK test_v152_fp")
