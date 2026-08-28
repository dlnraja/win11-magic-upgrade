# Contributing

1. Fork and create a feature branch from `main`.
2. **Runtime is pure Python** under `python/` (PyInstaller EXE). Do **not** add new PowerShell for the on-PC engine.
3. Legacy PowerShell under `src/` is **reference only** — not executed by One-Click.
4. Keep build scripts under `build/` compatible with **Windows PowerShell 5.1** (ASCII-safe when possible).
5. Local checks:
   - `python -m py_compile` on changed modules
   - `python build/test_*.py` (logic unit tests)
   - Diagnose: `python python/magic_upgrade.py --cli --diagnose` (admin)
6. Open a PR with OS build, MBR/GPT, TPM, and whether SSE4.2 is present.

## Code style

- Prefer stdlib + Win32 (`winreg`, `ctypes`, `subprocess`). No .NET / WinUI on the target PC.
- Log via `engine.logutil.log`.
- Never commit ISOs, `av_keys.json`, `.env`, PFX files, or personal `%LOCALAPPDATA%\Win11MagicUpgrade` logs.

## Security

- Secrets only via GitHub Actions (`CODESIGN_*`, `SIGNPATH_*`, AV keys).
- Do not weaken ESP/MBR safety gates or enable `MAGIC_SRP_CONTINUE` by default.
- SignPath / OV signing: see `docs/CODESIGN.md` and `docs/SIGNPATH_APPLICATION.md`.

## Docs to read first

- `docs/ARCHITECTURE.md` — decision tree
- `docs/MIGRATION_BUGS.md` — error codes
- `docs/LEGACY_OS.md` — Vista→8.1
- `docs/RESEARCH_FORUMS.md` — forum-backed remediations
