# Antivirus false positives

Win11 Magic Upgrade is **not malware**. A few engines heuristically flag the PyInstaller onefile EXE (especially with UAC + self-sign / `invalid-signature` metadata).

## Typical detections (community / VirusTotal)

| Vendor | Label | Nature |
|--------|--------|--------|
| Kaspersky | `HEUR:Trojan.Python.Agent.gen` | Heuristic on PyInstaller Python runtime |
| Bkav Pro | `W32.Malware.*` | Generic heuristic |
| SecureAge | `Malicious` | Static ML + invalid/self signature |

Usually **~3/70** vendors; Microsoft, ESET, Bitdefender, Malwarebytes, etc. stay clean.

## What we do in CI / releases

1. **No UPX**, UAC manifest, strong version resource (`dlnraja`, “NOT malware”)
2. Prefer **ZIP** downloads from GitHub Releases (Chrome/SmartScreen softer)
3. Authenticode when PFX / SignPath configured ([CODESIGN.md](CODESIGN.md))
4. Release job: VirusTotal upload + **harmless** vote + FP comment; Kaspersky OpenTIP sample
5. Vendor email drafts under `%LOCALAPPDATA%\Win11MagicUpgrade\fp_submissions\`

## Report a specific VirusTotal hash

```powershell
.\build\submit_fp_hash.ps1 -Sha256 a3fb187f11a7cb327f9e0d7b4eaa829244661e99b467501f2940906bd7d795dd
```

Requires `MAGIC_VT_API_KEY` or `av_keys.json`.

Portals:

- VirusTotal file page: `https://www.virustotal.com/gui/file/<sha256>`
- Kaspersky OpenTIP: `https://opentip.kaspersky.com/<sha256>/results`
- Kaspersky email: `newvirus@kaspersky.com`
- Bkav: https://www.bkav.com/en/false-positive
- SecureAge: https://www.secureage.com/false-positive

## User PC (Kaspersky quarantine)

1. Restore from Quarantine → add to Trusted applications  
2. Or run `Fix-KIS.cmd` from the portable ZIP  
3. Prefer a **code-signed** release when available  

See [SECURITY.md](../SECURITY.md) and [DOWNLOAD.md](DOWNLOAD.md).
