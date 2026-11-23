# win11-magic-upgrade — external research: features, failure modes, bug guards

Compiled 2026-11-13 against `main` @ `e18bebb` (after PR #22). Free methods only. Every item cites a public source.

## Servicing watch, 2026-11-16

November 10, 2026 Patch Tuesday, checked against the public release-health page and the KB articles. No paid feed.

- In-place floor: Windows 11 26H2 **26300.9974**, 25H2 **26200.9974**, 24H2 **26100.9974**, [KB5132061](https://support.microsoft.com/en-us/servicing/os/windows-11/2026/11/kb5132061-windows-11-24h2-25h2-26h2-security-update) (2026-11-10). Release health: [windows11-release-information](https://learn.microsoft.com/en-us/windows/release-health/windows11-release-information).
- 26H1 (new devices only, not the in-place target): **28000.3501**, [KB5132059](https://support.microsoft.com/en-us/servicing/os/windows-11/2026/11/kb5132059-windows-11-26h1-security-update).
- 23H2 is end of updates for every edition on the release-health summary (both edition columns). [KB5131087](https://support.microsoft.com/en-us/servicing/os/windows-11/2026/11/kb5131087-windows-11-23h2-security-update) (2026-11-10, build 22631.7711) says that security update is the last one for Enterprise and Education. The later build is **22631.7714** (2026-11-13, [KB5136483](https://support.microsoft.com/en-us/servicing/os/windows-11/2026/11/kb5136483-windows-11-22h2-23h2-update), non-security out-of-band, all editions). The planner sends a 23H2 PC to the 26H2 floor.
- Both November KBs say there is no December 2026 non-security preview. Next regular servicing is January 2027. The floor stays 26300.9974 until a newer B or security OOB is published.

## Servicing watch, 2026-11-23

Checked the same release-health page again. The November optional previews are published. The monthly security floor did not move.

- Default target stays 26H2 **26300.9974**, 25H2 **26200.9974**, 24H2 **26100.9974**, [KB5132061](https://support.microsoft.com/en-us/servicing/os/windows-11/2026/11/kb5132061-windows-11-24h2-25h2-26h2-security-update) (2026-11-10). 24H2 has no November D row.
- Recognized-only, not the default target: 26H2 **26300.10075** and 25H2 **26200.10075**, [KB5132057](https://support.microsoft.com/help/5132057) (2026-11 D, released 2026-11-19). 26H1 **28000.3620**, [KB5132265](https://support.microsoft.com/help/5132265) (2026-11 D). A PC already on one of these is a known build. The health check does not call it outdated, and it does not NO-GO.
- `MAGIC_WIN11_PREVIEW=1` is what selects those D revisions. 26H1 still is not an in-place target for other PCs. Its default branch cumulative remains **28000.3501**, [KB5132059](https://support.microsoft.com/en-us/servicing/os/windows-11/2026/11/kb5132059-windows-11-26h1-security-update).
- 23H2 is unchanged: end of updates, last build **22631.7714** (2026-11-13 OOB, KB5136483). The planner still sends that PC to the 26H2 floor.
- Rufus 4.16 final (2026-11-17) is reviewed in the next section. DBX lookup, CA 2023 media checks, the AMI NTFS decision, and ESP sizing are recorded there. Firmware `dbx` is still not written. Windows build pins are unchanged.

## Rufus 4.16 final, checked 2026-11-18

[Rufus 4.16](https://github.com/pbatard/rufus/releases/tag/v4.16) was published 2026-11-17 (tag `v4.16`, commit `bed2c33669048a432c7135441d8cbcae72fd315f`). The notes that matter here are the AMI NTFS workaround, the CA 2023 media-validation fix, the "Write as ESP" size regression, the DBX URL update, and embedded GRUB 2.16. Windows 11 build pins in this repo were left as they were.

### DBX URLs

Rufus 4.15 (`res/dbx/dbx_update.sh` on tag `v4.15`) downloaded `PostSignedObjects/DBX/<arch>/DBXUpdate.bin` from the GitHub contents API. Tag `v4.16` downloads `PostSignedObjects/SignedByKEK2023/dbx_<arch>.efiauth2` from the same API, with `Accept: application/vnd.github.v3.raw`. The x64 entry in `res/dbx/dbx_info.h` is:

`https://api.github.com/repos/microsoft/secureboot_objects/contents/PostSignedObjects/SignedByKEK2023/dbx_x64.efiauth2`

That URL is the first entry in `OFFICIAL_DBX_URLS`. On 2026-11-18 the API URL and `https://raw.githubusercontent.com/microsoft/secureboot_objects/main/PostSignedObjects/SignedByKEK2023/dbx_x64.efiauth2` both returned 17256 bytes and the same first 16 bytes. `PostSignedObjects/DBX/amd64/DBXUpdate.bin` still returned 200 and was 24629 bytes, so it is not the same object. It stays a last resort, which is the Rufus 4.15 path, not the 4.16 path. Tags `v4.16_BETA` and `v4.16_BETA2` already used the KEK 2023 API URLs; the final tag did not change `dbx_update.sh`. Nothing is written to firmware `dbx`. A failed download returns empty bytes.

### CA 2023 and SVN

Commit [f8ab5bbb11](https://github.com/pbatard/rufus/commit/f8ab5bbb11e237ebd806970d0b2daea9d1e78283) (`[md5sum] fix CA 2023 replacement files not being re-computed for MD5Sum`, 2026-10-26) is an ancestor of tag `v4.16` (GitHub compare status `ahead`). On that tag, `src/wue.c` still subtracts and adds `md5sum_totalbytes` when `EFI_EX\bootmgfw_EX.efi` and `bootmgr_EX.efi` replace the stock loaders, and `UpdateMD5Sum` in `src/hash.c` rewrites those names in the checksum file. This tree does not hash a USB image. `setup_media_block_reason` still checks the stock loader and any `*_EX.efi` copy, and the SVN marker is still `BOOTMGRSECURITYVERSIONNUMBER`.

### AMI NTFS driver: not applied here

[AmiNtfsBug](https://github.com/pbatard/AmiNtfsBug) describes the AMI UEFI NTFS driver version `0x10000`, used in firmware from vendors such as ASUS, Gigabyte, Dell, and Intel. A file whose data is split across more than one NTFS run can be read correctly in one call and incorrectly in the next call when the first read ends exactly at the end of a run. The driver then returns clusters that are not part of the file. Rufus hit this while validating MD5 hashes from the UEFI environment (`uefi-md5sum`) of an NTFS install stick. The 4.16 workaround is `CreatePreallocatedFile` in `src/stdio.c`: `SetFilePointerEx` to the file size, `SetEndOfFile`, then seek back to 0, so the file has one run when the volume can allocate it. `src/wue.c` also writes an updated `boot.wim` with `WIMLIB_WRITE_FLAG_UNSAFE_COMPACT` for the same reason.

That path is not used here:

- UEFI boot files (`bootx64.efi`, `bootmgfw.efi`, the OpenDuet bridge) are written to a FAT32 ESP. `bcdboot_ready` refuses a UEFI target that is not FAT32. The AMI driver reads NTFS, not FAT32.
- This project does not embed `uefi-ntfs` and does not run a UEFI MD5 check of an NTFS volume. Media checks run in this process, on Windows, against the loader files. They do not ask AMI firmware to read the file.
- The BIOS System Reserved partition can stay NTFS. Legacy `bootmgr` is not loaded by the AMI UEFI NTFS driver.
- The OpenDuet stick is a FAT32 volume. It is not an NTFS UEFI:NTFS chain.

A detection step that looked for AMI firmware and then preallocated NTFS runs would not change any boot file this program writes. The safe layout is already the FAT32 ESP. No extra workaround was added.

### "Write as ESP" size

Rufus 4.16 notes a regression where "Write as ESP" used the whole drive instead of a 1 GB partition. On tag `v4.16`, `MAX_ISO_TO_ESP_SIZE` in `src/rufus.h` is `1 * GB`, and `src/drive.c` sets `main_size = min(main_size, MAX_ISO_TO_ESP_SIZE)` when `write_as_esp` is set. Diskpart has the same class of mistake: `create partition efi` with no `size=` uses the remaining disk.

`efi_create_diskpart_script` always emits `create partition efi size=<MB>`. `bounded_esp_size_mb` clamps the request to `MAX_BOOT_CREATE_MB` (2048) and, when the disk size is known, to one megabyte under that disk. The PowerShell Storage create path uses the same clamp before `New-Partition -Size`. 2048 MB is the existing Windows boot-partition ceiling. It is not Rufus's 1 GB ISO-to-ESP limit; a Windows ESP here is a boot partition, not a copy of a small ISO. The OpenDuet script still uses `create partition primary` with no size because that stick is one FAT32 volume, not an EFI system partition, and writes above 128 GiB stay refused.

### GRUB 2.16

`res/grub2/grub2_version.h` on tag `v4.16` sets `GRUB2_PACKAGE_VERSION` to `"2.16"`. This repository does not embed that binary. An existing Linux EFI directory on the ESP is left in place.

"Coverage" is based on a grep/read of `README.md`, `docs/` and `python/engine/` in the local clone. **Covered** = code exists; **Partial** = something exists but the gap described is real; **Missing** = nothing found.

Legend: **[SAFETY]** = touches disks, boot files, firmware variables or encryption. Destructive steps must stay opt-in, or run only after a verified backup plus a post-op check.

---

## P0 — boot-safety and data-safety guards (do first)

### P0-1. `bcdboot /s X:` never creates the UEFI NVRAM entry **[SAFETY]**
- **Problem:** Microsoft's BCDBoot reference says that with `/s`, "this entry is not created". BCDBoot then relies on the firmware fallback `\EFI\Boot\bootx64.efi`. Our builders (`post_op_verify.py:435-441`, `drive_map.py:478`) always pass `/s <letter>`, because "bcdboot always gets a verified letter". After an ESP rebuild, or on a PC whose NVRAM was reset (BIOS update or CMOS clear), the PC may have no "Windows Boot Manager" entry and fall to "no bootable device".
- **Detect:** after bcdboot, run `bcdedit /enum firmware`. Check that a `{bootmgr}` entry exists and that `{fwbootmgr} displayorder` contains it. Also check that `\EFI\Boot\bootx64.efi` exists on the ESP.
- **Fix:** when the target ESP is the live system ESP, run a second `bcdboot <win> /f UEFI` without `/s`, which writes NVRAM. Add `/p` on dual-boot PCs so GRUB keeps its place. Otherwise fall back to `bcdedit /set {fwbootmgr} displayorder {bootmgr} /addfirst`. Always make sure `\EFI\Boot\bootx64.efi` exists, by copying `bootmgfw.efi` only if the file is missing or is Microsoft-signed. Make the post-op verify fail if no firmware entry exists.
- **Coverage:** Covered. After a successful `bcdboot /s`, repair runs `bcdboot` again without `/s` (`/f UEFI` or `/f ALL`, `/p` when Linux is present) and checks `bcdedit /enum firmware` for `{bootmgr}` in `displayorder`. Missing `\EFI\Boot\bootx64.efi` is copied from `bootmgfw.efi`.
- **Sources:** https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/bcdboot-command-line-options-techref-di?view=windows-11 ; https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/bcd-system-store-settings-for-uefi?view=windows-11

### P0-2. BitLocker suspended for only 1 reboot, and the app continues "even if still on" **[SAFETY]**
- **Problem:** `oem_adapt.py:683` runs `manage-bde -protectors -disable C:` with no `-rebootcount`. Per Microsoft, protection then resumes after the next restart. Our flow can reboot several times: boot repair, then Setup's SafeOS, then first and second boot. The policy also says "CONTINUE even if still on". Changing the boot manager, the BCD or Secure Boot DB/DBX while protectors are on triggers the BitLocker recovery screen (PCR7/PCR11 change).
- **Detect:** run `manage-bde -status C:` and check "Protection Off" plus the reboot count. Run `manage-bde -protectors -get C:` to confirm a Numerical Password protector exists.
- **Fix:**
  - Use `-rebootcount 3` (range 0-15) sized to the plan, then re-read the status.
  - Refuse every boot, BCD, ESP or DBX write while protection is still On. A NO-GO is better than a recovery-key lock-out.
  - Before the first boot write, show the user the recovery key ID and where the key is stored (MSA, file, print), with a "J'ai noté ma clé" confirmation.
- **Coverage:** Covered. Suspend uses `-rebootcount` (default 3, `MAGIC_BITLOCKER_REBOOTS`). Boot repair refuses to start while protection is On or the recovery-key acknowledgement is missing.
- **Sources:** https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/manage-bde-protectors ; https://learn.microsoft.com/en-us/troubleshoot/windows-client/windows-security/suspend-bitlocker-protection-non-microsoft-updates ; https://support.microsoft.com/en-us/servicing/os/windows-server/2022/02/kb5012170-security-update-for-secure-boot-dbx

### P0-3. A BIOS "reset keys" or CMOS clear removes Windows UEFI CA 2023 from db, so the PC can't boot or asks for the BitLocker key **[SAFETY]**
- **Problem:** On firmware whose factory-default db has only the 2011 CAs, resetting Secure Boot keys drops the 2023 CA. A boot manager signed only by the 2023 CA then fails Secure Boot, and PCR7 changes. Forum reports describe BitLocker recovery after exactly this. Our recovery docs and RAM rescue may tell users to "reset Secure Boot keys", which is Microsoft's own KB5025885 recovery step.
- **Detect:** in `secureboot_certs.py`, compare the 2023 CA in the active `db` against `dbDefault`, which you read through GetFirmwareEnvironmentVariableEx in the same GUID namespace. If db has the 2023 CA and dbDefault doesn't, flag "reset keys = unsafe".
- **Fix:** add a warning to the GUI, the rescue docs and the auto-diag issue: "Ne pas réinitialiser les clés Secure Boot avant une mise à jour BIOS dont les clés par défaut incluent CA 2023". Point to the OEM BIOS update first. Keep Microsoft's recovery steps (disable SB → `mountvol s: /s` → `bcdboot %systemroot% /s S:`) as a last resort, and require the user to confirm they have the BitLocker key.
- **Coverage:** Covered. If db has the 2023 CA and dbDefault does not, the warning says not to reset Secure Boot keys. No firmware variable is written.
- **Sources:** https://www.elevenforum.com/t/bitlocker-recovery-after-bios-reset-wiped-secure-boot-2023-certs-no-key-need-help-restoring-db-to-match-pcr7.50104/ ; https://support.microsoft.com/en-us/topic/how-to-manage-the-windows-boot-manager-revocations-for-secure-boot-changes-associated-with-cve-2023-24932-41a975df-beb2-40c1-99a3-b3ff139f832d (section "Recovery procedure") ; https://www.dell.com/support/kbdoc/en-us/000390990/secure-boot-transition-faq

### P0-4. After the 2011 PCA revocation (DBX) or an SVN bump, old rescue media no longer boots **[SAFETY]**
- **Problem:** Once KB5025885 mitigations 3 and 4 are applied (`AvailableUpdates` 0x80 / 0x200), any ISO, USB or WinPE/WinRE ramdisk carrying a 2011-signed or low-SVN `bootmgfw.efi` is refused. Our "WinRE/WinPE RAM rescue" and our downloaded ISOs could be dead on arrival exactly when needed.
- **Detect:** read the DBX for the PCA2011 hash and the firmware SVN, plus the Servicing `AvailableUpdates` bits and the event IDs listed in KB5025885. Check the signer and SVN of the rescue media's `bootmgfw.efi`; the Authenticode parser already exists.
- **Fix:**
  - Before offering a rescue USB or ramdisk, check that its boot manager passes the current DB/DBX/SVN, the same check Rufus does.
  - If it fails, rebuild the media with the 2023-signed boot manager: `bcdboot ... /bootex`, or copy `Windows\Boot\EFI_EX\bootmgfw_EX.efi`, following Microsoft's Make2023BootableMedia logic. This is free, but needs ADK for the ISO path.
  - Never set `AvailableUpdates` 0x80/0x200 ourselves.
- **Coverage:** Covered. Rescue media signed only by the 2011 CA, or with a low SVN, is not offered. A 2023 loader can still be offered. AvailableUpdates 0x80/0x200 is not set.
- **Sources:** https://support.microsoft.com/en-us/servicing/os/windows/2023/03/how-to-manage-the-windows-boot-manager-revocations-for-secure-boot-changes-associated-with-cve-2023 ; https://support.microsoft.com/en-us/servicing/os/windows/2025/02/updating-windows-bootable-media-to-use-the-pca2023-signed-boot-manager ; https://support.microsoft.com/en-us/servicing/os/windows/2025/02/enterprise-deployment-guidance-for-cve-2023-24932 ; https://www.neowin.net/news/rufus-46-gets-windows-11-24h2-unsupported-pc-bypass-reactos-boot-fix/ (Rufus "revoked UEFI bootloaders check: SBAT, SVN, DBX")

### P0-5. Dual-boot: Windows repairs must not break shim/GRUB (SBAT) or the boot order **[SAFETY]**
- **Problem:**
  - The August 2024 Windows SBAT update made Linux fail with "Verifying shim SBAT data failed: Security Policy Violation". Microsoft's dual-boot detection missed custom setups; it was fixed only by the May 13, 2025 updates.
  - Windows (and bcdboot by default) also moves Windows Boot Manager to the top of the UEFI order, which hides GRUB or rEFInd.
- **Detect:**
  - Linux EFI trees on the ESP: the `LINUX_EFI_VENDORS` snapshot already does this.
  - Read `SbatLevel` (already read) and the registry `HKLM\SYSTEM\CurrentControlSet\Control\SecureBoot\SBAT`.
  - Note the `{fwbootmgr}` displayorder before and after.
- **Fix:**
  - When Linux is detected, offer (opt-in) Microsoft's documented `SBAT\OptOut=1`.
  - Use `bcdboot /p` (preserve the order) instead of the default "move first".
  - Restore the original displayorder after repair, instead of `/addfirst` (`boot_guard.py:190` currently forces Windows first).
  - Write a GRUB recovery note (`mokutil --set-sbat-policy delete`, `GRUB_DISABLE_OS_PROBER=false`, `efibootmgr -o`) into the report.
- **Coverage:** Covered. A recorded Linux EFI tree keeps firmware order (`/p`, no `/addfirst`) and adds a GRUB/SBAT note. `MAGIC_SBAT_OPTOUT=1` is required before any SBAT opt-out.
- **Sources:** https://www.bleepingcomputer.com/news/microsoft/microsoft-shares-temp-fix-for-linux-boot-issues-on-dual-boot-systems/ ; https://www.bleepingcomputer.com/news/microsoft/microsoft-fixes-linux-boot-issues-on-dual-boot-windows-systems/ ; https://www.rodsbooks.com/refind/bootcoup.html ; https://learn.microsoft.com/en-us/answers/questions/1335597/how-can-i-stop-windows-from-resetting-boot-order-a ; https://alioth-lists.debian.net/pipermail/pkg-grub-devel/2023-September/018595.html

### P0-6. MBR2GPT followed by "disable CSM" leaves a black screen on GPUs without UEFI GOP **[SAFETY]**
- **Problem:** After MBR2GPT the firmware must boot UEFI with CSM off. Older GPUs (legacy VBIOS only) show no picture at all with CSM off, and the user can't even reach the BIOS. AMD documents UEFI-only GPU support; Microsoft Q&A has many "black screen after disabling CSM" threads.
- **Detect:**
  - GPU vendor and device ID plus VBIOS date via the Win32_VideoController CIM shim.
  - The GOP presence heuristic: a GPU released before ~2012, or no "UEFI" in the VBIOS string where readable.
  - The integrated GPU's presence, as a fallback output.
- **Fix:** before advising "disable CSM" after MBR2GPT, show a GO/NO-GO: "GPU sans GOP probable : branchez l'écran sur la sortie de la carte mère, ou gardez CSM." Document the CMOS-clear recovery. Don't automate the conversion when the GPU is at risk unless the user overrides.
- **Coverage:** Covered. The post-conversion warning does not tell the user to disable CSM when GOP is unknown or missing, unless `MAGIC_CSM_OVERRIDE=1`.
- **Sources:** https://www.amd.com/en/resources/support-articles/faqs/GPU-N4XCSM.html ; https://learn.microsoft.com/en-ca/answers/questions/5585141/can-i-turn-on-secure-boot-without-go-to-bios-becau ; https://learn.microsoft.com/en-us/windows-hardware/drivers/bringup/mbr2gpt-tool-test-guidance

### P0-7. MBR2GPT layout pre-checks and return-code mapping **[SAFETY]**
- **Problem:** MBR2GPT refuses a disk when any of these hold:
  - more than 3 primary partitions;
  - extended or logical partitions;
  - no active system partition;
  - a BCD without a default OS entry;
  - missing 16 KB + 2 sectors of free space at the start, or 16 KB + 1 sector at the end;
  - System Reserved on a different disk from Windows ("Cannot find OS partition(s) for disk 0").

  Return code 6 means encrypted volumes, 7 means layout, 100 means GPT is fine but BCD entries weren't restored.
- **Detect:** run all the documented checks ourselves before calling `/validate`. Detect a cross-disk boot partition (the bootmgr device's disk differs from the OS disk). Parse `setupact.log` / `setuperr.log` in `/logs:`.
- **Fix:**
  - Map return codes 0-10 and 100 to plain-language actions (6 → suspend BitLocker, 7 → layout fix).
  - On cross-disk boot, offer `bcdboot C:\Windows /s <new active part on disk 0> /f BIOS` first. This is opt-in.
  - On 4Kn disks, expect a 260 MB ESP instead of 100 MB.
  - After code 100, run our BCD regenerate plus verify.
- **Coverage:** Covered. Layout problems (more than 3 primaries, logical partitions, no active partition, cross-disk boot, free space, 4K sectors) and return codes 0–10 and 100 are mapped. A cross-disk `bcdboot` remains opt-in and is not run from this check.
- **Sources:** https://learn.microsoft.com/en-us/windows/deployment/mbr-to-gpt ; https://ss64.com/nt/mbr2gpt.html ; https://learn.microsoft.com/en-us/answers/questions/5974868/how-to-fix-mbr2gpt-validate-failure-cannot-find-os ; https://learn.microsoft.com/en-us/answers/questions/5579359/mbr2gpt-disk-layout-validation-failed-for-disk-2

### P0-8. ESP free space ≤ 10 MB makes current cumulative updates fail with 0x800F0922 (May 2026 KB5089549)
- **Problem:** Microsoft confirmed that 24H2/25H2 May 2026 updates fail at about 35% on reboot ("Undoing changes") when the ESP has ≤ 10 MB free. CBS.log shows the ESP-space error. The mitigation is `HKLM\SYSTEM\CurrentControlSet\Control\Bfsvc\EspPaddingPercent=0`, then KIR. Fonts cleanup (`\EFI\Microsoft\Boot\Fonts\*.ttf`) is the classic workaround.
- **Detect:** ESP free MB (use a numeric threshold of 15 MB, and record it in auto-diag). Grep CBS.log for "EFI" or "ESP" plus "insufficient", and for 0x800f0922.
- **Fix:** already partly done (EspPaddingPercent and cleanup). Add three things:
  - a hard pre-flight number (free < 15 MB → clean, then re-measure, then only then propose an opt-in enlarge);
  - parse `CBS.log` in the auto-diag;
  - write the post-upgrade padding value back only if the user wants it.
- **Coverage:** Covered. Free space under 15 MB or 0x800F0922 in CBS.log cleans fonts and remeasures. Enlarging the ESP requires `MAGIC_ESP_ENLARGE=1`.
- **Sources:** https://www.windowscentral.com/microsoft/windows-11/microsoft-confirms-may-2026-update-install-failure-with-error-0x800f0922-on-windows-11-and-provides-mitigation ; https://learn.microsoft.com/en-us/answers/questions/3943836/is-there-an-easy-way-to-fix-this-issue-and-is-this ; https://www.winhelponline.com/blog/resize-efi-system-partition/

### P0-9. WinRE partition too small (KB5034441 0x80070643, event 4502) **[SAFETY]**
- **Problem:**
  - The WinRE partition needs 250 MB free for WinRE servicing. Without it, Windows Update fails with the misleading error 0x80070643.
  - `System.evtx` shows event 4502 "Windows Recovery Environment servicing failed", ErrorPhase 2.
  - Microsoft won't fix this automatically. Its manual procedure is KB5028997 (shrink OS by 250 MB, delete and recreate the recovery partition with the GPT type/attributes, run `reagentc /enable`). The script version is KB5035679.
- **Detect:** run `reagentc /info` and parse the `\\?\GLOBALROOT\device\harddiskN\partitionM` location. Measure free space on that partition by mounting it via a GUID path. Query the System log for event 4502. Note whether the recovery partition sits right after the OS partition, which the script requires.
- **Fix:** report and NO-GO the "WinRE update" class. Offer an opt-in, verified implementation of KB5028997: back up `winre.wim` first, and if anything fails, re-run `reagentc /enable`. **Never automatic.**
- **Coverage:** Covered. Free space under 250 MB or event 4502 is a NO-GO for the WinRE update. Resize runs only with `MAGIC_WINRE_RESIZE=1` and backs up `winre.wim` first.
- **Sources:** https://support.microsoft.com/en-us/servicing/os/windows/2023/06/kb5028997-instructions-to-manually-resize-your-partition-to-install-the-winre-update ; https://support.microsoft.com/en-us/servicing/os/windows/2024/02/kb5035679-instructions-to-run-a-script-to-resize-the-recovery-partition-to-install-a-winre-update ; https://learn.microsoft.com/en-us/answers/questions/3743059/update-kb5034441-fails-to-install ; https://www.bleepingcomputer.com/news/microsoft/microsoft-wont-fix-windows-0x80070643-errors-manual-fix-required/

### P0-10. `reagentc /enable` fails with "Unable to update Boot Configuration Data"
- **Problem:** After ESP or BCD rebuilds, or when several system partitions exist, WinRE won't re-enable, and the "RAM rescue / WinRE" plan silently disappears.
- **Detect:** check the reagentc exit code and output. Look for a `ReAgent.xml` vs `winre.wim` mismatch, and for duplicate ESPs or system partitions in the drive map.
- **Fix:** run the documented sequence in a bounded way:
  1. `reagentc /disable`.
  2. Rename (don't delete) `C:\Windows\System32\Recovery\ReAgent.xml`.
  3. `reagentc /setreimage /path <R:\Recovery\WindowsRE>` through a GUID or temp-letter path.
  4. `reagentc /enable`.
  5. If the BCD is ambiguous, `bcdboot` to the active ESP, then retry once.

  Add a post-check that `reagentc /info` says "Enabled".
- **Coverage:** Covered. `reagentc` "Unable to update Boot Configuration Data" is detected. The disable, rename `ReAgent.xml`, setreimage, enable sequence runs only with `MAGIC_REAGENTC_REPAIR=1`.
- **Sources:** https://learn.microsoft.com/en-us/answers/questions/4163725/reagentc-exe-unable-to-update-boot-configuration-d ; https://superuser.com/questions/1799744/repair-the-windows-recovery-partition-fix-errors-preventing-winre-from-enabling

### P0-11. A storage controller in RAID/VMD (Intel RST) gives "no drives" or INACCESSIBLE_BOOT_DEVICE **[SAFETY]**
- **Problem:** On 11th–13th gen Intel PCs, Setup or WinPE sees no disk without the IRST VMD driver. Switching RAID to AHCI in the BIOS without preparation bluescreens with INACCESSIBLE_BOOT_DEVICE. This matters for our WinPE/RAM rescue and for any advice to change the controller mode.
- **Detect:** `detect.py` already flags `has_rst_raid`. Add the PnP IDs of the VMD controller (`iaStorVD`) and whether a rescue image contains that driver.
- **Fix:**
  - Inject the PC's own storage driver (export via `pnputil /export-driver` for iaStorVD/iaStorAC) into the WinPE/WinRE rescue image.
  - Never advise RAID→AHCI unless safe mode is staged (`bcdedit /set {current} safeboot minimal`, removed after boot), and the bounded-rollback state is recorded.
- **Coverage:** Covered. iaStorVD/iaStorAC or the existing RST flag asks for the PC's storage driver in rescue media. RAID to AHCI is offered only with `MAGIC_RAID_TO_AHCI=1` and a staged safe-boot boot.
- **Sources:** https://www.dell.com/support/kbdoc/en-us/000188116/intel-11th-generation-processors-no-drives-can-be-found-during-windows-10-installation ; https://www.dell.com/support/kbdoc/en-us/000216532/changing-the-storage-controller-mode-causes-windows-blue-screen-with-inaccessible-boot-device-error ; https://superuser.com/questions/1280141/switch-raid-to-ahci-without-reinstalling-windows-11-10

### P0-12. Never delete `{current}`; break boot loops with documented reversals only **[SAFETY]**
- **Problem:** Forum advice for "Undoing changes" or "Automatic Repair" loops often suggests destructive BCD edits. Microsoft's guidance:
  - remove a stuck `safeboot` value;
  - otherwise use WinRE "Uninstall updates";
  - or `DISM /Image:C:\ /Cleanup-Image /RevertPendingActions`.
- **Detect:** in the pending-reboot state, check for a stale `safeboot` value on the loader, `pending.xml` in WinSxS, and repeated boot failures (a `BootFailureDetected` SetupDiag rule, or the boot-status data).
- **Fix:** put these into the bounded-repair strategy list as low-risk first steps. Keep a deny-list so no code path can `bcdedit /delete {current}` or `{default}`.
- **Coverage:** Covered. `bcdedit /delete {current}` and `bcdedit /delete {default}` are refused before the process starts. The suggested first steps are clearing safeboot, WinRE uninstall updates, and DISM RevertPendingActions. None of those run automatically.
- **Sources:** https://learn.microsoft.com/en-us/answers/questions/3882729/windows-11-not-functioning-after-update-stuck-in-r ; https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/dism-operating-system-package-servicing-command-line-options

### P0-13. A dynamic disk blocks the in-place upgrade (0x80070001 / "can't tell if your PC has enough space")
- **Problem:** Since 22H2, Windows 11 Setup fails on an OS or boot volume on a dynamic disk. Diskpart can convert to basic only after deleting all volumes.
- **Detect:** `drive_map.py` already has `volume.dynamic`. Raise it to a pre-flight NO-GO with a clear message and a 0x80070001 mapping.
- **Fix:** explain the safe options: back up first, then use a free third-party convert tool or reinstall. As an advanced workaround, run Windows 11 21H2 `setup.exe` with `/InstallFrom` pointing to the new `install.wim`. **Never run `diskpart convert basic` automatically.**
- **Coverage:** Covered. A dynamic OS disk is a pre-flight NO-GO mapped to 0x80070001. `diskpart convert basic` is never run.
- **Sources:** https://www.neowin.net/news/microsoft-apparently-blocks-windows-11-22h2-upgrades-on-dynamic-disks-with-0x8007001-error/ ; https://www.winhelponline.com/blog/error-0x80070001-windows-11-setup/ ; https://www.winhelponline.com/blog/convert-dynamic-disk-to-basic-disk/

---

## P1 — upgrade-success and diagnosis features

### P1-14. A full SetupDiag rule-name → action table
- **Problem:** We download and run SetupDiag and parse FailureData, but none of the documented rule names are mapped to actions:
  - `CompatBlockedApplicationDismissable`
  - `CompatBlockedFODDismissable`
  - `HardblockMismatchedLanguage`
  - `HardblockDeviceOrDriver`
  - `GatedCompatBlock`
  - `PreReleaseWimMountDriverFound`
  - `DiskSpaceBlockInDownLevel`
  - `DiskSpaceFailure`
  - `BootFailureDetected`
  - `UnsignedDriverBootFailure`
  - `DriverPackageMissingFileFailure`
  - `FindDebugInfoFromRollbackLog`
  - …
- **Fix:** add a static table keyed by rule GUID or name, giving the action, the user message and the next strategy:
  - Dismissable → re-run with `/compat ignorewarning`.
  - MismatchedLanguage → fetch an ISO in the InstallLanguage locale.
  - FOD → list the FoDs to remove.
  - WimMount → re-register `wimmount.sys`.
  - Gated → safeguard hold (see P1-16).
  - Feed the table into auto-diag issue labels.
- **Coverage:** Covered. `setupdiag_rules.py` maps the documented rule names to an action, a message, and an auto-diag label.
- **Sources:** https://learn.microsoft.com/en-us/windows/deployment/upgrade/setupdiag

### P1-15. Run `setup.exe /compat scanonly` before the real run, and decode its exit codes
- **Problem:** `compat_scan.py` deliberately doesn't run the real scan. Microsoft's own pre-check, `setup.exe /auto upgrade /quiet /compat scanonly /copylogs <dir>`, returns 0xC1900210 (no issues) or 0xC1900208 (actionable compat issues). Other codes, such as 0xC1900200/0xC1900202 (hardware) and 0xC190020E (disk space), are documented.
- **Fix:** once the ISO is mounted (already done for the upgrade), run scanonly with a timeout. Parse `CompatData_*.xml` and the exit code. Use `/copylogs` to gather Panther logs into the report automatically. Use `/noreboot` on the real run so we can verify the boot state before the first reboot (fits "reboot only on GO").
- **Coverage:** Covered. `setup.exe /compat scanonly /copylogs /noreboot` is built and its exit codes are decoded. It runs only with `MAGIC_COMPAT_SCANONLY=1`.
- **Sources:** https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/windows-setup-command-line-options?view=windows-11 ; https://learn.microsoft.com/en-us/troubleshoot/windows-client/setup-upgrade-and-drivers/windows-10-upgrade-error-codes ; https://learn.microsoft.com/en-us/troubleshoot/windows-client/setup-upgrade-and-drivers/use-windows-setup-compatibility-scan-logs-to-identify-blocking-issues

### P1-16. Detect safeguard holds and explain them before bypassing
- **Problem:** "The update is on its way" means a safeguard hold. Microsoft's example: the Intel Smart Sound Technology driver 10.29/10.30.0.5152 on 11th gen. Bypassing holds (`DisableWUfBSafeguards`) can lead to the very BSOD the hold prevents.
- **Detect:** read the Appraiser `GatedBlockId` / `RedReason` values from `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\TargetVersionUpgradeExperienceIndicators\*`. Cross-check installed driver versions against a small, sourced table of known holds (for example IntcAudioBus 10.29/10.30.0.5152).
- **Fix:** show the hold and the fix (update the driver from the OEM or Windows Update) first. Only then offer the bypass. Today the preventive pack sets `DisableWUfBSafeguards=1` unconditionally; make it opt-in and log the reason.
- **Coverage:** Covered. A known hold is explained first. `DisableWUfBSafeguards` is applied only with `MAGIC_DISABLE_SAFEGUARDS=1`.
- **Sources:** https://learn.microsoft.com/en-us/windows/deployment/update/safeguard-holds ; https://learn.microsoft.com/en-us/windows/release-health/known-issues/known-issue-2746 ; https://github.com/AveYo/MediaCreationTool.bat/blob/main/bypass11/Skip_TPM_Check_on_Dynamic_Update.cmd

### P1-17. Language and edition mismatch greys out "Keep personal files and apps"
- **Problem:** If the ISO's base language (for example en-US vs en-GB, or fr-FR) or edition doesn't match the installed OS, Setup offers only "Nothing", which looks like a clean install. This is the SetupDiag rule `HardblockMismatchedLanguage`.
- **Detect:** read `HKLM\SYSTEM\CurrentControlSet\Control\Nls\Language\InstallLanguage` (already read in `enrich.py`), the EditionID, and the architecture. Compare them with `iso_inspect` (the language of install.wim/esd).
- **Fix:** hard-block the upgrade when the language or edition doesn't match and the user chose keep-apps. Pick the matching ISO language automatically (Fido/MCT language list). For editions, use `ei.cfg` (already present).
- **Coverage:** Covered. Keep-apps is refused when the ISO language or edition does not match the installed OS.
- **Sources:** https://learn.microsoft.com/en-us/answers/questions/4042358/unable-to-keep-personal-files-and-apps-when-attemp ; https://www.elevenforum.com/t/windows-setup-keep-personal-files-and-folders-and-this-first-option-are-greyed-out.45855/ ; https://learn.microsoft.com/en-us/windows/deployment/upgrade/setupdiag

### P1-18. Corrupt or "Account Unknown" profiles: 0x8007001F-0x20006 and MIGRATE_DATA failures
- **Problem:** 24H2 upgrades fail when ProfileList holds orphaned or `.bak` profiles.
- **Detect:** in `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList`, look for SIDs whose ProfileImagePath is missing, `.bak` duplicates, or SIDs that don't resolve.
- **Fix:** report them, then offer an opt-in export plus removal of the orphan key. Never delete profile folders.
- **Coverage:** Partial (`profile_heal.py` exists; check it covers `.bak` / unresolvable SIDs and the 0x8007001F mapping).
- **Sources:** https://learn.microsoft.com/en-us/answers/questions/3973210/how-to-fix-0x8007001f-on-windows-11-24h2-update ; https://learn.microsoft.com/en-us/troubleshoot/windows-client/setup-upgrade-and-drivers/windows-10-upgrade-resolution-procedures

### P1-19. A 0xC1900101 phase decoder with phase-specific actions
- **Problem:** The extended code (0x20017, 0x30018, 0x40017, 0x4000D…) gives the phase: SafeOS, first boot or second boot. Second-boot failures (0x40017) are typically GPU, filter, AV or encryption drivers. Microsoft suggests the basic display driver, a clean boot, and checking the Memory Integrity incompatible-driver list.
- **Fix:** extend `setup_recovery.py` with:
  - an offline "Memory integrity incompatible drivers" list, by reading `CodeIntegrity` events 3087/3033 or the HVCI incompatible list;
  - opt-in GPU driver rollback to the Microsoft Basic Display Adapter;
  - a staged clean boot (`msconfig`-equivalent registry) that the bounded-attempt framework undoes automatically.
- **Coverage:** Covered. 0x20017, 0x30018, and 0x40017 map to SafeOS, first boot, and second boot. Basic display rollback stays opt-in.
- **Sources:** https://learn.microsoft.com/en-us/troubleshoot/windows-client/setup-upgrade-and-drivers/windows-10-upgrade-resolution-procedures ; https://learn.microsoft.com/en-us/answers/questions/3954877/0xc1900101-0x40017-error-during-windows-11-upgrade ; https://learn.microsoft.com/en-us/answers/questions/3914053/24h2-upgrade-error-0xc1900101-0x40017-the-install

### P1-20. Rufus-style `setup.exe` wrapper behavior: `/auto upgrade` must stay silent with bypasses
- **Problem:** Rufus issue #2752: with the bypass wrapper, `setup.exe /auto upgrade /compat IgnoreWarning ...` still opened the GUI until Rufus 4.8. Rufus issue #2568 lists the exact 24H2 in-place keys: delete CompatMarkers/Shared/TargetVersionUpgradeExperienceIndicators, set HwReqChkVars and `MoSetup\AllowUpgradesWithUnsupportedTPMOrCPU`.
- **Fix:** add a regression test that the One-Click argv stays fully unattended when bypasses are applied (check that `/auto upgrade /quiet /eula accept /compat ignorewarning /dynamicupdate` survive wrapping). Re-apply the keys immediately before launch, because Setup or the Appraiser can recreate CompatMarkers.
- **Coverage:** Covered for the keys; Partial for wrapper argv regression.
- **Sources:** https://github.com/pbatard/rufus/issues/2752 ; https://github.com/pbatard/rufus/issues/2568 ; https://github.com/pbatard/rufus/commit/c800448c620a3767565e038e29739a9dccfe5e71

### P1-21. Repair-upgrade and servicing-stack mismatch after a bypass upgrade (FlyOOBE #380)
- **Problem:** After a 25H2 upgrade via a bypass tool, users report DISM failing with 0x80070057, LCU KB5065789 failing with 0x80070306, and the repair install (`setup.exe` from an ISO) refused with "Le PC doit prendre en charge TPM 2.0". The repair path also needs the bypass.
- **Detect:** compare the DISM / servicing-stack build with the OS build, and record the last CU error.
- **Fix:** add a "Repair upgrade (keep everything)" mode that reuses our bypass pipeline with a same-build ISO. This is the documented community fix. Add a post-upgrade health check: `DISM /Online /Cleanup-Image /CheckHealth`, then `/RestoreHealth /Source:wim:<iso>\sources\install.wim:<index>` with `/LimitAccess`.
- **Coverage:** Partial.
- **Sources:** https://github.com/builtbybel/FlyOOBE/issues/380 ; https://github.com/builtbybel/FlyOOBE/issues/473

### P1-22. Extend the rollback window (Windows.old) automatically after success
- **Problem:** The default go-back window is 10 days. Users find problems later.
- **Fix:** after a successful upgrade, offer `DISM /Online /Set-OSUninstallWindow /Value:30` (range 2–60). Check it with `/Get-OSUninstallWindow`. Warn before Disk Cleanup removes Windows.old.
- **Coverage:** Covered. `DISM /Set-OSUninstallWindow` runs only with `MAGIC_KEEP_WINDOWS_OLD=1`. The value is clamped to 2–60 days.
- **Sources:** https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/dism-uninstallos-command-line-options?view=windows-11 ; https://support.microsoft.com/en-us/windows/deployment/install-upgrade/go-back-to-the-previous-version-of-windows

### P1-23. Secure Boot CA 2023 readiness: surface status, errors and events, and point to firmware
- **Problem:** Microsoft's 2026 playbook says to check `UEFICA2023Status` ("Updated"), `UEFICA2023Error`, and System events 1801/1795/1808. When firmware blocks the DB write, the fix is an OEM BIOS update first.
- **Fix:** we read these values already. Add three things:
  - a plain GO/NO-GO card in the GUI;
  - a free "trigger" action (`AvailableUpdates` 0x40 then 0x100, plus the `\Microsoft\Windows\PI\Secure-Boot-Update` task), gated on BitLocker being suspended (P0-2) and only for DB plus boot manager, never 0x80/0x200;
  - a verify step (db contains "Windows UEFI CA 2023"; bootmgfw signer).
- **Coverage:** Covered. A GO/NO-GO card is produced from UEFICA2023Status and UEFICA2023Error. Writing db stays out of this check.
- **Sources:** https://techcommunity.microsoft.com/blog/windows-itpro-blog/secure-boot-playbook-for-certificates-expiring-in-2026/4469235 ; https://support.microsoft.com/en-us/servicing/os/secure-boot/2026/03/secure-boot-troubleshooting-guide ; https://support.microsoft.com/en-us/servicing/os/secure-boot/2025/06/windows-secure-boot-certificate-expiration-and-ca-updates ; https://github.com/ajf8729/BlackLotus

### P1-24. DBX updates fail with 0x800F0922 when the firmware or loader is incompatible
- **Problem:** KB5012170 (Secure Boot DBX) fails with 0x800f0922 on incompatible firmware and can trigger BitLocker recovery after 1–2 restarts. Microsoft's guidance is `manage-bde -protectors -disable %systemdrive% -rebootcount 2` and a firmware update.
- **Fix:** in the error catalog, distinguish "0x800F0922 + DBX/SecureBoot in CBS" (firmware: update the BIOS) from "0x800F0922 + ESP space" (P0-8). Don't apply ESP fixes for a firmware-class error.
- **Coverage:** Covered. 0x800F0922 plus DBX or KB5012170 is a firmware error. The same code plus ESP or EFI is the space error. They are not treated as the same fix.
- **Sources:** https://support.microsoft.com/en-us/servicing/os/windows-server/2022/02/kb5012170-security-update-for-secure-boot-dbx ; https://www.bleepingcomputer.com/news/microsoft/windows-kb5012170-update-causing-bitlocker-recovery-screens-boot-issues/

### P1-25. `bootrec /fixboot` "Access is denied" on UEFI: never rely on bootrec there
- **Problem:** `bootrec /fixboot` is an MBR/PBR tool. On UEFI it commonly returns "Access is denied". The working fix is `bcdboot <win> /s <ESP> /f UEFI` with verified letters.
- **Fix:** make sure the rescue scripts (`boot_emergency.py`, rescue_env) don't call bootrec when the firmware is UEFI. Add the rule to the error catalog: "Access is denied" from bootrec means switch to bcdboot.
- **Coverage:** Covered. `bootrec /fixboot` is skipped when the firmware is UEFI. bcdboot is the UEFI path.
- **Sources:** https://learn.microsoft.com/en-us/answers/questions/3308763/(w10)-bootrec-fixboot-access-is-denied-even-after?forum=windows-all&referrer=answers ; https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/windows-boot-issues-troubleshooting

### P1-26. bcdboot "Failure when attempting to copy boot files": a decision tree
- **Problem:** Typical causes:
  - the wrong source (`X:\Windows` in WinPE instead of the real OS);
  - the target isn't FAT32 or isn't the ESP;
  - firmware type mismatch (`/f UEFI` on a BIOS boot, or the reverse);
  - the ESP is full;
  - the BCD template is missing.
- **Fix:** before bcdboot, assert in this order:
  1. the source has `System32\Config\BCD-Template`;
  2. the target is FAT32 with the ESP GPT type, or an active NTFS for BIOS;
  3. the target has free space ≥ 10 MB.

  Use `/v` and parse the verbose output into the catalog. Retry with `/f ALL` only on removable or BIOS targets.
- **Coverage:** Covered. bcdboot is refused without BCD-Template, the right filesystem, or 10 MB free. `/f ALL` is not the first retry on UEFI.
- **Sources:** https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/bcdboot-command-line-options-techref-di?view=windows-11 ; https://learn.microsoft.com/en-us/answers/questions/5926793/bcdboot-fails-with-failure-when-attempting-to-copy

### P1-27. Fast Startup or hibernation leaves NTFS "hibernated" and breaks dual-boot or offline repair
- **Problem:** With Fast Startup on, the C: volume is left hibernated. Linux and offline tools refuse to mount it ("Windows is hibernated, refused to mount"). WinPE or offline edits to a hibernated volume can corrupt it.
- **Detect:** `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power\HiberbootEnabled`, plus the presence of `hiberfil.sys`.
- **Fix:**
  - Before any reboot into the RAM rescue, Linux plan B or an offline repair, run `powercfg /h off`, or at least disable `HiberbootEnabled` and do a full shutdown.
  - Restore the setting afterwards.
  - The app already runs `powercfg /hibernate off` in the preventive pack: tie it to the rescue path and log it.
- **Coverage:** Covered. Fast Startup is detected and `powercfg /h off` is the step before an offline repair.
- **Sources:** https://unix.stackexchange.com/questions/107978/cant-access-windows-drive-windows-is-hibernated-refused-to-mount ; https://superuser.com/questions/1609432/really-disabling-hibernation-in-windows-10

### P1-28. Storage Spaces and ReFS volumes become inaccessible after an upgrade or CU
- **Problem:** Recent reports describe inaccessible Storage Spaces after a 25H2 CU (KB5067036) and ReFS volumes inaccessible after a 10 to 11 upgrade. Upgraded pools also can't be read by older Windows.
- **Detect:** `Get-StoragePool` / `Get-VirtualDisk` health through the CIM shim, ReFS volumes (FileSystem == ReFS), and Dev Drive.
- **Fix:** pre-flight WARN plus a backup prompt. Never run a pool "upgrade" automatically. Record pool and ReFS versions in the report so the auto-diag can spot regressions.
- **Coverage:** Covered. An unhealthy pool or a ReFS volume is a backup warning. The pool is not upgraded.
- **Sources:** https://learn.microsoft.com/en-us/answers/questions/5603446/kb5067036-and-w11-25h2-inaccessible-storage-spaces ; https://techcommunity.microsoft.com/t5/windows-11/refs-volume-inaccessible-after-update-from-windows-10-22h2-to/m-p/4224060 ; https://support.microsoft.com/en-us/windows/experience/storage-filemanagement/storage-spaces-in-windows

### P1-29. OneDrive Known Folder Move: "my files disappeared" after the upgrade
- **Problem:** After an upgrade or OOBE, Desktop and Documents may be redirected to `%USERPROFILE%\OneDrive\...`. Turning OneDrive off then looks like data loss.
- **Detect:** before and after the upgrade, snapshot `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders` (Desktop, Personal, My Pictures) and the file counts.
- **Fix:** post-migration check. If the paths changed, show where the files are now and link Microsoft's "Find lost files after upgrading". Optionally pre-seed OOBE to skip the backup prompt (FlyOOBE parity).
- **Coverage:** Covered. Shell-folder paths under OneDrive are reported so turning OneDrive off does not look like data loss.
- **Sources:** https://support.microsoft.com/en-us/windows/deployment/install-upgrade/find-lost-files-after-upgrading-windows ; https://windowsforum.com/windows-news.4/onedrive-known-folder-move-how-windows-reassigns-folders-and-how-to-reclaim-them.380058/

### P1-30. Device encryption silently turns on after a clean install or reset (24H2+)
- **Problem:** On 24H2 clean installs or resets, with an MSA or work account plus TPM and Secure Boot, BitLocker device encryption turns on automatically and the key goes to the account. Users of our clean-install or rescue path then hit BitLocker recovery on the next boot change without knowing a key exists.
- **Fix:** for the clean-install path, offer (opt-in) the `PreventDeviceEncryption` setting in unattend/registry, or show the user where the key is after OOBE. Include the BitLocker status in every report (already partly done).
- **Coverage:** Covered for the opt-in. `PreventDeviceEncryption` is emitted only with `MAGIC_PREVENT_DEVICE_ENCRYPTION=1`.
- **Sources:** https://learn.microsoft.com/en-us/windows-hardware/design/device-Experiences/oem-bitlocker ; https://blog.elcomsoft.com/2025/05/forensic-implications-of-bitlocker-by-default-in-windows-11-24h2/

### P1-31. Boot-Info style read-only diagnostic summary (Boot-Repair parity)
- **Problem:** Boot-Repair's most valued feature is a non-destructive "Boot-Info" report you can paste to a forum, with automatic backups before any repair. Our auto-diag is per-run log scanning.
- **Fix:** add a `--boot-info` mode (and a GUI button) that changes nothing and dumps:
  - the disk/partition table (style, GUIDs, types, flags, sizes, free);
  - every ESP's tree (filenames, sizes, signers);
  - `bcdedit /enum all /v` and `bcdedit /enum firmware`;
  - `reagentc /info`;
  - Secure Boot db/dbx/SBAT/SVN;
  - the BitLocker status.

  Redact it, then attach it to the auto-diag issue.
- **Coverage:** Covered. `--boot-info` text is assembled from already collected read-only sections.
- **Sources:** https://sourceforge.net/p/boot-repair/home/Home/

### P1-32. UEFI boot-entry manager plus ESP backup and restore (EasyUEFI parity, free)
- **Problem:** EasyUEFI's free edition (home use) lists, creates, reorders, disables and backs up NVRAM boot entries, and backs up, restores or rebuilds the ESP. We back up the ESP but don't expose NVRAM entries.
- **Fix:** an NVRAM view in the GUI from `bcdedit /enum firmware` (read-only by default). Add "restore saved order" and "remove a stale entry pointing to a non-existent partition GUID" (opt-in). Save the full firmware order with every boot snapshot so rollback restores it too.
- **Coverage:** Covered for the read-only view. Stale firmware entries are listed. Removing one requires `MAGIC_REMOVE_STALE_BOOT=1`.
- **Sources:** https://www.easyuefi.com/easyuefi/ ; https://www.easyuefi.com/easyuefi/faq/Backup-EFI-System-Partition.html ; https://www.easyuefi.com/easyuefi/faq/Rebuild-EFI-System-Partition.html

### P1-33. Inject the PC's critical drivers into the rescue WinPE/WinRE (Macrium "Fix boot problems" / ReDeploy idea)
- **Problem:** Macrium's rescue media detects Windows installations and repairs boot options. Its paid ReDeploy injects storage drivers. Our WinPE/RAM rescue may not see NVMe/VMD/RAID disks.
- **Fix:** at build time, `pnputil /export-driver` the storage, NIC and USB controller drivers, then `dism /Add-Driver` them into the rescue `boot.wim`. All free, built-in tools. Boot the rescue in the same firmware mode as the OS (UEFI vs CSM).
- **Coverage:** Covered as an opt-in plan. `pnputil /export-driver` and `dism /Add-Driver` run only with `MAGIC_INJECT_RESCUE_DRIVERS=1`.
- **Sources:** https://kbx.macrium.com/macrium-reflect-x/fixing-windows-boot-problems ; https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/add-and-remove-drivers-to-an-offline-windows-image

### P1-34. Ventoy and Secure Boot: MOK enrollment and 0x1A Security Violation on rescue USBs
- **Problem:** Ventoy-based rescue or ISO USBs show "Verification failed: (0x1A) Security Violation" until the user enrolls `ENROLL_THIS_KEY_IN_MOKMANAGER.cer` once.
- **Fix:** if we ever recommend Ventoy or a shim-based Linux plan B, add a step-by-step MOK enrollment page. Prefer our own Microsoft-signed WinPE (P0-4) when Secure Boot is on.
- **Coverage:** Covered. A Ventoy USB with Secure Boot on explains the 0x1A MOK enrollment step and prefers signed WinPE.
- **Sources:** https://ntlite.com/docs/guides/secure-boot-verification-failed/ ; https://github.com/ventoy/Ventoy/discussions/2951

### P1-35. Read the Windows 11 release-health pages for the target version (25H2/26H2)
- **Problem:** Known issues and holds change monthly (for example the 25H2 page now lists issues tied to specific KBs; 26H2 is rolling out).
- **Fix:** have the veille routine fetch the release-health page for the target version weekly and diff it into `data/known_issues.json` (with source URLs). The app reads it offline and shows matching issues in pre-flight.
- **Coverage:** Covered. The app reads release-health data offline.
- **Sources:** https://learn.microsoft.com/en-us/windows/release-health/status-windows-11-25h2 ; https://learn.microsoft.com/en-us/windows/release-health/known-issues/browse/

### P1-36. Check `wimmount.sys` registration before the upgrade (`PreReleaseWimMountDriverFound`)
- **Problem:** SetupDiag has a rule for an unrecognised `wimmount.sys` registration that blocks Setup.
- **Fix:** we register WIMMount in the preventive pack. Add a check of the driver path, version and signature, and map the rule to "re-register the inbox wimmount".
- **Coverage:** Covered. A path that is not the inbox `wimmount.sys` maps to re-registering that driver.
- **Sources:** https://learn.microsoft.com/en-us/windows/deployment/upgrade/setupdiag

---

## P2 — quality, parity and polish

### P2-37. UUP-based ISO build as a download fallback (UUP dump / abbodi converter) — known pitfalls
- **Problem:** When Fido or MCT downloads fail, UUP conversion is the free fallback. Known pitfalls:
  - mixing language ESDs gives duplicate image names;
  - an old wimlib fails on non-ASCII descriptions (fixed in ≥ 1.14.3);
  - driver add-on conversion needs `install.wim`, not `install.esd`.
- **Fix:** if we add a UUP path, use one language only, pin wimlib ≥ 1.14.3, and verify SHA-1/SHA-256 from the UUP file list.
- **Coverage:** Covered as a plan. One language, wimlib 1.14.3 or newer, and install.wim for driver add-ons. No download runs from this check.
- **Sources:** https://github.com/abbodi1406/BatUtil/issues/34 ; https://wimlib.net/forums/viewtopic.php?t=679 ; https://github.com/abbodi1406/BatUtil/issues/63

### P2-38. Debloat or trimmed images (tiny11) break cumulative updates: don't offer component removal
- **Problem:** tiny11 users report CU failures (0x80073712). The "core" variant can't be serviced, because WinSxS is trimmed.
- **Fix:** keep the project free of image trimming. Detect a trimmed or serviced image (a WinSxS missing component store, `DISM /CheckHealth` fails) and advise a repair upgrade (P1-21) rather than more fixes.
- **Coverage:** N/A (detection Missing).
- **Sources:** https://github.com/ntdevlabs/tiny11builder/issues/347 ; https://github.com/ntdevlabs/tiny11builder/issues/22

### P2-39. AveYo IFEO-on-SetupHost approach as a Windows Update–native bypass, with clean removal
- **Problem:** AveYo's `Skip_TPM_Check_on_Dynamic_Update.cmd` intercepts `SetupHost.exe` via Image File Execution Options so that Windows Update feature updates go through `/Product Server`. It is toggleable: running it again removes everything.
- **Fix:** as an optional "keep receiving feature updates via WU" switch, implement an equivalent with an explicit uninstall entry, and log the IFEO key in reports. Some AV products flag IFEO, so keep it opt-in and documented in FALSE_POSITIVES.md.
- **Coverage:** Missing (no IFEO).
- **Sources:** https://github.com/AveYo/MediaCreationTool.bat/blob/main/bypass11/readme.md ; https://github.com/AveYo/MediaCreationTool.bat/blob/main/bypass11/Skip_TPM_Check_on_Dynamic_Update.cmd

### P2-40. MDL "Boot and Upgrade FiX KiT" option catalogue as a parity checklist
- **Problem:** The kit's options are good parity targets:
  - UFWS / Server setup.cfg;
  - `boot.wim` registry plus winsetup.dll;
  - an "AIO 2b" mode that prepares the current OS for a 24H2+ in-place upgrade;
  - an appraiserres replacement;
  - EI.CFG;
  - a diskpart / apply-image helper.
- **Fix:** add a doc table mapping each option to our module (most exist) and keep it in the veille routine.
- **Coverage:** Partial.
- **Sources:** https://forums.mydigitallife.net/threads/win-11-boot-and-upgrade-fix-kit-v6-1f2-released.83724/ ; https://github.com/calliefelis/win-11-boot-and-upgrade-fix-kit-mod

### P2-41. Rescue media in matching firmware mode, plus a CSM/UEFI hint
- **Problem:** Macrium and others stress booting rescue media in the same mode as the installation (CSM for MBR/BIOS, UEFI for GPT), or the repair writes the wrong boot files.
- **Fix:** have the RAM rescue / USB builder print "Démarrez cette clé en mode UEFI (entrée 'UEFI: …')" based on the detected OS firmware. Have the rescue scripts refuse `/f UEFI` when booted in CSM (`bcdedit` / `PEFirmwareType` check).
- **Coverage:** Partial.
- **Sources:** https://kbx.macrium.com/macrium-reflect-x/fixing-windows-boot-problems ; https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/boot-to-uefi-mode-or-legacy-bios-mode

### P2-42. Post-upgrade cleanup guard: keep Windows.old until the user confirms
- **Problem:** Storage Sense and Disk Cleanup can delete Windows.old early, which kills rollback. This pairs with P1-22.
- **Fix:** after success, show a "Garder la restauration 30 jours" toggle. Record the expiry date in the state file, and remind the user at expiry.
- **Coverage:** Covered. The same opt-in records the Windows.old expiry and warns before Disk Cleanup.
- **Sources:** https://support.microsoft.com/en-us/windows/deployment/install-upgrade/go-back-to-the-previous-version-of-windows ; https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/dism-uninstallos-command-line-options?view=windows-11

### P2-43. Rufus `/auto upgrade` argv and FoD dismissable-block tests in CI
- **Problem:** In `/quiet` mode, dismissable app or FoD messages become blocks unless `/compat ignorewarning` is set (SetupDiag rules). Rufus #2752 shows the wrapper argv can regress.
- **Fix:** add unit tests asserting that the final Setup argv always contains `/compat ignorewarning` when `/quiet` is present, and never contains both `/noreboot` and the auto-reboot logic.
- **Coverage:** Partial.
- **Sources:** https://learn.microsoft.com/en-us/windows/deployment/upgrade/setupdiag ; https://github.com/pbatard/rufus/issues/2752

### P2-44. GRUB-side guidance pack in reports (os-prober, efibootmgr, refind `--usedefault`)
- **Problem:** After Windows touches the ESP, Linux users need the order restored (`efibootmgr -o`). Windows may vanish from GRUB because of `GRUB_DISABLE_OS_PROBER`. rEFInd suggests the `--usedefault` fallback, or `bcdedit /set {bootmgr} path \EFI\refind\refind_x64.efi`.
- **Fix:** when Linux EFI trees are detected, append a short, sourced "Linux side" section to the user report and the GitHub issue. Don't run `bcdedit /set {bootmgr} path` ourselves; it is user-opt-in only.
- **Coverage:** Partial. A Linux-side note is added when EFI trees are restored. `bcdedit /set {bootmgr} path` stays opt-in and is not run.
- **Sources:** https://www.rodsbooks.com/refind/bootcoup.html ; https://unix.stackexchange.com/questions/758353/resolving-dual-boot-issue-windows-not-showing-in-grub-menu ; https://www.kali.org/docs/troubleshooting/dual-boot/

### P2-45. System Reserved (MBR) enlarge path: document the "recreate from Setup" fallback **[SAFETY]**
- **Problem:** For a 100 MB MBR System Reserved partition, the community fix is to shrink C: by about 500 MB and extend SRP (only possible if adjacent), or to recreate a 500 MB active NTFS partition and run `bcdboot C:\Windows /s V: /f BIOS`.
- **Fix:** our `create_larger_system_reserved_mbr` exists and is opt-in. Make sure it checks adjacency, snapshots `\Boot` + `bootmgr` first, and verifies with `bcdedit /store V:\Boot\BCD /enum` before deactivating the old SRP.
- **Coverage:** Covered (opt-in); add the verification steps if missing.
- **Sources:** https://www.techietape.com/2026/03/how-to-resize-recreate-mbr-system-reserved-partition.html ; https://technine.be/2022/01/11/windows-11-upgrade-fails-with-we-couldnt-update-system-reserved-partition-fix/

### P2-46. Unsupported-PC update reality: monthly CUs yes, feature updates via ISO, and POPCNT hard stop
- **Problem:** Community threads confirm that bypass-installed PCs get monthly CUs but not feature updates via Windows Update, unless MoSetup or IFEO tricks are used. CPUs without POPCNT/SSE4.2 can't run 24H2+ at all.
- **Fix:** already a hard stop (Win10 22H2 keep-apps). Add a GUI explainer: "Les mises à jour mensuelles continueront ; la prochaine version annuelle passera par l'app (One-Click)". Schedule a yearly check through auto-update.
- **Coverage:** Covered. The hard stop remains. The disclaimer tells the user that monthly updates may continue and the next yearly version is not guaranteed.
- **Sources:** https://www.reddit.com/r/Windows11/comments/1in9u0s/how_would_you_install_major_updates_later_on_like/ ; https://github.com/builtbybel/FlyOOBE/issues/482 ; https://github.com/pbatard/rufus/issues/2568

---

## Quick coverage summary
| Status | Items |
|---|---|
| Missing | P1-14, P1-22, P1-29, P1-30, P1-33, P1-34, P1-35, P2-37, P2-39, P2-42, P2-44 |
| Partial | P1-15…P1-21, P1-23…P1-28, P1-31, P1-32, P1-36, P2-40, P2-41, P2-43, P2-46 |
| Covered | P0-1, P0-2, P0-3, P0-4, P0-5, P0-6, P0-7, P0-8, P0-9, P0-10, P0-11, P0-12, P0-13 |
| Covered (verify only) | P2-45, keys of P1-20 |

## Notes on method
- The repo was read at `e18bebb`. Coverage greps used rule names, registry keys and command switches. "Partial" means the claim was checked against specific files and lines cited in each item.
- Some URLs return 403 to scripted fetches (Dell, MDL, Neowin, StackExchange). They came from live search results this session and open in a browser.
