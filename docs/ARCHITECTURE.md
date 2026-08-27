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
              ├── mbrgpt.py        mbr2gpt.exe + diskpart
              ├── iso.py           Microsoft CDN API (Fido-compatible, urllib)
              ├── virtdisk.py      Mount ISO via virtdisk.dll
              └── pipeline.py      Orchestration + setup.exe /product server

Legacy PowerShell under src/ is optional/reference only — not required at runtime.
```

## Upgrade decision tree

1. If CPU lacks SSE4.2/POPCNT → **stop** (24H2+ will not boot).
2. If Win10 build &lt; 17763 (1809) → download Win10 22H2 ISO → inplace → RunOnce resume.
3. If system disk is MBR and `mbr2gpt` exists → convert (no wipe) → remind UEFI firmware.
4. Apply registry bypasses + migration patches.
5. Download Win11 latest ISO (Fido) → `setupprep.exe` / `setup.exe` **`/product server`** `/auto upgrade`.

## Why `/product server`

Documented community / Flyby11 approach: Server setup path skips client TPM/Secure Boot/CPU allow-list checks while still installing client Windows 11 from a client ISO.

## State / resume

`%LOCALAPPDATA%\Win11MagicUpgrade\state.json` + `HKLM\...\RunOnce\Win11MagicUpgrade` after intermediate upgrades.
