# OEM adaptation (Acer / Asus / Toshiba / Dell / HP / Lenovo / …)

Win11 Magic Upgrade detects the PC brand and adapts **boot**, **ESP/SRP**, **encryption**, and **OEM license** handling.

Module: `python/engine/oem_adapt.py`  
Profile dump: `%LOCALAPPDATA%\Win11MagicUpgrade\oem-profile.json`  
Desktop guide: `Win11MagicUpgrade-OEM-Guide.txt`

## Brands

| Family | Typical quirks |
|--------|----------------|
| **Toshiba / Dynabook** | HDD Password (ATA Security) in BIOS; Device Encryption; prefer **new ESP** (do not move recovery) |
| **Acer** | Crowded EFI\Acer capsules; PQSERVICE recovery; Device Encryption |
| **Asus** | EFI\ASUS / MyASUS; set UEFI after MBR2GPT |
| **Dell** | EFI\Dell / SupportAssist payloads fill ESP |
| **HP** | HP_TOOLS / HP_RECOVERY — never merge; new ESP only |
| **Lenovo** | OEM recovery + ThinkPad Device Encryption; disable WinRE early for mbr2gpt |
| **MSI / generic** | Standard BitLocker suspend + ESP cleanup |

## Encryption

1. **BitLocker On** / **Device Encryption** → `manage-bde -protectors -disable` before mutate  
2. **BitLocker Locked** → hard block until unlocked  
3. **Toshiba HDD Password** → unlock in BIOS; tool warns and refuses mutate if the system volume is unreachable  

Env: `MAGIC_OEM_ADAPT=0` disables brand profiling (generic path only).

## OEM Windows license (MSDM / OA3)

- Detects ACPI **MSDM** / **OA3** embedded key when possible  
- **Never wipes** the disk for license reasons — digital entitlement follows the motherboard  
- After upgrade, Windows usually reactivates automatically  

## Partition / boot policy

- Prefer **cleanup fonts + bulky firmware** under `EFI\<OEM>` while **keeping `.efi` loaders**  
- Prefer **new ~512 MB ESP** after shrink C: on Acer/Asus/Toshiba/HP/Dell/Lenovo (avoid growing into recovery)  
- **Do not delete** labeled Recovery / PQSERVICE / HP_RECOVERY / DELLSUPPORT partitions  
- mbr2gpt: disable WinRE early on crowded OEM layouts  

## See also

- [SECURITY.md](../SECURITY.md) — boot ladder  
- [MIGRATION_BUGS.md](MIGRATION_BUGS.md) — ESP/SRP strategy  
