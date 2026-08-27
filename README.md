# Win11 Magic Upgrade

[![CI](https://github.com/dlnraja/win11-magic-upgrade/actions/workflows/ci.yml/badge.svg)](https://github.com/dlnraja/win11-magic-upgrade/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Portable **one-click** upgrader: Windows 10 (including **1511** and other obsolete builds) and older Windows 11 → **Windows 11 latest**, keeping **files and apps**.

Inspired by **Flyby11 / FlyOOBE**, but the runtime is **pure Python (PyInstaller)**:

- **No .NET Framework 4.x** required on the target PC  
- **No PowerShell** engine (FlyOOBE/.NET failures on 1511 do not apply)  
- **No FlyOOBE** GUI

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
| Auto-diag | Builds an action plan (32-bit / MBR / obsolete / no SSE4.2 / unsupported HW) |
| Bypass | Embedded intelligent registry pack (LabConfig, MoSetup, HwReqChk, PCHC, …) |
| ISO | Official Microsoft CDN via urllib (Fido-compatible API) |
| MBR→GPT | `mbr2gpt` without wipe + layout prep + **bcdboot bootmgr repair** |
| 32-bit | Win11 impossible → max path **Win10 22H2 x86** (keep apps) |
| No SSE4.2 | Win11 24H2+ won't boot → max path **Win10 22H2 x64** |
| Obsolete Win10 | Intermediate **22H2**, then Win11 (RunOnce resume) |
| Runtime | Pure Python EXE — **no .NET 4.x / no PowerShell** |

## Max compatibility matrix

| Situation | What the app does |
|-----------|-------------------|
| Unsupported TPM/CPU/Secure Boot | Full registry pack + `setup /product server` |
| MBR disk | Auto `mbr2gpt` (no wipe) + `bcdboot` bootmgr repair; remind UEFI firmware |
| Win10 1511 / obsolete | Intermediate Win10 22H2 then Win11 |
| 32-bit Windows | **Cannot** install Win11; upgrades to **Win10 22H2 x86** (keep files/apps) |
| CPU without SSE4.2/POPCNT | **Cannot** boot Win11 24H2+; upgrades to **Win10 22H2 x64** |
| Already Win11 24H2+ | Re-applies registry pack for future feature updates |

Registry keys are embedded in `python/engine/bypass.py` (`REGISTRY_PACK`) — no external `.reg` file required.


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
