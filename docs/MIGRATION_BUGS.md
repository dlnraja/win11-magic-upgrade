# Migration bugs & patches

Research notes (Microsoft Support, SetupDiag, FlyOOBE issues, forums) and what this tool does.

| Symptom / code | Typical cause | Patch in this project |
|----------------|---------------|------------------------|
| FlyOOBE needs .NET 4.0.30319 on Win10 1511 | Modern GUI / framework | **Pure Python EXE** (no .NET 4.x, no PowerShell) |
| "This PC can't run Windows 11" on Win10 | Appraiser / MoSetup | LabConfig + MoSetup + HwReqChk + `/product server` |
| 24H2 still blocks after old registry hacks | HwReqChk | `HwReqChkVars` MULTI_SZ spoof + AppCompat purge |
| Direct 1511 → Win11 fails | Obsolete servicing stack | Intermediate **Win10 22H2** then Win11 |
| Win11 needs GPT/UEFI | MBR legacy | `mbr2gpt /allowFullOS` + shrink/WinRE prep |
| `0xC1900101` / SECOND_BOOT | Drivers, AV, backup filters, USB | Detect blockers, stop AV services, clear mapped drives, filter audit |
| `0xC1900208` | Incompatible app | Software pattern warnings |
| `0x80070070` | Disk full | Temp cleanup, hibernation off, space gate |
| `0x8007001F` | Bad device | Disconnect guidance, PNP problem enum |
| `0x800F081F` | Component store | DISM CheckHealth/ScanHealth hook |
| `0xC1900107` | Stale `$WINDOWS.~BT` | Auto cleanup before retry |
| Keep apps greyed out | Edition/language mismatch | Locale→Fido language mapping + log warning |
| Mapped drives / `IOCTL_STORAGE_QUERY_PROPERTY 0x32` | Network drives | `net use * /delete` |
| Legacy Logitech Gaming Software | Filter drivers | Detect + warn |
| Acronis / Macrium / EaseUS filters | SafeOS rollback | Detect + warn; stop related services when possible |
| Stale WU appraiser cache | Soft blocks | Clear appraiser XML/SDB + MoSetup logs |
| BitLocker during mbr2gpt | Code 6 | Suspend BitLocker |
| We couldn't update the system reserved partition / Impossible de mettre a jour la partition reservee | ESP or System Reserved too small / full | Auto cleanup (fonts, OEM) + new 512 MB boot partition via shrink C: + bcdboot (`--cli --srp`) |
| SafeOS fail mount `winre.wim` (`0xC1420121` / `0x800704DB`) | Missing WIMMount / 3rd-party minifilter | Recreate `WIMMount` if driver present; `fltmc` audit; stop AV/VPN/encryption services |
| Feature update stuck "reboot pending" / `0xC1900107` | Leftover `~BT` + RebootPending markers | Wipe `$WINDOWS.~BT` / `$Windows.~WS`; detect CBS/WU reboot keys; warn |
| WinRE disabled blocks updates | `reagentc` disabled / broken recovery | `reagentc /info` + `/enable` |
| Upgrade fails after VeraCrypt decrypt (`0xC190012E`) | Leftover `SetupConfig.ini` ReflectDrivers | Rename Default user WSUS `SetupConfig.ini` |
| VPN / encryption filters | TAP/WFP/minifilter | Soft detect Nord/OpenVPN/WireGuard/AnyConnect/VeraCrypt + fltmc hints |
| `0xC190020E` / low space | Disk full | Hibernate off, temp cleanup, pagefile managed, 12 GB gate |
| Problem devices / `0x80070490` | Bad PnP drivers | `pnputil /enum-devices /problem` warn |
| USB storage during setup | External disks | Warn to unplug removable drives |
| Component store (`0x800F081F`) | Corruption | DISM `/CheckHealth` (warn only) |
| Win11 x64 + 32-bit Boot Manager on ESP | Stale `bootia32` / wrong PE after repairs | Detect PE machine of `bootmgfw.efi`; `bcdboot` rewrite + `bootx64.efi` |
| Win11 on IA32-only UEFI (Atom tablets) | Firmware bitness must match OS for native UEFI | **Hybrid**: CSMWrap IA32 → SeaBIOS → BIOS bootmgr → Win x64 (`--cli --hybrid`) |
| 32-bit Windows + 64-bit CPU | Architecture change not inplace | Max Win10 22H2 x86 keep-apps; Win11 = clean install x64 (via hybrid if IA32 UEFI) |
| `0xC1900208` CompatData / Appraiser | Blocking apps `BlockMigration=True` | Parse CompatData/Appraiser XML and list blockers |
| `DuplicateUserProfileFailure` | Broken/duplicate ProfileList SIDs | Detect missing profile paths + duplicate ProfileImagePath |
| SafeMode / AuditMode hardblock | SetupDiag | Detect SafeBoot Option + AuditBoot/ImageState |
| VHDHardblock | OS on VHD | Detect virtual/VHD system volume |
| `0x80070002` / `0x80240034` / `0x80070422` | WU cache / services | Soft reset SoftwareDistribution + catroot2; start wuauserv/BITS |
| `0x800F0922` / dirty volume | Disk errors / SRP | Dirty-bit check + schedule `chkdsk /F`; SRP enlarge |
| Offline Files / CSC | MIG interference | Stop `CscService` during prep |
| Secondary disks `0x80070002-0x20009` | Extra fixed disks confuse Setup | Warn to disconnect non-system disks |
| CrowdStrike / more EDR | SafeOS driver rollback | Expand service stop list + software detect |
| Long Users paths | InstallPathTooLong / MIG | Warn on extreme path lengths |
| Driver DU during setup | Outdated NIC/storage drivers | Win11 setup uses `/dynamicupdate enable` |
| ESP still too tight (24H2/25H2) | Padding requirement | `EspPaddingPercent=0` registry workaround + SRP enlarge |
| `0x800f0805` / CBS invalid package | Orphan Server language packs | DISM audit + remove orphan `Server-LanguagePack` |
| No undo after failed prep | Risky changes | System Restore point before patch/upgrade |
| CBS / WinSxS bloat | Component store | `StartComponentCleanup` (+ optional `--patch-deep` RestoreHealth/SFC) |
| SysMain / Spooler / Search churn | SafeOS instability | Temporarily stop during prep |
| Support / handoff | Need clear next steps | `SupportGuide.txt` + checklist on Desktop |

