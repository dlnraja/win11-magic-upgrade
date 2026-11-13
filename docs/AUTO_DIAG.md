# End-of-run auto-diag

At the end of a CLI run, a GUI action, One-Click, or a rescue pass, Win11 Magic Upgrade reads the logs written during that run. A line is kept when it looks like a real fault:

- `[WARN]` or `[ERROR]`, or a Setup-style Warning/Error line
- `invalid`, `introuvable`, `not found`, or `WinError`
- an exit code other than 0
- diskpart argument errors (`arguments non valides`, `arguments are not valid`)
- bcdboot or bcdedit usage and syntax errors
- path and mount failures

Benign lines such as `Error log:` are ignored. If nothing matches, no issue is opened.

## Redaction

The report uses the same placeholder every time for the same value in that report:

| Data | Placeholder |
| --- | --- |
| A path under a user profile | `%USERPROFILE%` |
| Volume GUID | `[VOL-1]`, `[VOL-2]`, … |
| Disk serial | `[SERIAL]` |
| MAC address | `[MAC]` |
| Email | `[EMAIL]` |
| IP address | `[IP]` |
| Product key | `[PRODUCTKEY]` |
| User name or computer name | `[NAME]` |

Each normalized line gets a short SHA-256 signature. The same failure from two computers produces the same signature.

The issue body also includes the app version, Windows build, firmware mode, the step that just finished, a command template with arguments redacted, and the matching error-catalog id when one is known (`bcdboot-123`, `bcdboot-112`, `diskpart-args`, `path-not-found`).

## GitHub

The label is `auto-diag`.

- If an open issue already contains that signature, the new text is added as a comment.
- A new issue is limited to three per 24 hours.
- `MAGIC_SKIP_AUTO_DIAG=1` turns the scan off.
- With no `gh` login and no `MAGIC_GITHUB_TOKEN` or `GITHUB_TOKEN`, the program does not call GitHub. It writes `autodiag/paste-<signature>.md` and a prefilled issue URL you can open yourself.

No token is stored in the program.

## Earlier logs

The same end-of-run pass also reads logs that were not packed before:

- session logs under `%LOCALAPPDATA%\Win11MagicUpgrade\logs`
- `C:\MagicUpgrade\logs`
- state counters (`postflight-loop.json`, `*count*`, boot status files)
- BCD export backup metadata (file name and size only; the hive is not read)
- excerpts from `C:\Windows\Panther\setupact.log` and `setuperr.log`
- excerpts from `C:\$WINDOWS.~BT\Sources\Panther` and `Rollback`
- `C:\Windows\System32\LogFiles\Srt\SrtTrail.txt`

Text is redacted before it is stored. The issue body gets a short summary: one line per new file, one line per signature, cut at 4000 characters. The full redacted text is a zip under `autodiag/bundle-<id>.zip`.

`autodiag/uploaded.json` records the content id of each packed file. A later run does not pack that file again. A changed file is a new id and is packed once.

Set `MAGIC_AUTO_DIAG_GIST=1` when `gh` is logged in to upload that zip as a gist. The command line does not carry a token. Without that setting, the issue text contains the local zip path and the issue link.
