# Win11 Magic Upgrade

[![CI](https://github.com/dlnraja/win11-magic-upgrade/actions/workflows/ci.yml/badge.svg)](https://github.com/dlnraja/win11-magic-upgrade/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/dlnraja/win11-magic-upgrade)](https://github.com/dlnraja/win11-magic-upgrade/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Portable **autonomous one-click** upgrader: Windows 10 (including **1511** and other obsolete builds) and older Windows 11 → **Windows 11 latest**, keeping **files and apps**.

Inspired by **Flyby11 / FlyOOBE**, but the runtime is **pure Python (PyInstaller)**:

- **No .NET Framework 4.x** on the target PC  
- **No PowerShell** engine  
- **No FlyOOBE** GUI  

> **English** · [Français](docs/README.fr.md)

**Docs:** [Architecture](docs/ARCHITECTURE.md) · [Migration bugs & patches](docs/MIGRATION_BUGS.md) · [Latest release](https://github.com/dlnraja/win11-magic-upgrade/releases/latest)

## Quick start

1. Download **Win11MagicUpgrade-Portable-*.zip** (or the EXE) from [Releases](https://github.com/dlnraja/win11-magic-upgrade/releases/latest).
2. Run as Administrator: `Win11MagicUpgrade.exe` or `Win11MagicUpgrade.cmd`
3. Click **One-Click (autonomous)** — no confirmation by default (set `MAGIC_CONFIRM=1` to ask).

```text
Win11MagicUpgrade.exe --cli --oneclick
```

## What One-Click does (autonomous)

1. Auto-elevate to Administrator  
2. Install **all preventive patches** (persistent registry / services)  
3. **Intelligent compatibility engine** — bypass soft HW/app checks + HwReqChk spoof  
4. Runtime remediations (filters, USB, WU soft reset, ESP/SRP, bootmgr…)  
5. Quiet Setup (`/quiet`) with `/product server` + `/compat IgnoreWarning`  
6. Intermediate versions if needed (e.g. 1511 → Win10 22H2 → Win11)  
7. Auto-reboot + **RunOnce resume** across reboots  

## Features

| Area | Behavior |
|------|----------|
| Auto-diag | Action plan (32-bit / MBR / obsolete / no SSE4.2 / unsupported HW) |
| Compat engine | LabConfig + MoSetup + HwReqChk spoof + CompatData soften + SetupConfig.ini |
| Preventives | Durable pack installed on the PC (`--install-patches`) |
| ISO | Official Microsoft CDN via urllib (Fido-compatible API) |
| MBR→GPT | `mbr2gpt` without wipe + bcdboot repair |
| ESP / SRP | Cleanup + enlarge (~512 MB) — idempotent, resume-safe |
| Boot 32/64 | PE detect; bcdboot repair; **hybrid CSMWrap** for IA32-only UEFI |
| Stability | Resume-safe `$WINDOWS.~BT`, no reboot loops, timeouts, atomic state |
| Logs | Panther-style `setupact` / `setuperr` + Desktop `MigrationReport` + `SupportGuide` |
| Runtime | Pure Python EXE — **no .NET 4.x / no PowerShell** |

## CLI (admin)

```text
Win11MagicUpgrade.exe --cli --oneclick         # Fully autonomous
Win11MagicUpgrade.exe --cli --install-patches  # Persistent preventive pack only
Win11MagicUpgrade.exe --cli --patch            # Preventives + runtime + SupportGuide
Win11MagicUpgrade.exe --cli --patch-deep       # + DISM RestoreHealth / SFC
Win11MagicUpgrade.exe --cli --srp              # Fix ESP / System Reserved only
Win11MagicUpgrade.exe --cli --mbr              # MBR→GPT + bootmgr
Win11MagicUpgrade.exe --cli --hybrid           # Stage CSMWrap IA32 hybrid
Win11MagicUpgrade.exe --cli --diagnose
```

## Situation → action

| Situation | What the app does |
|-----------|-------------------|
| Unsupported TPM / CPU / Secure Boot | Compat engine + `/product server` + `/compat IgnoreWarning` |
| ESP / System Reserved full | Cleanup + enlarge (`--cli --srp`) |
| MBR disk | Auto `mbr2gpt` (no wipe) + bcdboot |
| Win10 1511 / obsolete | Intermediate **Win10 22H2** then Win11 (RunOnce) |
| 32-bit Windows | Max **Win10 22H2 x86** (keep apps) — Win11 needs clean x64 |
| No SSE4.2 / POPCNT | Max **Win10 22H2 x64** — cannot boot Win11 24H2+ |

## Logs

| File | Location |
|------|----------|
| `setupact.log` / `setuperr.log` | `%LOCALAPPDATA%\Win11MagicUpgrade\Panther\` |
| `MigrationReport.txt` | same folder **and Desktop** |
| `SupportGuide.txt` | state folder **and Desktop** |
| `compat-engine.json` | `%LOCALAPPDATA%\Win11MagicUpgrade\` |
| `installed-preventive-patches.json` | same |

## Build

```powershell
powershell -ExecutionPolicy Bypass -File .\build\build_exe.ps1
```

Output: `dist\Win11MagicUpgrade-Portable\`

## CI/CD

- **CI** (`.github/workflows/ci.yml`): Python compile, i18n parity  
- **Release** (`.github/workflows/release.yml`): portable package on `v*` tags  

## License

MIT — see [LICENSE](LICENSE). Vendored Fido remains under its upstream license (see [NOTICE](NOTICE)).

## Disclaimer

Unsupported-hardware installs are not guaranteed by Microsoft. Always back up first. **POPCNT/SSE4.2 cannot be spoofed** for Windows 11 24H2+.
