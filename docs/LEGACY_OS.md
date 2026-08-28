# Legacy Windows support (Vista → 11)

Win11 Magic Upgrade can migrate **very old** hosts toward Windows 11 using the same autonomous chain as obsolete Win10.

## Supported host families

| Host | Build | In-place path |
|------|-------|----------------|
| Windows Vista | 6000–6002 | Attempt Win10 22H2 ISO (no Microsoft-supported path; may be keep-files or clean-only) |
| Windows 7 SP1 | 7601 | Win10 22H2 → (MBR2GPT) → Win11 latest |
| Windows 8 | 9200 | Same |
| Windows 8.1 | 9600 | Same |
| Windows 8.1 **Media Center** | 9600 + MC edition | Same + **ei.cfg / pid.txt** on Setup media (force Pro) |
| Obsolete Win10 | &lt; 19045 | Win10 22H2 → Win11 |
| Win11 21H2–23H2 | 22000–22631 | Win11 latest ISO only |

## What the engine does

1. **Detect** — `os_family`, `is_legacy`, `has_media_center` in `detect.py`.
2. **Registry prep** — `legacy_os.apply_legacy_host_registry()`:
   - `AllowOSUpgrade` / `ReservationsAllowed`
   - `SYSTEM\Setup\Compact=1` on Win8/8.1 (blocks some Win10 upgrades if missing)
3. **Media Center** — writable ISO stage + `sources\ei.cfg` + `sources\pid.txt` (Pro retail PID for edition selection only).
4. **Setup** — always prefers `sources\setupprep.exe` (Flyby11 parity).
5. **Chain** — legacy hosts always hop through **Win10 22H2** (Microsoft CDN) then **Win11 latest**.

## Limits

- **ISO availability:** Microsoft CDN only serves Win10 22H2 + Win11 latest. No auto-download for Vista/7/8 local ISOs.
- **Vista:** treat as best-effort; backup before upgrade.
- **32-bit legacy:** max keep-apps destination is Win10 22H2 x86 (no Win11 in-place).
- **mbr2gpt:** runs after the first Win10 hop on MBR disks (Win7 does not ship `mbr2gpt.exe`).
- **Exotic editions** (Embedded, N, Single Language): Setup may still block; registry + media bypasses cover the common Media Center case.

## Module

`python/engine/legacy_os.py` — detection helpers, registry prep, Media Center media patch.

See also `docs/ARCHITECTURE.md` (version chain + boot resume).
