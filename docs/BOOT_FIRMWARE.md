# Latest Windows 11 and boot / firmware paths

The upgrade target and the boot path are both detected. Neither is a single hardcoded version or a single “always convert MBR” rule.

Sources are free and need no API key: the public [Windows 11 release health](https://learn.microsoft.com/en-us/windows/release-health/windows11-release-information) page, the public [Windows 11 download](https://www.microsoft.com/en-us/software-download/windows11) page, the Microsoft software-download CDN already used for ISOs, and the public [OpenCorePkg](https://github.com/acidanthera/OpenCorePkg) release archive. Detection uses the Python standard library (`urllib`). If a page fails, the tool keeps working from saved state, then from the built-in floor.

## Which Windows 11

Selection follows the version label, not the raw build number. `26H2` is newer than `26H1`, which is newer than `25H2`, even when `26H1` has the higher build (`28000` vs `26300`).

Microsoft documents **26H1** as a release for new devices, not an in-place feature update for existing PCs. The release-health text is parsed for that exclusion. A PC whose current build is already that branch (major `28000`) stays on it and only tracks that branch’s cumulative. It is not offered a “downgrade” ISO to 26H2.

The floor shipped with this version is **26H2, build 26300, cumulative revision 9725** (the 2026-10-26 out-of-band security update, KB5134133). Edition id **3813** stays first (older ids `3816`, `3321`, `3324` remain fallbacks). The October 2026 monthly security release on that branch is 26300.9711 (KB5127648). A successful parse of a newer in-place security baseline replaces the floor and is cached in `state.json`. A failed or truncated page never lowers the target below the floor. A cached cumulative on the same version that is older than the floor does not hide the floor. A newer cached cumulative still wins.

### Security baseline and optional D previews

Release health has two shapes of table:

- The summary table's **Latest build** is the newest row Microsoft published, including the optional preview.
- The history tables label each row. **B** is the monthly security release, **OOB** is an out-of-band security update, and **D** is the optional non-security preview from the fourth week of the month.

The default target is the newest **B or OOB** row on the newest in-place version. D previews are parsed and stored as their own class so a higher preview revision cannot replace that baseline. As of the 2026-11-02 page, that choice is 26H2 **26300.9725**, while the summary's latest build 26300.9818 is the 2026-10 D preview (KB5127936) and is left unused.

`MAGIC_WIN11_PREVIEW=1` opts in to D previews (26H2 then becomes 26300.9818). A PC already on the device-scoped 26H1 branch stays there and tracks that branch's B/OOB cumulative (28000.3237, KB5134134), including when the summary lists the D revision 28000.3365.

Same feature build with a lower update revision (UBR) asks the built-in `UsoClient.exe` to scan and download once. That is not a new ISO. UBR `0` means unknown and does not trigger the nudge. An ISO whose file name is behind the detected feature build is still installed, then the cumulative step runs after reboot.

## Secure Boot certificates (2011 CA expiry, Windows UEFI CA 2023)

The 2011 Secure Boot certificates expire in 2026. Microsoft's replacement for the Windows boot manager is **Windows UEFI CA 2023**, stored in the firmware **db**. The 2011 issuer is **Microsoft Windows Production PCA 2011**. A later **dbx** update can revoke that 2011 PCA, and a DBX/SVN update can reject a boot manager whose `BOOTMGRSECURITYVERSIONNUMBER` is below the firmware minimum. Rufus 4.16 describes the same rotation: CA 2023 files have to be part of UEFI media checks, and Microsoft moved the signed DBX files under the KEK 2023 path.

`python/engine/secureboot_certs.py` reads this before any UEFI boot-file change. It uses only local Windows facilities:

| Signal | Where |
|--------|--------|
| Secure Boot on or off | `HKLM\SYSTEM\CurrentControlSet\Control\SecureBoot\State\UEFISecureBootEnabled`, then the firmware `SecureBoot` variable if the value is absent |
| Windows UEFI CA 2023 in db | Firmware `db` bytes (subject CN or the published certificate), otherwise `WindowsUEFICA2023Capable` / `UEFICA2023Status` under `...\SecureBoot\Servicing` |
| 2011 PCA revoked | Firmware `dbx` bytes: the PCA subject, or the SHA-1 / SHA-256 / TBS SHA-256 published in [microsoft/secureboot_objects](https://github.com/microsoft/secureboot_objects) |
| Boot-manager SVN | `BOOTMGRSECURITYVERSIONNUMBER,0x...` in the loader and in `dbx` or `SbatLevel` |
| Loader signer | Authenticode certificate table of `bootmgfw.efi` / `bootx64.efi` |

Fingerprints pinned from those public DER files: Windows UEFI CA 2023 SHA-1 `45a0fa32604773c82433c3b7d59e7466b3ac0c67`; Windows Production PCA 2011 SHA-1 `580a6f4cc4e4b669b9ebdc1b2b3e087b80d0678d`, TBS SHA-256 `4e80be107c860de896384b3eff50504dc2d76ac7151df3102a4450637a032146`.

When Secure Boot is **on** and the target loader would fail that check, the tool does not run `bcdboot` (`/f UEFI` or `/f ALL`), does not start Setup, and does not convert MBR→GPT into a UEFI boot the firmware would reject. The plan id gains a `_cert_hold` suffix, `win11_inplace` is cleared, and the log says which certificate or SVN failed. The existing ESP snapshot is taken first and left in place. Snapshot restore itself skips an EFI loader that db/dbx/SVN would reject, and still restores a loader whose signer could not be classified, because that file is the pre-change boot manager.

These paths are unchanged:

- Secure Boot off, or the registry value absent and no firmware variable (state unknown, no proven mismatch)
- Legacy BIOS `bootmgr`, including `bcdboot /f BIOS`
- Legacy-only OpenDuet suggestion (the stick is a BIOS chain; it is not a Secure Boot enrollment)
- IA32 CSMWrap: activation already required Secure Boot off, and the deploy step now refuses to replace `bootia32.efi` while Secure Boot is on. Staging the bridge still happens.
- 32-bit CPU (stop at Windows 10) and 32-bit Windows clean-install limits

Turning Secure Boot on later, after a rewrite done while it was off, is a firmware setup change this tool does not make. The check applies to the Secure Boot state at the moment of the write.

### Rufus 4.16 and what this tree does not copy

[Rufus 4.16 BETA](https://github.com/pbatard/rufus/releases/tag/v4.16_BETA) (2026-10-30) fixes three items that are adjacent to boot media:

- **CA 2023 files during runtime UEFI media validation.** Rufus replaces a few boot files when its CA 2023 option is selected and then hashes the result. This project does not build Rufus-style USB media and does not re-hash a patched NTFS ESP. The equivalent check is the signer/db/SVN gate above, before `bcdboot` or Setup.
- **DBX URL move.** Microsoft now publishes the KEK-2023-signed revocation lists at `PostSignedObjects/SignedByKEK2023/dbx_*.efiauth2` in [secureboot_objects](https://github.com/microsoft/secureboot_objects). Rufus downloads them from `https://api.github.com/repos/microsoft/secureboot_objects/contents/PostSignedObjects/SignedByKEK2023/`. This project does not download or apply a DBX. Applying one can revoke the boot manager that is currently installed. The local firmware `dbx` is read in place.
- **AMI NTFS UEFI driver.** Rufus works around bad reads from the AMI NTFS driver so its own media validation stops false-failing ([AmiNtfsBug](https://github.com/pbatard/AmiNtfsBug)). This tree does not embed `uefi-ntfs` or that AMI driver. Nothing in the boot path needed that workaround.

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

Unit tests cover release-health parsing (including the 2026-10 OOB and D rows), the firmware decision matrix, the Secure Boot certificate gate (db / dbx / SVN / signer), sector splice, and write refusals. They do not boot a PC. Still untested on hardware: a real disk write, firmware setup screens, a machine whose db is missing Windows UEFI CA 2023, Bay Trail `bootia32.efi`, CSM machines whose firmware does not advertise UEFI in the registry or BIOS version, and `ocvalidate.exe` on Windows (the Linux binary of the same release was the one executed here).
