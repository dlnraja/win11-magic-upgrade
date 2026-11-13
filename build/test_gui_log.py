"""Log pane size and log-folder helpers. No Tk."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from gui_log import clamp_sash, layout_file, load_sash, log_folder, newest_log, save_sash  # noqa: E402


def test_log_folder_and_sash() -> None:
    assert log_folder() == Path(r"C:\MagicUpgrade\logs")
    assert clamp_sash(10, 800) == 120
    assert clamp_sash(900, 800) == 680
    assert clamp_sash(400, 800) == 400
    folder = Path(__file__).resolve().parent / "_gui_layout_tmp"
    target = layout_file(folder)
    save_sash(target, 360)
    assert load_sash(target) == 360
    target.unlink()
    folder.rmdir()
    logs = folder / "logs"
    logs.mkdir(parents=True)
    older = logs / "a.log"
    newer = logs / "b.log"
    older.write_text("a", encoding="utf-8")
    newer.write_text("b", encoding="utf-8")
    os_time = newer.stat().st_mtime + 5
    import os

    os.utime(newer, (os_time, os_time))
    assert newest_log(logs) == newer
    older.unlink()
    newer.unlink()
    logs.rmdir()
    folder.rmdir()


if __name__ == "__main__":
    test_log_folder_and_sash()
    print("gui log helpers OK")
