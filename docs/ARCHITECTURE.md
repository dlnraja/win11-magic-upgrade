# Architecture

```
Win11MagicUpgrade.exe / .cmd
        │
        ▼
python/magic_upgrade.py            GUI/CLI (tkinter) — NO .NET 4.x
        │
        └── python/engine/         Pure Python pipeline (stdlib + Win32)
              ├── detect.py        winreg + ctypes + diskpart/wmic
              ├── bypass.py        winreg LabConfig / MoSetup / HwReqChk
              ├── patches.py       AV/filters/caches / mapped drives
              ├── sysreserved.py   ESP / System Reserved cleanup + enlarge
              ├── mbrgpt.py        mbr2gpt.exe + diskpart
              ├── iso.py           Microsoft CDN API (Fido-compatible, urllib)
              ├── virtdisk.py      Mount ISO via virtdisk.dll
              ├── chain.py         Intermediate version plan across reboots
              └── pipeline.py      Orchestration + setup.exe /product server

Legacy PowerShell under src/ is optional/reference only — not required at runtime.
```

## Upgrade decision tree

1. Fix System Reserved / EFI (cleanup fonts/OEM; enlarge via new 512 MB boot partition if needed).
2. If CPU lacks SSE4.2/POPCNT → **max Win10 22H2** (24H2+ will not boot).
3. If Win10 build &lt; 19045 → download Win10 22H2 ISO → inplace → RunOnce resume.
4. If system disk is MBR and `mbr2gpt` exists → convert (no wipe) → remind UEFI firmware.
5. Apply registry bypasses + migration patches.
6. Download Win11 latest ISO (Fido) → `setupprep.exe` / `setup.exe` **`/product server`** `/auto upgrade`.
## Why `/product server`

Documented community / Flyby11 approach: Server setup path skips client TPM/Secure Boot/CPU allow-list checks while still installing client Windows 11 from a client ISO.

## State / resume

`%LOCALAPPDATA%\Win11MagicUpgrade\state.json` + `HKLM\...\RunOnce\Win11MagicUpgrade` after intermediate upgrades.
