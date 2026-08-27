# Trusted Authenticode certificate (SmartScreen)

**Publisher:** `dlnraja`  
Goal: Windows SmartScreen shows a **known publisher** instead of “Windows protected your PC”.

Self-signed builds (default CI without secrets) are still signed, but **not** SmartScreen-trusted.  
You need a real **OV or EV code-signing** `.pfx` from a CA (DigiCert, Sectigo, SSL.com, GlobalSign, …).

## Local (recommended env vars)

```powershell
$env:MAGIC_CODESIGN_PFX = "C:\path\to\your-codesign.pfx"
$env:MAGIC_CODESIGN_PASSWORD = "your-pfx-password"
powershell -NoProfile -ExecutionPolicy Bypass -File .\build\build_exe.ps1
```

Validate first:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build\setup_codesign.ps1 `
  -PfxPath "C:\path\to\your-codesign.pfx" `
  -Password "your-pfx-password" `
  -RequireTrustedChain
```

Persist for your user account:

```powershell
powershell -File .\build\setup_codesign.ps1 -PfxPath "...\cert.pfx" -Password "***" -SetUserEnv
```

Aliases also accepted: `CODESIGN_PFX` / `CODESIGN_PASSWORD`.

## GitHub Actions (Release + CI)

Never commit the `.pfx`. Store secrets:

| Secret | Value |
|--------|--------|
| `CODESIGN_PFX_BASE64` | Base64 of the `.pfx` file |
| `CODESIGN_PASSWORD` | PFX password |

Generate base64 safely:

```powershell
powershell -File .\build\setup_codesign.ps1 -PfxPath "...\cert.pfx" -Password "***" -ExportBase64
```

Then: GitHub → **Settings → Secrets and variables → Actions** → New repository secret.

Optional strict gate on Release (fails if only self-signed):

- Secret / env `MAGIC_REQUIRE_TRUSTED_CODESIGN=1`

Aliases: `MAGIC_CODESIGN_PFX_BASE64` / `MAGIC_CODESIGN_PASSWORD`.

## What “intelligent” signing does

1. Prefer `MAGIC_CODESIGN_PFX` path if the file exists  
2. Else decode `CODESIGN_PFX_BASE64` in CI  
3. Validate code-signing EKU + private key + expiry  
4. Inspect certificate chain (`smartscreen_ready` in `PUBLISHER.json`)  
5. Sign with multiple timestamp servers (DigiCert / Sectigo / GlobalSign / Apple)  
6. Fall back to self-signed only when no PFX is configured  

Artifacts after build:

- `dist/PUBLISHER.txt` — human summary (`SmartScreenReady: True/False`)  
- `dist/PUBLISHER.json` — machine fields (`smartscreen_ready`, `mode`, `issuer`)

## After you install a trusted PFX

1. Rebuild / re-tag a release so CI signs with the CA cert  
2. Prefer the **ZIP** download for Chrome ([DOWNLOAD.md](DOWNLOAD.md))  
3. Reputation still builds over downloads — EV is fastest for SmartScreen  

## Buy a certificate

Search for **“OV code signing certificate”** or **“EV code signing”** from a public CA.  
EV usually requires hardware token / attestation; OV is often file-based `.pfx` (policies change — follow your CA).
