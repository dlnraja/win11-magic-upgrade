# Download help — Chrome / SmartScreen / antivirus false positives

**Publisher:** [dlnraja](https://github.com/dlnraja)  
**Official builds only:** [GitHub Releases](https://github.com/dlnraja/win11-magic-upgrade/releases/latest)

Win11 Magic Upgrade is **open source (MIT)**, **not malware**, and contains **no PDF/trojan payloads**.  
Chrome Safe Browsing and Windows SmartScreen often flag **new or self-signed PyInstaller EXEs** until reputation builds.

## Prefer the ZIP (recommended)

On the [latest release](https://github.com/dlnraja/win11-magic-upgrade/releases/latest) page, download:

1. **`Win11MagicUpgrade-Portable-vX.Y.Z.zip`** ← use this in Chrome  
2. Optionally verify **`SHA256SUMS.txt`**
3. Extract, then run `Win11MagicUpgrade.exe` as Administrator

Chrome blocks **naked `.exe`** downloads much more often than **`.zip`** from GitHub.

## If Chrome still says “virus detected”

1. Open `chrome://downloads` → **Keep** / **Keep anyway**  
2. Or download with **Edge / Firefox**, or:

```bash
curl -L -o Win11MagicUpgrade-Portable.zip "https://github.com/dlnraja/win11-magic-upgrade/releases/latest/download/Win11MagicUpgrade-Portable-vX.Y.Z.zip"
```

3. Unblock the file (PowerShell):

```powershell
Unblock-File .\Win11MagicUpgrade-Portable-vX.Y.Z.zip
# after extract:
Unblock-File .\Win11MagicUpgrade.exe
```

4. Or: right-click EXE → **Properties** → check **Unblock** → OK  

5. Report a false positive to Google (helps future downloads):  
   https://safebrowsing.google.com/safebrowsing/report_error/

## SmartScreen: “Windows protected your PC”

**More info** → **Run anyway**.  
A paid **OV/EV Authenticode certificate** (repo secrets `CODESIGN_PFX_BASE64`) removes most of these prompts as reputation accumulates.

## Verify integrity

```powershell
Get-FileHash .\Win11MagicUpgrade-Portable-vX.Y.Z.zip -Algorithm SHA256
# Compare to the matching line in SHA256SUMS.txt on the same Release
```

## Antivirus quarantine

See [SECURITY.md](../SECURITY.md) — Defender exclusions, VirusTotal / Kaspersky FP helpers (`--cli --declare-av`).
