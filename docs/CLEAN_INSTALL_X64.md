# Clean install Windows 11 x64 (assisted checklist)

Use this when **in-place keep-apps is impossible**:

- 32-bit Windows (even on 64-bit CPU)
- IA32-only UEFI path after hybrid CSMWrap (may still need clean install for Win11)
- Severely corrupted servicing stack / repeated SafeOS rollbacks

## Before you wipe

1. Backup documents / browser profiles / product keys (`slmgr /dli`, Microsoft account).
2. Note Wi-Fi password and BitLocker recovery key (`manage-bde -protectors -get C:`).
3. Download official Win11 x64 ISO (match language) — prefer Portable ZIP of this tool + Fido CDN.
4. If firmware is **IA32 UEFI**: follow Desktop `Win11MagicUpgrade-Hybrid-SecureBoot.txt` (Secure Boot OFF).

## Steps (manual — this app does not auto-wipe)

1. Create bootable USB (Rufus / Microsoft Media Creation Tool) **or** mount ISO after hybrid is ready.
2. Boot USB → Custom install → select system partition (or delete/recreate only if you understand data loss).
3. After OOBE: run `Win11MagicUpgrade.exe --cli --install-patches` then `--bypass` for future feature updates.
4. Reactivation: digital license / MSDM OEM key usually returns automatically on same motherboard.

## What One-Click already does instead

On 32-bit OS: max keep-apps path = **Win10 22H2 x86**, then stop (documented in chain).
Win11 requires x64 clean install — this document is that path.

See also: `docs/LEGACY_OS.md`, `docs/RESEARCH_FORUMS.md`.
