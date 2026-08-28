# Architecture

```
Win11MagicUpgrade.exe / .cmd
        │
        ▼
python/magic_upgrade.py            GUI/CLI (tkinter) — NO .NET 4.x / NO PowerShell
        │
        └── python/engine/         Pure Python pipeline (stdlib + Win32)
              ├── detect.py        winreg + ctypes + diskpart/wmic
              ├── compat.py        Intelligent HW/app compatibility engine
              ├── bypass.py        LabConfig / MoSetup / HwReqChk entrypoint
              ├── preventive.py    Durable preventive pack install
              ├── patches.py       Runtime remediations + AutonomousReboot
              ├── autonomy.py      Auto filters / USB / disks / reboot+RunOnce
              ├── errfix.py       SetupDiag-class extra fixes
              ├── enrich.py        Forum enrichments + DISM heal
              ├── support.py       SupportGuide.txt checklist
              ├── sysreserved.py   ESP / System Reserved cleanup + enlarge
              ├── bootmgr.py       PE bitness + bcdboot / hybrid handoff
              ├── hybrid_uefi.py   CSMWrap IA32 → SeaBIOS → BIOS bootmgr
              ├── mbrgpt.py        mbr2gpt.exe + diskpart
              ├── iso.py           Microsoft CDN API (Fido-compatible, urllib)
              ├── virtdisk.py      Mount ISO via virtdisk.dll
              ├── chain.py         Intermediate version plan across reboots
              ├── version_planner.py  Host/ISO build evaluation + smart skip logic
              ├── logutil.py       Panther logs + MigrationReport + state.json
              └── pipeline.py      Orchestration + quiet setup + resume
```

Legacy PowerShell under `src/` is reference only — **not** used at runtime.

## Upgrade decision tree

1. Preventive pack + intelligent compat engine (LabConfig / HwReqChk / CompatData).  
2. Fix System Reserved / EFI (cleanup; enlarge ~512 MB if needed — idempotent).  
3. If CPU lacks SSE4.2/POPCNT → **max Win10 22H2** (24H2+ will not boot).  
4. If Win10 build &lt; 19045 → Win10 22H2 ISO → inplace → RunOnce resume.  
5. If system disk is MBR and `mbr2gpt` exists → convert (no wipe) → reboot resume.  
6. Boot Manager / hybrid IA32 path when firmware bitness requires it.  
7. Win11 latest ISO → `setupprep` / `setup` with **`/product server`** + `/compat IgnoreWarning` + `/quiet`.  

## Why `/product server`

Documented community / Flyby11 approach: Server setup path skips client TPM/Secure Boot/CPU allow-list checks while still installing client Windows 11 from a client ISO.

## State / resume

| Item | Path |
|------|------|
| State | `%LOCALAPPDATA%\Win11MagicUpgrade\state.json` |
| RunOnce | `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce\Win11MagicUpgrade` |
| Logon task (fallback) | `schtasks` → `Win11MagicUpgradeResume` (until `Phase=Done`) |
| Migration flag | `HKLM\SOFTWARE\Win11MagicUpgrade\MigrationActive` |
| Compat inventory | `...\compat-engine.json` |
| Preventive inventory | `...\installed-preventive-patches.json` |
| Panther logs | `...\Panther\setupact.log` / `setuperr.log` |

Resume is **safe**: live `$WINDOWS.~BT` is not deleted mid-Setup; failed Setup does not advance the chain index.
