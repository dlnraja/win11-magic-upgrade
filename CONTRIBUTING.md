# Contributing

1. Fork and create a feature branch from `main`.
2. Keep PowerShell compatible with **Windows PowerShell 5.1** (Win10 1511+).
3. Avoid requiring .NET 4.8+ or WinUI for the core engine.
4. Run diagnose locally: `.\Diagnose.cmd`
5. Open a PR with a short summary and test notes (OS build, MBR/GPT, TPM yes/no).

## Code style

- Module functions prefixed with `Wmu` / `*-Wmu*`.
- Log via `Write-WmuLog`.
- Prefer ASCII in `.ps1` or UTF-8 **with BOM** (Windows PowerShell 5.1).

## Security

Do not commit ISOs, secrets, or personal upgrade logs from `%LOCALAPPDATA%\Win11MagicUpgrade`.
