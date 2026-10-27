# Improvement map (40 ideas → implementation)

| # | Idea | Status | Where |
|---|------|--------|--------|
| 1 | Pre-scan compat | Done | `compat_scan.run_compat_scan` (Panther CompatData) |
| 2 | Parse CompatData / Appraiser XML | Done | `compat_scan.py` |
| 3 | SetupDiag-style mapping | Done | `setup_recovery.py` + compat_scan |
| 4 | Blocker catalog soft uninstall + restore | Done | `blocker_apps.py` |
| 5 | Orphan blocker file cleanup | Done | `MAGIC_CLEAN_ORPHAN_BLOCKERS=1` |
| 6 | BlockMigration drivers | Done | `compat_scan` drivers list + soften |
| 7 | Differentiated Setup retry | Done | `pipeline` SetupFailRetries + recovery codes |
| 8 | DU offline | Done | `du_offline.py` + `MAGIC_DU_*` |
| 9 | Dynamic disk space gate | Done | `disk_gate.py` |
| 10 | Auto free space | Done | `MAGIC_FREE_SPACE=1` / auto when low |
| 11 | BitLocker key backup + suspend | Done | `patches.backup_bitlocker_recovery_key` |
| 12 | OEM profiles | Done | `oem_adapt.py` (existing + SupportGuide) |
| 13 | Toshiba HDD password | Done | OEM docs / SupportGuide |
| 14 | Post-mbr2gpt validation | Done | `boot_safe` / mbrgpt postflight |
| 15 | Emergency boot media | Done | `boot_emergency.py` (stage, never auto-flash) |
| 16 | Win8.1 drop ISO | Done | `MAGIC_WIN81_ISO` + iso drop folders |
| 17 | Shared ISO cache | Done | `MAGIC_ISO_CACHE` |
| 18–19 | Language / edition ISO matching | Done | `iso_inspect` + verify gate |
| 20 | LTSC honest path | Done | detect `is_ltsc` + `MAGIC_ALLOW_LTSC` / enterprise ISO |
| 21 | Media Center 2nd pass | Done | chain `settle` + `settle.py` |
| 22 | Optional VLC | Done | `MAGIC_INSTALL_VLC=1` |
| 23 | Activation snapshot | Done | `activation.py` |
| 24 | Settle post-Win10 | Done | chain step `settle` |
| 25 | Vista gate | Done | `MAGIC_ALLOW_VISTA` / `MAGIC_BLOCK_VISTA` |
| 26 | ARM64 detection | Done | `detect.is_arm64` + hard stop |
| 27 | HwReqChk 25H2 extras | Done | `compat.FULL_HWREQCHK_SPOOF` |
| 28 | Broader CompatData soften | Done | `compat_scan.soften_block_migration` |
| 29–30 | Chipset/RST hints | Done | `has_rst_raid` + logs |
| 31 | GUI timeline | Done | ChainLabel + confidence labels |
| 32 | Dry-run | Done | `--dry-run` / GUI Dry-run |
| 33 | Support ZIP | Done | `support_zip.py` / `--support-zip` |
| 34 | Confidence score | Done | `confidence.py` |
| 35 | i18n DE/ES/IT | Done | `i18n/strings.json` |
| 36 | VM test matrix | Done | `docs/TEST_MATRIX.md` |
| 37 | Telemetry opt-in | Done | `MAGIC_STATS` / `MAGIC_TELEMETRY` local |
| 38 | Self-update check | Done | `self_update.py` |
| 39 | Offline pack | Done | `offline_pack.py` / `--offline-pack` |
| 40 | Post-migration checklist | Done | `post_migration.py` |
