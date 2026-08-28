# Research notes — forums & Microsoft (cross-checked 2026-08)

Sources: Microsoft Learn Support Articles, Spiceworks, WindowsForum, Winaero, memstechtips, MS Answers.

## Setup /product server & Appraiser

| Finding | Implication for this project |
|---------|------------------------------|
| `setup.exe /product server` patched on some Canary/newer Setup | Prefer **`sources\setupprep.exe /Product Server`** (Flyby parity) |
| Emptying `appraiserres.dll` often fails on **24H2+** | Keep **writable stage + neutralize** + LabConfig/HwReqChk (already) |
| Language mismatch → "setupprep is not compatible…" | Match Fido ISO locale to OS; recovery scanner flags this |
| **SSE4.2 / POPCNT** required to *boot* 24H2+ | Hard stop → Win10 22H2 (cannot spoof ISA) |

## 0xC1900101 (generic SafeOS rollback)

| Subcode | Typical cause | Auto / guide |
|---------|---------------|--------------|
| 0x20004 | AV, unused SATA, old drivers | stop AV services; SupportGuide |
| 0x20017 | Storage/RST, disk encryption, CrowdStrike | BitLocker suspend; EDR stop |
| 0x2000c | Disk corruption / WIM apply | chkdsk warn; disconnect USB |
| 0x30018 | First-boot migrate (AV/NIC/GPU) | filter stop + driver warn |
| 0x4000D | Second-boot BSOD | point to setupmem.dmp |

Implemented in `python/engine/setup_recovery.py` (scan Panther → actions → soft remediations).

## ESP / System Reserved

"We couldn't update the system reserved partition" / FR equivalent remains a top blocker.
One-Click: cleanup + enlarge. **Do not** use `MAGIC_SRP_CONTINUE=1` unless restore verified.

## MBR2GPT + BitLocker

BitLocker **On** → suspend protectors (safe). **Locked** → unlock first.
After convert: firmware must boot **UEFI** (CSM off).

## Media Center 8.1

Edition mismatch → `sources\ei.cfg` + `pid.txt` (Professional) + setupprep.

## Vista

No supported inplace path to Win10/11 — best-effort only; backup + `MAGIC_ALLOW_VISTA=1`.

## Hybrid IA32 UEFI

CSMWrap → SeaBIOS → BIOS bootmgr. **Secure Boot must be OFF** before activating.

## WMIC removed (Windows 11 25H2+)

Microsoft removed `wmic.exe`. This project uses `wmi_compat.py`: WMIC if present, else PowerShell CIM as last-resort shim (WMI COM still exists). Prefer diskpart / registry / manage-bde when possible. Call sites in errfix/patches/autonomy/preventive/bootmgr/enrich/detect/diskpart_safe go through the shim.

## 0x8007042B / 0x2000D (MIGRATE_DATA)

Frequent on 25H2 feature upgrades: migration arbitration / corrupt `ProgramData\Microsoft\Crypto\RSA\MachineKeys` / TPM-Driver-WMI. `setup_recovery.py` parses Panther + SetupDiagResults.xml and advises **manual** removal of only the flagged MachineKeys file (never auto-wipe).

## Env enrichments (v1.38+)

| Var | Purpose |
|-----|---------|
| `MAGIC_ENTERPRISE_ISO_DIR` | Local VL/enterprise ISO search root |
| `MAGIC_DU_CAB_DIR` | Offline Dynamic Update cab folder hint |
| `MAGIC_STATS=1` | Local opt-in counters (`local-stats.json`) |
| `MAGIC_LP_DRY_RUN=1` | Language pack audit without remove |
| `MAGIC_UNINSTALL_ALLOWLIST=1` | Extra guidance for LGS / Daemon Tools only |
