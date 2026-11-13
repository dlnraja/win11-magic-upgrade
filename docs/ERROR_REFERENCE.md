# Error reference

Machine-readable entries live in `data/error_catalog.json`. The engine loads that file from `python/engine/error_catalog.py`. A log line or Setup exit code matches the longest pattern. The matching entry supplies the cause and the repair order.

Sources are Microsoft Learn pages. The notes below are short pointers to those pages, not a copy of them.

## Diskpart

`assign` and `remove` act on the volume that has focus. `active` marks the MBR partition that has focus. `rescan` locates disks. A letter is `assign letter=S`, with no colon. The English and French argument errors are matched so a bad script stops and the next access method runs.

Source: [diskpart](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/diskpart). The UEFI `device` example is on [BCD system store settings for UEFI](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/bcd-system-store-settings-for-uefi?view=windows-11).

## bcdboot, bcdedit, bootsect, bootrec

`bcdboot C:\Windows /s S: /f UEFI` copies the UEFI boot files. `/f BIOS` copies the BIOS boot files. `/f ALL` copies both, and `/f` requires `/s`. `/l` sets the locale. `/c` is the documented switch that does not preserve existing BCD elements.

`bcdedit /export` then `/import` restores a system store. `/createstore` creates an empty store that is not the system store. `device` and `osdevice` use `partition=<letter>:`.

`bootsect /nt60 S: /mbr` writes Bootmgr-compatible boot code. `bootrec /fixmbr` rewrites only the master boot code and does not repair a damaged partition table. `bootrec /rebuildbcd` follows an export of the existing store.

Sources: [BCDBoot](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/bcdboot-command-line-options-techref-di?view=windows-11), [bcdedit](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/bcdedit), [bootsect](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/bootsect-command-line-options?view=windows-11), [Windows startup issues](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/windows-boot-issues-troubleshooting).

## Setup codes

`0xC1900101` is a generic rollback and usually means a driver. The extend code says which phase failed. `0xC1900101-0x20017` is documented as a driver, hardware, or third-party software failure during the upgrade boot phase. `0xC1900200` means the computer does not meet the minimum requirements. `0xC1900107` means a previous setup cleanup still needs a restart. `0x80070070` means there is not enough free disk space. `0x80070002` with extend `0x20009` can mean a second system disk was left connected.

Sources: [resolution procedures](https://learn.microsoft.com/en-us/troubleshoot/windows-client/setup-upgrade-and-drivers/windows-10-upgrade-resolution-procedures), [0xC1900101-0x20017](https://learn.microsoft.com/en-us/troubleshoot/windows-client/setup-upgrade-and-drivers/upgrade-fails-with-error-0xc1900101-0x20017), [result and extend codes](https://learn.microsoft.com/en-us/troubleshoot/windows-client/setup-upgrade-and-drivers/windows-10-upgrade-error-codes), [log files](https://learn.microsoft.com/en-us/windows/deployment/upgrade/log-files).

## Boot stop codes

Status `0xc000000e` means the selected boot device is inaccessible. The documented correction is to set `{default}` device and osdevice, and `{bootmgr}` device, to a real partition. Stop `INACCESSIBLE_BOOT_DEVICE` (`0x7B`) is covered by the startup troubleshooting article, including a pending-update path that uses `DISM /Image:<windows> /Cleanup-Image /RevertPendingActions` only after `/get-packages`.

Source: [0xc000000e](https://learn.microsoft.com/en-us/troubleshoot/windows-server/performance/windows-fail-to-start-status-0xc000000e), [startup issues](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/windows-boot-issues-troubleshooting).

`0xc0000225`, `0xc000000f`, and `0xc0000098` are matched as boot-file or BCD failures and use the same `bcdboot` and `/import` order. The command syntax comes from the BCDBoot page above. This catalog does not claim a separate Microsoft article for each of those three statuses.

## Secure Boot

Windows 11 boot requires the Windows UEFI CA 2023 in `db`. `dbx` is checked before `db`. Event 1801 and `UEFICA2023` mark a device that still needs the 2023 certificates. Event 1808 is the completion signal described in Microsoft Q&A and the certificate-update article. SBAT and `dbx` failures, including event 1796, do not justify clearing `dbx`.

Sources: [key guidance](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/windows-secure-boot-key-creation-and-management-guidance?view=windows-11), [certificate update](https://learn.microsoft.com/en-us/troubleshoot/windows-client/windows-security/update-secure-boot-certificates), [2023 CA transition](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/linux-distribution-guidance-handling-2023keys?view=windows-11).

## Free and open tools

No source from these projects is copied into this repository. The list records what each one does, the license, and which repair name in `data/error_catalog.json` the idea maps to.

| Tool | License | What we took from it |
| --- | --- | --- |
| Startup Repair, bootrec, bcdboot, bcdedit, bootsect, DISM, SFC, reagentc | Microsoft Windows | The command order in the catalog. SFC offline uses `/offbootdir` and `/offwindir` only after the Windows letter is known. |
| SetupDiag | Microsoft, included with Setup | Read the logs before changing the disk. [SetupDiag](https://learn.microsoft.com/en-us/windows/deployment/upgrade/setupdiag) |
| Media Creation Tool | Microsoft, free download, not open source | Official media only. Not redistributed. |
| Boot-Repair | GPL-3.0-only | Back up first and write a diagnosis. [Launchpad](https://launchpad.net/boot-repair) |
| rEFInd | GPL-3.0-only for the rEFInd changes | Scan the ESP for loaders before replacing `bootmgfw.efi`. [Features](https://www.rodsbooks.com/refind/features.html) |
| OpenCore | BSD-3-Clause | Optional chain already in this project. [OpenCorePkg](https://github.com/acidanthera/OpenCorePkg) |
| GRUB | GPL-3.0-or-later | Leave the Linux EFI directory in place. [GRUB](https://www.gnu.org/software/grub/) |
| SystemRescue | GPL-3.0-only | Optional live disk. The rescue plan does not download it. [system-rescue.org](https://www.system-rescue.org/) |
| TestDisk | GPL-2.0-only | Manual partition-table recovery. Not launched automatically. [COPYING](https://github.com/cgsecurity/testdisk/blob/master/COPYING) |
| ms-sys | GPL-2.0-only | Not shipped. Windows `bootsect` is used instead, because ms-sys embeds boot records whose copyright is disputed. [ms-sys](https://ms-sys.sourceforge.net/) and [ms-sys-free](https://sourceforge.net/projects/ms-sys-free/) |
| EasyBCD | NeoSmart freeware, not open source | A menu entry and a one-time boot sequence. No EasyBCD files are included. |
| Rufus | GPL-3.0-only | A USB is bootable only after the boot files verify. [Rufus](https://github.com/pbatard/rufus/) |

## reagentc

`reagentc /info` shows whether Windows RE is enabled. `reagentc /enable` registers the default `Winre.wim` when no other image is set.

Source: [REAgentC](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/reagentc-command-line-options?view=windows-11).
