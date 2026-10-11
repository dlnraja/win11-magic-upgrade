# Blocker apps — soft uninstall & restore

One-Click reduces SafeOS / `0xC1900101` failures by **soft-uninstalling** known filter-heavy apps before Setup, then **restoring compatible versions** after the migration (winget when available).

Module: `python/engine/blocker_apps.py`

## Flow

1. **Detect** installed apps via `Uninstall` registry (HKLM/HKCU, 32+64-bit).
2. **Soft-uninstall** curated entries (`QuietUninstallString` or `msiexec /X … /qn /norestart`).
3. **Queue** each removed (or warn-only) product in `%LOCALAPPDATA%\Win11MagicUpgrade\pending-app-restores.json`.
4. Chain step **`restore_apps`** (end of One-Click): `winget install` modern package if still missing.
5. Write `AppRestoreGuide.txt` (+ Desktop copy) for manual vendor restores.

## Soft-uninstall by default

| Product family | Restore |
|----------------|---------|
| Acronis True Image / Cyber Protect | Manual (vendor) |
| EaseUS Todo Backup | `EaseUS.TodoBackup` |
| Macrium Reflect | `Macrium.ReflectFree` (paid → Macrium account) |
| AOMEI Backupper | `AOMEI.Backupper` |
| Logitech Gaming Software | Prefer **`Logitech.GHUB`**, fallback `Logitech.LGS` |
| Daemon Tools / Alcohol 120% | Prefer not to reinstall SPTD |
| NordVPN / ExpressVPN / WireGuard | Matching winget ids |

## Warn only (unless env)

| Category | Behavior |
|----------|----------|
| **Antivirus** | Pause/disable for Setup. Soft-uninstall + restore only if `MAGIC_SOFT_UNINSTALL_AV=1` |
| **EDR** (CrowdStrike, SentinelOne, …) | **Never** auto-uninstalled |
| **VeraCrypt / TrueCrypt** | Decrypt system volume first; optional later restore |

## Env

| Variable | Effect |
|----------|--------|
| `MAGIC_SKIP_SOFT_UNINSTALL=1` | Detect only |
| `MAGIC_SKIP_APP_RESTORE=1` | Do not winget-restore |
| `MAGIC_SOFT_UNINSTALL_AV=1` | Soft-remove AV + queue restore |

## Safety

- No silent force-kill of EDR.
- Restore runs only on Win10+ builds and only if the product is still absent.
- Successor packages preferred (LGS → G HUB).
