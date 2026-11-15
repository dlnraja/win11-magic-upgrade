# RAM rescue

Windows already includes a signed recovery image, `Winre.wim`. The rescue plan boots that image from a RAM disk. It does not rewrite the image.

## What gets set

`plan_rescue_environment` writes `EFI\MagicUpgrade\rescue.cmd` and `winpeshl.ini` on the ESP, and appends `EFI\MagicUpgrade\rescue.log`. The BCD plan is:

- `{ramdiskoptions}` stays the existing Windows RAM-disk description
- `{current}` `recoveryenabled` is `Yes`
- `{current}` `recoverysequence` points at the existing recovery loader
- `bootstatuspolicy` is `DisplayAllFailures`, so failed boots are not hidden
- `/bootsequence` adds a one-time boot of that loader

With recovery enabled, Windows Boot Manager offers that recovery loader after failed starts. `IgnoreAllFailures` is not used here, because it hides those failures.

The script on the ESP records the same repair order as the running app: classify the failure, try EFI or BIOS regeneration, verify, and roll back. The attempt counter is the same `post-op-regen.json` budget. Two arms are stored in `EFI\MagicUpgrade\rescue-arms.json`. A third arm is **NO-GO**.

## Signed image

`MAGIC_WINRE_INJECT` is off by default, so `winpeshl.ini` is not copied into `Winre.wim`. Turning it on marks the plan as no longer preserving the signed image. Leave it off when Secure Boot is on.

## Optional Linux plan B

`MAGIC_LINUX_RESCUE=1` only writes a plan. Nothing is downloaded and no disk is written. The plan is refused while Secure Boot is on, because a Linux ramdisk is not the signed Windows boot path.

When it is allowed:

1. Keep the existing OpenDuet or GRUB entry. Do not replace Windows Boot Manager.
2. Chain-load a GPL-2.0-or-later or MIT ramdisk from that entry.
3. Do not use a paid rescue image.
4. If Secure Boot must stay on, use the signed WinRE path instead and enroll no extra key.

The WinRE script itself tries `bcdboot C:\Windows /f UEFI` first and `bcdboot C:\Windows /f BIOS` only when that copy fails. It does not call `bootrec /fixboot`. Copying the script into `Winre.wim` still requires `MAGIC_WINRE_INJECT=1`.

## Sources

- [REAgentC command-line options](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/reagentc-command-line-options?view=windows-11)
- [BCDBoot command-line options](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/bcdboot-command-line-options-techref-di?view=windows-11)
- [Windows startup issues troubleshooting](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/windows-boot-issues-troubleshooting)
