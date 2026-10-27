# Blocker apps — soft uninstall & restore

One-Click reduces SafeOS / `0xC1900101` failures by **soft-uninstalling** known filter-heavy apps before Setup, then **restoring compatible versions** after the migration (winget when available).

Module: `python/engine/blocker_apps.py`

## Flow

1. **Detect** installed apps via `Uninstall` registry (HKLM/HKCU, 32+64-bit).
2. **Carry** (module `app_carry.py`): before soft-uninstall, export known registry trees, license/serial value names, and AppData folders into `%LOCALAPPDATA%\Win11MagicUpgrade\app-carry\<id>\`. Also snapshot the Windows `BackupProductKeyDefault` locally (digital entitlement usually survives inplace; key is for emergency re-entry only).
3. **Soft-uninstall** curated entries (`QuietUninstallString` or `msiexec /X … /qn /norestart`).
4. **Queue** each removed (or warn-only) product in `%LOCALAPPDATA%\Win11MagicUpgrade\pending-app-restores.json`.
5. Chain step **`restore_apps`** (end of One-Click): `winget install` modern package if still missing, then **re-import** `.reg`, rewrite license values when paths still match, and merge userdata.
6. Write `AppRestoreGuide.txt` (+ Desktop copy) for manual vendor restores.

**Privacy:** `licenses.json` and `windows-product-key.txt` are **local-only** — never copied into the Desktop Support ZIP or GitHub autodiag.

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
| `MAGIC_SKIP_APP_CARRY=1` | Do not capture/restore keys or registry/AppData |

## Safety

- No silent force-kill of EDR.
- Restore runs only on Win10+ builds and only if the product is still absent.
- Successor packages preferred (LGS → G HUB).
- License/settings carry is best-effort: account-tied licenses may still need vendor login; obsolete registry paths are skipped with a warning.
