# Repair before reboot

Use this when a run has already mounted an ESP, deleted font files, shrunk C:, or created a second EFI partition, and the PC has not been restarted.

```text
Win11MagicUpgrade.exe --cli --repair-before-reboot
```

The command reads `%LOCALAPPDATA%\Win11MagicUpgrade` first: session logs, Panther `setupact.log` / `setuperr.log`, `bcd-backups\bcd-export-*` (including an export logged at a clock time such as 18:38:05), `partition-backups\<stamp>\bcd-export`, and the ESP file copy `EFI\Microsoft\Boot\BCD`. It also runs `bcdedit /enum all` and `bcdedit /enum firmware`, each with a timeout.

It then:

- inventories every BCD backup with its timestamp
- validates each with `bcdedit /store <file> /enum all`
- diffs those stores against the current store and the store on each ESP
- selects the latest known-good backup taken before the first `bcdboot` / BFSVC rewrite
- exports the current store, then `bcdedit /import` of that backup when the current store is broken or was rewritten
- checks `{bootmgr}` device, `{default}` device / osdevice / path `winload.efi`, and systemroot
- copies deleted `EFI\Microsoft\Boot\Fonts` back from the partition backup
- removes leftover letters this app assigned
- leaves every other ESP in place

It does not delete a partition, shrink C:, or create an ESP. The redacted zip and `SUMMARY.txt` are written under `%LOCALAPPDATA%\Win11MagicUpgrade\repair-before-reboot\<stamp>\`. Reboot only when the summary says **GO**.

One-Click no longer shrinks C: or deletes ESP files unless `MAGIC_ESP_EXPAND=1` or `MAGIC_ESP_FONT_CLEANUP=1` is set.
