# Latest Windows 11 and boot / firmware paths

The upgrade target and the boot path are both detected. Neither is a single hardcoded version or a single “always convert MBR” rule.

Sources are free and need no API key: the public [Windows 11 release health](https://learn.microsoft.com/en-us/windows/release-health/windows11-release-information) page, the public [Windows 11 download](https://www.microsoft.com/en-us/software-download/windows11) page, the Microsoft software-download CDN already used for ISOs, and the public [OpenCorePkg](https://github.com/acidanthera/OpenCorePkg) release archive. Detection uses the Python standard library (`urllib`). If a page fails, the tool keeps working from saved state, then from the built-in floor.

## Which Windows 11

Selection follows the version label, not the raw build number. `26H2` is newer than `26H1`, which is newer than `25H2`, even when `26H1` has the higher build (`28000` vs `26300`).

Microsoft documents **26H1** as a release for new devices, not an in-place feature update for existing PCs. The release-health text is parsed for that exclusion. A PC whose current build is already that branch (major `28000`) stays on it and only tracks that branch’s cumulative. It is not offered a “downgrade” ISO to 26H2.

The floor shipped with this version is **26H2, build 26300, cumulative revision 9711**, edition id **3813** (older ids `3816`, `3321`, `3324` remain fallbacks). A successful parse of a newer consumer GA version replaces the floor and is cached in `state.json`. A failed or truncated page never lowers the target below the floor or a newer cache.

Same feature build with a lower update revision (UBR) asks the built-in `UsoClient.exe` to scan and download once. That is not a new ISO. UBR `0` means unknown and does not trigger the nudge. An ISO whose file name is behind the detected feature build is still installed, then the cumulative step runs after reboot.

## Firmware decision

`python/engine/firmware_matrix.py` classifies firmware mode, firmware bitness, CPU/OS bitness, partition style (including a hybrid MBR that also has a GPT protective entry `0xEE`), and Secure Boot. `mbr2gpt` runs only when the plan says the PC can boot UEFI afterwards.

| Situation | Plan | mbr2gpt | Boot files |
|-----------|------|---------|------------|
| 64-bit UEFI + GPT | native UEFI | no | `bootx64.efi` |
| 64-bit UEFI + MBR | convert, then UEFI | yes | `bootx64.efi` |
| 64-bit UEFI + hybrid MBR/GPT | keep layout | no | `bootx64.efi` |
| 64-bit UEFI, style unknown | boot repair only | no | `bootx64.efi` |
| 64-bit UEFI, 32-bit Windows | clean x64 install | no | `bootx64.efi` (not `bootia32.efi`) |
| 32-bit UEFI on a 64-bit CPU (Bay Trail class) | CSMWrap → SeaBIOS → BIOS `bootmgr` | no | `bootia32.efi` + `bootmgr` |
| Same, Secure Boot on | stage the bridge, do not activate it | no | same |
| CSM / legacy boot, UEFI-capable, x64 OS, MBR | convert, then switch firmware to UEFI | yes | `bootx64.efi` |
| CSM, UEFI-capable, already GPT | switch firmware to UEFI | no | `bootx64.efi` |
| Legacy-only BIOS + MBR | keep BIOS boot and the existing bypass path | no | `bootmgr` |
| Legacy-only BIOS + GPT | keep GPT; optional OpenDuet stick | no | `bootmgr` on disk, EFI loader via the stick |
| 32-bit CPU | stop at Windows 10 | no | no Windows 11 path |
| ARM64 | stop | no | use Microsoft ARM64 media |

UEFI-capable means the PC is currently booted in UEFI, or the Secure Boot registry state/key exists, or `BIOSVersion` contains `UEFI`. A CSM machine with none of those signals is treated as legacy-only so `mbr2gpt` cannot brick it. `MAGIC_FORCE_MBR2GPT=1` overrides that. `MAGIC_KEEP_LEGACY=1` wins over the force flag and keeps the current boot.

## Optional OpenDuet stick

On a legacy-only BIOS, OpenCore’s OpenDuet can add a software UEFI layer. The chain is BIOS → `boot0` → `boot1f32` → OpenDuet (`boot`) → OpenCore → the OS loader. The stick has to stay plugged in.

This is optional. The GUI checkbox / `MAGIC_OPENDUET_PREPARE=1` only downloads and stages a folder. It does not write a disk. A disk write is a separate CLI action and always has a dry-run:

```text
Win11MagicUpgrade.exe --cli --openduet-prepare
Win11MagicUpgrade.exe --cli --openduet-write --disk N
Win11MagicUpgrade.exe --cli --openduet-write --disk N --confirm TOKEN
Win11MagicUpgrade.exe --cli --openduet-write --disk N --confirm TOKEN --openduet-partition
```

The dry-run prints a confirm token (`disk|size|bus|boot=yes|no|part=yes|no`) and writes nothing. The token has to match the same disk on the commit. The system/boot disk is refused even if the token is typed by hand. A non-USB disk needs `--openduet-allow-fixed`. Partitioning (`clean`, GPT, FAT32) erases the target and is refused above 128 GiB unless `MAGIC_OPENDUET_ALLOW_LARGE=1`. Sector writes go through a helper that only accepts a 512-byte write at offset 0 on a raw device, and that path also refuses the system disk.

Boot-code splice (from the OpenCore legacy-boot installer, rewritten): MBR keeps the existing partition table (bytes 446–512); the FAT32 boot sector keeps the formatted BPB (87 bytes at offset 3). No BOOTICE and no Rufus.

Pinned archive: **OpenCorePkg 1.0.8** (`OpenCore-1.0.8-RELEASE.zip`, sha256 `2011e8b7216ecb2645d97ea710df965947edd406846e92050b9bc4190be6d27b`, published 2026-09-27). The 1.0.8 notes that touch this bridge are additive: OpenLegacyBoot detects grub4dos, and `Sample.plist` adds an optional driver key `HideVerbose` (left as shipped). Config written for Windows: `ScanPolicy` 0, `SecureBootModel` Disabled, `Vault` Optional, `HfsPlus.efi` off, `OpenUsbKbDxe.efi` on, `KeySupport` false. `BlessOverride` stays empty because OpenCore already scans `\EFI\Microsoft\Boot\bootmgfw.efi` when `ScanPolicy` is 0 (`ocvalidate` calls that path redundant). `MAGIC_OPENDUET_BLESS` can add a different loader path.

Background for the legacy-BIOS technique (rewritten here, not copied): <https://chainesteve.be/ajouter-uefi-pc-bios-legacy-opendued-opencore/>.

`ocvalidate.linux` from the 1.0.8 archive was run on this config (empty `BlessOverride`, and a non-Windows bless path). Both returned “No issues found”. A Windows `bootmgfw.efi` bless is the one case treated as acceptable when it is the only issue.

## What still needs real hardware

Unit tests cover parsing, the decision matrix, sector splice, and write refusals. They do not boot a PC. Still untested on hardware: a real disk write, firmware setup screens, Secure Boot interaction, Bay Trail `bootia32.efi`, CSM machines whose firmware does not advertise UEFI in the registry or BIOS version, and `ocvalidate.exe` on Windows (the Linux binary of the same release was the one executed here).
