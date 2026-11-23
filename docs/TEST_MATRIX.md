# VM test matrix (manual / CI smoke)

| Host | Scenario | Expect |
|------|----------|--------|
| Win7 SP1 x64 | One-Click | Win10 22H2 → Win11; restore apps |
| Win8.1 Pro WMC | One-Click | ei.cfg Pro; media_offers; settle; Win11 |
| Win8 x64 + local 8.1 ISO | `MAGIC_WIN81_ISO` | 8→8.1→10→11 |
| Win10 1511 | Intermediate | 22H2 then Win11 |
| Win10 22H2 MBR on UEFI | mbr2gpt | GPT + UEFI note |
| Win10 22H2 MBR, legacy-only BIOS | chain | keep MBR; optional OpenDuet prepare, no disk write |
| Win10 22H2 no SSE4.2 | chain | stop at 22H2 (honest) |
| Win11 22H2 / 23H2 / 25H2 | latest | in-place to detected feature update (26H2 security floor 26300.9974). 23H2 is end of updates for every edition (last build 22631.7714), then cumulative if UBR lags. 25H2 26200.10075 is a recognized D preview and still moves to that floor |
| Already on 26H2 26300.10075 (2026-11 D) | latest | recognized optional preview; health check only; not outdated, not an unknown build, not NO-GO. Default target stays 26300.9974 |
| Already on 26H1 (28000) | latest | stay on that branch; default cumulative is the B build 28000.3501. 28000.3620 (2026-11 D, KB5132265) is recognized-only |
| Release health shows a D preview above the B build | latest | default target stays on the B/OOB row (26300.9974). `MAGIC_WIN11_PREVIEW=1` selects 26300.10075, or 28000.3620 when the PC is already on 26H1 |
| 24H2 | latest | unchanged at 26100.9974 (2026-11 B); in-place target remains 26H2 |
| Install media has a revoked 2011 loader and a Windows UEFI CA 2023 `*_EX.efi`, db has that CA | media | accept Setup; the replacement file is part of the check |
| UEFI target is NTFS | bcdboot | refuse; ESP-loaded files stay on FAT32 (AMI NTFS driver is not on this path) |
| Secure Boot on, db lacks Windows UEFI CA 2023, target bootmgr is 2023-only | boot files | refuse bcdboot/Setup/mbr2gpt; keep the current loader; snapshot stays |
| Secure Boot off, legacy BIOS, or 32-bit | boot files | certificate gate does not change the existing path |
| IA32 UEFI (Bay Trail class) | hybrid | CSMWrap, no mbr2gpt; Secure Boot must be off to activate |
| Win10 LTSC | retail ISO | warn; needs enterprise ISO |
| ARM64 | One-Click | blocked with clear message |
| Any + Acronis/LGS | blockers | soft-uninstall → winget restore |

Smoke (no Setup):

```text
Win11MagicUpgrade.exe --cli --dry-run
Win11MagicUpgrade.exe --cli --diagnose
python build\test_v145_suite.py
python build\test_v154_latest_and_firmware.py
```
