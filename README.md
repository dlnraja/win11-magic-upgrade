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
| Obsolete / not-22H2 Win10 | **Always** intermediate **Win10 22H2**, then Win11 (auto-resume) |
| ESP / System Reserved full | Auto cleanup fonts/OEM + enlarge via new 512 MB boot partition + bcdboot |
| SafeOS / WIM / WinRE / filters | WIMMount repair, WinRE enable, fltmc + VPN/VeraCrypt/AV detection, SetupConfig.ini cleanup |
| Boot Manager 32↔64 | Detect ESP PE bitness; repair x64 OS + IA32 bootmgr via bcdboot; **hybrid CSMWrap** for IA32-only UEFI |
| Migration logs | Panther-style `setupact.log` / `setuperr.log` + Desktop `MigrationReport.txt` |
| Extra SetupDiag errors | CompatData scan, ProfileList, WU reset, Safe/Audit mode, VHD, CSC, dirty disk, EDR |
| Runtime | Pure Python EXE — **no .NET 4.x / no PowerShell** |

## Logs (Windows Migration / Setup style)

Like Windows Setup Panther logs:

| File | Location |
|------|----------|
| `setupact.log` | `%LOCALAPPDATA%\Win11MagicUpgrade\Panther\` — all actions |
| `setuperr.log` | same folder — errors + warnings only |
| `MigrationReport.txt` | `%LOCALAPPDATA%\Win11MagicUpgrade\` **and Desktop** — summary + harvested Windows setup errors |

Also session transcripts under `...\logs\upgrade-YYYYMMDD-HHMMSS.log`.
If Windows Setup itself failed, check `C:\$WINDOWS.~BT\Sources\Panther\setuperr.log` (harvested into the report when present).

## Intermediate versions

Any Windows 10 build **below 22H2** automatically steps through **Windows 10 22H2** before Windows 11 (keeps files/apps). Example:

`1511 -> Fix ESP/SRP -> Win10 22H2 -> (MBR to GPT if needed) -> Win11 latest`

After each ISO step, **RunOnce** resumes the next chain step automatically.

CLI extras (admin):

```text
Win11MagicUpgrade.exe --cli --hybrid           # Stage CSMWrap IA32 hybrid on ESP
Win11MagicUpgrade.exe --cli --hybrid-activate  # Replace bootia32.efi (stock backed up)
Win11MagicUpgrade.exe --cli --srp      # Fix System Reserved / EFI only
Win11MagicUpgrade.exe --cli --mbr      # MBR→GPT + bootmgr only
Win11MagicUpgrade.exe --cli --diagnose
```


| Situation | What the app does |
|-----------|-------------------|
| Unsupported TPM/CPU/Secure Boot | Full registry pack + `setup /product server` |
| ESP / System Reserved full | Cleanup + enlarge (new 512 MB boot partition) — `--cli --srp` |
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
