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
