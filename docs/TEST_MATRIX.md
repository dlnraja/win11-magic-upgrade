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
| Win11 22H2 / 25H2 | latest | in-place to detected feature update (26H2 floor), then cumulative if UBR lags |
| Already on 26H1 (28000) | latest | stay on that branch; do not offer 26H2 as a downgrade |
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
