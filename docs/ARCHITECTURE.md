# Architecture

```
Win11MagicUpgrade.exe / .cmd
        │
        ▼
python/magic_upgrade.py            GUI/CLI (tkinter) — NO .NET 4.x / NO PowerShell
        │
        └── python/engine/         Pure Python pipeline (stdlib + Win32)
              ├── detect.py        winreg + ctypes + diskpart + wmi_compat
              ├── wmi_compat.py    WMIC → CIM shim (Win11 25H2+ without wmic.exe)
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
              ├── setup_recovery.py Panther/forum recovery map + strict ISO gate
              ├── legacy_os.py     Vista/7/8/8.1 + Media Center registry/media bypass
              ├── version_planner.py  Host/ISO build evaluation + smart skip logic
              ├── logutil.py       Panther logs + MigrationReport + state.json
              └── pipeline.py      Orchestration + quiet setup + resume
```

Legacy PowerShell under `src/` is reference only — **not** used at runtime.

## Upgrade decision tree

1. Preventive pack + intelligent compat engine (LabConfig / HwReqChk / CompatData).  
2. Fix System Reserved / EFI (cleanup; enlarge ~512 MB if needed — idempotent).  
3. If CPU lacks SSE4.2/POPCNT → **max Win10 22H2** (24H2+ will not boot).  
4. If **legacy** (Vista/7/8/8.1) or Win10 build &lt; 19045 → Win10 22H2 ISO → inplace → RunOnce resume.  
   Media Center 8.1: `ei.cfg` + `pid.txt` on staged media. See `docs/LEGACY_OS.md`.  
   After failed Setup: `setup_recovery` parses Panther codes (0xC1900101-*) and remediates.  
5. If system disk is MBR and `mbr2gpt` exists → convert (no wipe) → reboot resume.  
6. Boot Manager / hybrid IA32 path when firmware bitness requires it (Secure Boot OFF checklist).  
7. Strict ISO verify (family/arch/min_build/setupprep) then Win11 latest → `setupprep` + `/product server`.  
   See `docs/RESEARCH_FORUMS.md` for forum-backed notes.  

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
