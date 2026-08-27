# Win11 Magic Upgrade

[![CI](https://github.com/dlnraja/win11-magic-upgrade/actions/workflows/ci.yml/badge.svg)](https://github.com/dlnraja/win11-magic-upgrade/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Portable **one-click** upgrader: Windows 10 (including **1511** and other obsolete builds) and older Windows 11 → **Windows 11 latest**, keeping **files and apps**.

Inspired by **Flyby11 / FlyOOBE**, but **no FlyOOBE / modern .NET dependency** (common failure on Win10 1511).

> **English** · [Français](docs/README.fr.md)

## Quick start

1. Download the latest **Portable** artifact from [Releases](https://github.com/dlnraja/win11-magic-upgrade/releases) or build locally.
2. Run as Administrator:
   - `Win11MagicUpgrade.exe` **or**
   - `Win11MagicUpgrade.cmd`
3. Confirm → automated pipeline.

```powershell
# From source
.\Diagnose.cmd
.\Win11MagicUpgrade.cmd
```

## Features

| Area | Behavior |
|------|----------|
| Bypass | MoSetup, LabConfig, 24H2 HwReqChk, `/product server` |
| ISO | Official Microsoft CDN via [Fido](https://github.com/pbatard/Fido) |
| MBR→GPT | `mbr2gpt` without wipe (+ EFI space / WinRE prep) |
| Obsolete Win10 | Intermediate **22H2**, then Win11 (RunOnce resume) |
| Migration patches | AV/backup blockers, mapped drives, caches, SetupDiag hints |
| i18n | `i18n/strings.json` (en / fr) |

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Migration bugs & patches](docs/MIGRATION_BUGS.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)
- [Third-party notices](NOTICE)

## Build

```powershell
powershell -ExecutionPolicy Bypass -File .\build\build_exe.ps1
```

Output: `dist\Win11MagicUpgrade-Portable\`

## CI/CD

- **CI** (`.github/workflows/ci.yml`): PowerShell parse, Python compile, i18n key parity
- **Release** (`.github/workflows/release.yml`): build portable package on `v*` tags

## License

MIT — see [LICENSE](LICENSE). Vendored Fido remains under its upstream license (see [NOTICE](NOTICE)).

## Disclaimer

Unsupported-hardware installs are not guaranteed by Microsoft. Always back up first. POPCNT/SSE4.2 cannot be bypassed for Windows 11 24H2+.
