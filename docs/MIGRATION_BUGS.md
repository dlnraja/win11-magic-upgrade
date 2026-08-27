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

## Hard non-bypassable limits

- No 32-bit Windows 11
- No SSE4.2 / POPCNT → no 24H2+ boot
- Firmware must boot UEFI after MBR→GPT

## References

- https://learn.microsoft.com/windows/deployment/upgrade/setupdiag
- https://support.microsoft.com/windows (upgrade error codes)
- https://github.com/builtbybel/FlyOOBE
- https://github.com/pbatard/Fido
- https://learn.microsoft.com/windows/deployment/mbr-to-gpt
