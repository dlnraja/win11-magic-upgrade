# Architecture

```
Win11MagicUpgrade.exe / .cmd
        │
        ▼
python/magic_upgrade.py            GUI/CLI (tkinter) — One-Click + checkboxes + progress
python/gui_panel.py                Checkbox options → MAGIC_* env (persisted)
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
              ├── firmware_matrix.py  UEFI/BIOS/CSM/bitness/partition decision
              ├── hybrid_uefi.py   CSMWrap IA32 → SeaBIOS → BIOS bootmgr
              ├── openduet.py      Optional OpenCore 1.0.7 OpenDuet stick (no disk write unless confirmed)
              ├── rawdisk.py       512-byte sector splice for the OpenDuet boot code
              ├── mbrgpt.py        mbr2gpt.exe + diskpart
              ├── win11_release.py Release-health + download-page target (floor if offline)
              ├── iso.py           Microsoft CDN API (Fido-compatible, urllib)
              ├── virtdisk.py      Mount ISO via virtdisk.dll
              ├── chain.py         Intermediate version plan across reboots
              ├── setup_recovery.py Panther/forum recovery map + strict ISO gate
              ├── legacy_os.py     Vista/7/8/8.1 + Media Center registry/media bypass
              ├── blocker_apps.py  Soft-uninstall SafeOS blockers + winget restore
              ├── app_carry.py     License keys + registry/AppData carry (local-only)
              ├── unlock_pack.py   MC/DVD/Win8 migration + LabConfig HW unlock (every hop)
              ├── frameworks.py    .NET / DirectX / VC++ / Media Feature Pack (winget+DISM)
              ├── boot_remediation.py  EFI/MBR failover bot + WinRE/Desktop scripts
              ├── boot_guard.py        Autonomous EFI/GRUB/BCD heal + WinRE/WinPE net (no Live CD)
              ├── research_pack.py Orchestrates SetupDiag/profiles/drivers/compat scans
              ├── setupdiag_runner.py Download/run Microsoft SetupDiag
              ├── profile_heal.py  ProfileList duplicate/.bak audit
              ├── unsigned_drivers.py driverquery + pnputil problem scan
              ├── rollback_scan.py Prior SafeOS Rollback folder analysis
              ├── preflight_power.py AC power + sleep timeouts for Setup
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
   Media Center 8/8.1: `ei.cfg` + `pid.txt` on Win10 media; eligibility in state.json;
   `media_offers` chain step (DVD Player) after Win10 hop. See `docs/LEGACY_OS.md`.  
   After failed Setup: `setup_recovery` parses Panther codes (0xC1900101-*) and remediates.  
5. If the system disk is MBR **and** the firmware matrix says the PC can boot UEFI afterwards, and `mbr2gpt` exists → convert (no wipe) → reboot resume. Legacy-only BIOS keeps MBR. `MAGIC_KEEP_LEGACY=1` skips conversion; `MAGIC_FORCE_MBR2GPT=1` allows it.  
6. Boot Manager / hybrid IA32 path when firmware bitness requires it (Secure Boot OFF checklist). 64-bit firmware with a 32-bit OS uses `bootx64.efi` media, not `bootia32.efi`. Optional OpenDuet/OpenCore 1.0.7 stick for legacy-only BIOS (`docs/BOOT_FIRMWARE.md`).  
7. Strict ISO verify (family/arch/min_build/setupprep) then the detected Win11 feature update → `setupprep` + `/product server`. Target comes from Microsoft release health (version label, not raw build). Floor is 26H2 / 26300 when the page fails. Same build + older cumulative → `UsoClient`, not a second ISO.  
   See `docs/BOOT_FIRMWARE.md` and `docs/RESEARCH_FORUMS.md`.  

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
