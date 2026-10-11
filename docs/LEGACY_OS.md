# Legacy Windows support (Vista → 11)

Win11 Magic Upgrade migrates **very old** hosts toward the **latest Windows 11 build** using the same autonomous One-Click chain as obsolete Win10 — with **keep apps & files**, RunOnce resume across reboots, and special handling for **Windows 8 / 8.1 Media Center Key Edition**.

## Supported host families

| Host | Build | In-place path |
|------|-------|----------------|
| Windows Vista | 6000–6002 | Attempt Win10 22H2 ISO (no Microsoft-supported path; may be keep-files or clean-only) |
| Windows 7 SP1 | 7601 | Win10 22H2 → (MBR2GPT) → Win11 latest (+ DVD Player eligibility for Home Premium/Pro/Ultimate) |
| Windows 8 | 9200 | Optional **8.1** (local ISO) → Win10 22H2 → Win11 latest |
| Windows 8.1 | 9600 | Win10 22H2 → Win11 latest |
| Windows 8 / 8.1 **Media Center Key Edition** | + WMC / ProfessionalWMC | Same + **ei.cfg / pid.txt** (force Pro) + **DVD Player offer step** after Win10 |
| Obsolete Win10 | &lt; 19045 | Win10 22H2 → Win11 |
| Win11 21H2–23H2 | 22000–22631 | Win11 latest ISO only |

## Media Center Key Edition & Windows DVD Player

Microsoft treated **Windows 8 / 8.1 Pro with Media Center** (and several Win7 SKUs) as eligible for the free **Windows DVD Player** app after an **in-place** upgrade to Windows 10. A clean install voids that path.

One-Click does:

1. **Detect** Media Center via EditionID (`ProfessionalWMC`, …), product name, installed `ehome\ehshell.exe`, or Media Center registry keys.
2. **Persist eligibility** in `%LOCALAPPDATA%\Win11MagicUpgrade\state.json` *before* Setup (after the hop, EditionID becomes `Professional` and WMC is removed).
3. **Patch Win10 media** with `sources\ei.cfg` + `sources\pid.txt` → Professional (generic Pro PID for edition selection only — not activation).
4. Run **inplace** Setup (`setupprep.exe`, keep apps/files).
5. Chain step **`media_offers`**: nudge Windows Update (`UsoClient`), detect `Microsoft.WindowsDVDPlayer`, optional `winget` attempt.
6. Continue recursively to **Win11 latest** (MBR→GPT when needed), still keep-apps.

> The free DVD Player WU offer was **time-limited** (2015). The engine still does the correct inplace + entitlement markers + WU nudge; if Microsoft no longer delivers the AppX, markers remain and the chain continues.

## Optional Windows 8 → 8.1 hop

Microsoft CDN no longer serves Windows 8.1 ISOs. If you have one:

```text
set MAGIC_WIN81_ISO=D:\ISO\Win8.1.iso
```

or drop `*8.1*.iso` under `%LOCALAPPDATA%\Win11MagicUpgrade\iso\`.

Then the chain becomes:

`Win8 → Win8.1 (local) → Win10 22H2 → media offers → MBR2GPT → Win11 latest`

Without a local 8.1 ISO, Win8 goes **direct** to Win10 22H2 (keep-apps), which is the practical CDN path.

## What the engine does

1. **Detect** — `os_family`, `is_legacy`, `has_media_center` in `detect.py`.
2. **Registry prep** — `legacy_os.apply_legacy_host_registry()`:
   - `AllowOSUpgrade` / `ReservationsAllowed`
   - `SYSTEM\Setup\Compact=1` on Win8/8.1
3. **Media Center** — writable ISO stage + `ei.cfg`/`pid.txt` on **Win10** hops only (8.1 intermediate keeps WMC).
4. **Setup** — prefers `sources\setupprep.exe` (Flyby11 parity).
5. **Chain** — legacy hosts hop through **Win10 22H2** then **Win11 latest**, with RunOnce after every reboot.

## Limits

- **ISO availability:** Microsoft CDN only serves Win10 22H2 + Win11 latest. Win8.1 requires a local ISO.
- **Vista:** best-effort; backup before upgrade.
- **32-bit legacy:** max keep-apps destination is Win10 22H2 x86 (no Win11 in-place).
- **mbr2gpt:** runs after the first Win10 hop on MBR disks.
- **Exotic editions** (Embedded, N, Single Language): Setup may still block; Media Center is the covered special case.

## Module

`python/engine/legacy_os.py` — detection, registry prep, Media Center media patch, eligibility persistence, DVD Player claim.

See also `docs/ARCHITECTURE.md` (version chain + boot resume).
