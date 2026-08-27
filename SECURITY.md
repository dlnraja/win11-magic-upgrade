# Security Policy

## Supported versions

Fixes are applied on the `main` branch of this repository.

## Reporting a vulnerability

Please open a **private** security advisory on GitHub if available, or email the maintainer via the GitHub profile. Do not file public issues for exploitable local-privilege concerns in the upgrader itself.

## Scope notes

This tool intentionally bypasses Windows 11 *setup* hardware checks using documented/community methods (`/product server`, registry). It does not claim to be a security product. Running it requires Administrator rights and modifies system configuration.

## Antivirus false positives (Kaspersky / Defender)

Win11 Magic Upgrade is **not** malware, **not** a PDF trojan, and contains **no PDF exploit code**.

Heuristic engines sometimes flag unsigned PyInstaller onefile EXEs (especially with UPX or `mshta` elevation) as `Trojan.PDF` / generic HEUR.

Mitigations shipped in the build:

- **UPX disabled**
- **UAC application manifest** (`requireAdministrator`) — no `mshta` JavaScript
- **Version / ProductName** resource identifying the legitimate upgrader
- At runtime, **autonomous trust declarations**:
  - Windows Defender path exclusions
  - Local Kaspersky trusted-app attempts + FP declaration files
  - **VirusTotal**: upload + harmless vote + FP comment (needs `MAGIC_VT_API_KEY` or `av_keys.json`)
  - **Kaspersky OpenTIP**: sample upload (needs `MAGIC_KASPERSKY_OPENTIP_KEY`) + email draft to `newvirus@kaspersky.com`

Keys file: `%LOCALAPPDATA%\Win11MagicUpgrade\av_keys.json`

```json
{
  "virustotal": "YOUR_VT_API_KEY",
  "kaspersky_opentip": "YOUR_OPENTIP_TOKEN"
}
```

One-shot: `Win11MagicUpgrade.exe --cli --declare-av`

If Kaspersky still quarantines the EXE:

1. Restore the file from Quarantine and add it to **Trusted applications**
2. Send the package under `%LOCALAPPDATA%\Win11MagicUpgrade\fp_submissions\` to `newvirus@kaspersky.com` (password `infected` if re-zipping)
3. Prefer a **code-signed** release build when a certificate is available

## Autonomous diagnostics (privacy)

On hard failures (e.g. ESP/SRP cannot continue), the app may open a GitHub **Issue**
(and optionally a draft **PR** with `MAGIC_GH_DIAG_PR=1`) containing only scrubbed data:

- No usernames, hostnames, emails, SIDs, MACs, IPs, product keys, or `C:\Users\…` paths
- Allow-listed hardware/OS facts + sanitized setup log tails
- Local copy: `%LOCALAPPDATA%\Win11MagicUpgrade\autodiag\`
- Auth via logged-in `gh`, or `MAGIC_GITHUB_TOKEN` / `GITHUB_TOKEN` (never baked into the EXE)
- Browser fallback opens a pre-filled issues form with the same scrubbed body

Set `MAGIC_GH_REPO` to override the target repository (default `dlnraja/win11-magic-upgrade`).