## Patch / Enrich / Support mode

Without launching an ISO:

```text
Win11MagicUpgrade.exe --cli --install-patches  # INSTALL all preventive patches (persistent)
Win11MagicUpgrade.exe --cli --patch            # preventives + runtime remediation + support pack
Win11MagicUpgrade.exe --cli --patch-deep       # + DISM RestoreHealth + SFC
```

### Preventive (persistent) vs runtime remediation

| Layer | What | When |
|-------|------|------|
| **Preventive pack** | Durable registry (LabConfig, MoSetup, HwReqChk, EspPaddingPercent, AllowOSUpgrade, LongPaths, …), service start types, pagefile/hibernate, WIMMount registration, AppCompat tree purge | Installed on the PC; survives reboot. Marker: `HKLM\SOFTWARE\Win11MagicUpgrade\PreventivePackInstalled` |
| **Runtime remediation** | Stop AV/VPN filters, clear `$WINDOWS.~BT`, mapped drives, Soft WU reset, fltmc audit, SRP cleanup hooks, etc. | Runs before each upgrade / `--patch` |

One-Click always installs the preventive pack **then** applies runtime remediations before the ISO chain.

### Intelligent compatibility engine (v1.7+)

| Layer | Action |
|-------|--------|
| **Assess** | Detect TPM / Secure Boot / RAM / SSE4.2 gaps |
| **LabConfig + MoSetup** | All Bypass* + AllowUpgradesWithUnsupportedTPMOrCPU |
| **HwReqChkVars** | Full MULTI_SZ spoof (TPM/SB/RAM/NVMe/SSD/CPU/DX/WDDM) tailored to machine |
| **AppCompat purge** | Delete CompatMarkers / Shared / TargetVersionUpgradeExperienceIndicators |
| **CompatData** | Rewrite `BlockMigration=True` → False in cached XML (0xC1900208) |
| **SetupConfig.ini** | `Compat=IgnoreWarning` + DynamicUpdate |
| **Setup args** | `/product server` + `/compat IgnoreWarning` for Win11 |
| **Hard limit** | No SSE4.2/POPCNT → chain targets Win10 22H2 keep-apps (cannot spoof CPU ISA) |

