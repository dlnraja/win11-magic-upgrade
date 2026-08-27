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

For a **known publisher** signature (real fix), configure a trusted OV/EV `.pfx` or free SignPath OSS (GitHub-linked) — see [CODESIGN.md](CODESIGN.md).

```powershell
# Own PFX -> upload with your GitHub account
.\build\upload_codesign_github.ps1 -PfxPath "C:\path\to\codesign.pfx" -Password "***" -RequireTrustedChain

# Or local only:
$env:MAGIC_CODESIGN_PFX = "C:\path\to\codesign.pfx"
$env:MAGIC_CODESIGN_PASSWORD = "your-password"
```

Full guide: [CODESIGN.md](CODESIGN.md) (`upload_codesign_github.ps1`, SignPath, secrets `CODESIGN_PFX_BASE64`).

## Verify integrity

```powershell
Get-FileHash .\Win11MagicUpgrade-Portable-vX.Y.Z.zip -Algorithm SHA256
# Compare to the matching line in SHA256SUMS.txt on the same Release
```

## Antivirus quarantine

See [SECURITY.md](../SECURITY.md) — Defender exclusions, **Kaspersky KIS** trusted-app bypass, VirusTotal / OpenTIP (`--cli --declare-av`).

### Kaspersky Internet Security (KIS) — Trojan.PDF / HEUR

If KIS quarantines `Win11MagicUpgrade.exe`:

1. **Prefer the Release ZIP** (not the naked EXE) — see above.
2. Extract, then run **`Fix-KIS.cmd` as Administrator** (works even if the EXE is blocked).
3. On first run the app writes **`Win11MagicUpgrade-KIS-WHITELIST.txt`** on your Desktop with step-by-step KIS GUI steps.
4. **Restore from Quarantine** → add as **Trusted application** (Settings → Threats and Exclusions).
5. In the GUI: **Fix AV / KIS**, or one-shot: `Win11MagicUpgrade.exe --cli --declare-av`
6. Disable early auto-trust if needed: `MAGIC_EARLY_AV_TRUST=0` or `MAGIC_KIS_TRUST=0`

The app auto-attempts `avp.com ADD`, trusted-zone SET, and `Unblock-File` when KIS is installed (admin helps).
