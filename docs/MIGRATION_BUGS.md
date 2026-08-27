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