Inventory: `%LOCALAPPDATA%\Win11MagicUpgrade\compat-engine.json`

### Autonomous One-Click (v1.6+)

| Behavior | Detail |
|----------|--------|
| Auto-elevate | No Yes/No admin dialog |
| Quiet Setup | `/quiet /showoobe none` by default |
| Auto remediations | Unload risky fltmc filters, disable problem PnP, dismount USB, offline secondary disks |
| Auto deep heal | DISM RestoreHealth + SFC when CheckHealth reports corruption |
| Auto reboot | Pending reboot / after MBR2GPT → `shutdown /r` + RunOnce `--cli --resume` |
| Fail-fast | MBR/SRP/hybrid hard failures stop the chain (no silent continue) |
| Confirmation | Disabled; set env `MAGIC_CONFIRM=1` to re-enable GUI confirm |

Inventory files: `%LOCALAPPDATA%\Win11MagicUpgrade\installed-preventive-patches.json` and `preventive-patches.reg`.

GUI: **One-Click (autonomous)** or **Patch / Enrich**. Writes `SupportGuide.txt` + `MigrationReport.txt` (Desktop copies).

## Boot Manager / UEFI bitness (smart)

Windows native UEFI boot requires **firmware bitness == OS bitness**. The app:

1. Mounts ESP and reads PE machine type of `bootmgfw.efi` / `bootx64.efi` / `bootia32.efi`  
2. If **OS is x64** but ESP still has **32-bit** boot files → **`bcdboot` repair**  
3. If firmware is **IA32-only** + **x64 CPU** → **hybrid bridge** (not a hard stop):
   - Download **CSMWrap** `csmwrapia32.efi` (GitHub release)
   - Stage on ESP (`EFI\MagicUpgrade\` + selectable `bootia32.magic.efi`)
   - Install BIOS boot files (`bcdboot /f BIOS`)
   - Chain: `IA32 UEFI → CSMWrap → SeaBIOS → BIOS bootmgr → Windows x64`
   - **Secure Boot must be disabled**
   - Keep-apps max on 32-bit OS remains **Win10 22H2 x86**; Win11 x64 needs clean install after hybrid
   - `--cli --hybrid-activate` replaces default `bootia32.efi` (stock backed up)
4. Does not ship unsigned CSMWrap inside the EXE (downloaded on demand)

## System Reserved / EFI (SRP) fix

Classic setup failure when the EFI System Partition (UEFI) or System Reserved (BIOS/MBR) has too little free space for Windows 11 feature updates.

Safe strategy (no third-party Partition Magic):

1. Mount ESP (`mountvol /s`) or assign a letter to System Reserved  
2. Free space: `EFI\Microsoft\Boot\Fonts\*.ttf`, OEM firmware folders under `EFI\<OEM>\`, temp/logs  
3. If still under ~50 MB free or partition under ~260 MB: shrink `C:` slightly, create a **new ~512 MB** ESP (or MBR system partition), run `bcdboot` — OS data kept; old ESP left as fallback  
4. Runs automatically before each upgrade step; also GUI **Fix ESP/SRP** / `--cli --srp`

## Hard non-bypassable limits

- No 32-bit Windows 11
- No SSE4.2 / POPCNT → no 24H2+ boot
- Firmware must boot UEFI after MBR→GPT

## References

- https://learn.microsoft.com/windows/deployment/upgrade/setupdiag
- https://learn.microsoft.com/troubleshoot/windows-client/setup-upgrade-and-drivers/windows-10-upgrade-resolution-procedures
- https://support.microsoft.com/windows (upgrade error codes)
- https://www.sysnative.com/forums/ (WIMMount / SafeOS)
- https://github.com/builtbybel/FlyOOBE
- https://github.com/pbatard/Fido
- https://learn.microsoft.com/windows/deployment/mbr-to-gpt
- VeraCrypt leftover `SetupConfig.ini` / ReflectDrivers (community reports)
