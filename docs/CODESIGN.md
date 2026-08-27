# Trusted Authenticode certificate (SmartScreen)

**Publisher:** `dlnraja`  
Goal: Windows SmartScreen shows a **known publisher** instead of “Windows protected your PC”.

## Important: GitHub alone cannot trust an EXE

Your GitHub account (`dlnraja`) is used to:

1. Build + publish Releases  
2. Store signing secrets (`gh secret set`)  
3. Optionally link **SignPath Foundation** (free OV for open source)

It does **not** issue a Windows Authenticode certificate.  
Self-signed `CN=dlnraja` (current default without secrets) is signed, but **SmartScreen still warns**.

You need **Option A**, **Option B**, or **both** (recommended dual path):

| Path | Cost | How it uses GitHub |
|------|------|--------------------|
| **A)** Own OV/EV `.pfx` | Paid CA | Secrets on this repo; CI signs |
| **B)** [SignPath Foundation](https://signpath.org/) | Free for OSS | Sign up with GitHub; CI submits EXE to SignPath |
| **A+B** | Both | A signs the build, B re-signs the release EXE (final SmartScreen cert) |

Full checklist: [CODESIGN_A_AND_B.md](CODESIGN_A_AND_B.md) · status script: `build/setup_codesign_both.ps1`

## A) Own `.pfx` → upload with your GitHub account

```powershell
gh auth login
powershell -NoProfile -ExecutionPolicy Bypass -File .\build\upload_codesign_github.ps1 `
  -PfxPath "C:\path\to\your-codesign.pfx" `
  -Password "your-pfx-password" `
  -RequireTrustedChain
```

This sets repo secrets `CODESIGN_PFX_BASE64` + `CODESIGN_PASSWORD` via `gh` (your login).  
Then tag a new release so Actions signs with that PFX.

### Local build (without uploading)

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

Manual secrets (Settings → Secrets → Actions):

| Secret | Value |
|--------|--------|
| `CODESIGN_PFX_BASE64` | Base64 of the `.pfx` file |
| `CODESIGN_PASSWORD` | PFX password |

```powershell
powershell -File .\build\setup_codesign.ps1 -PfxPath "...\cert.pfx" -Password "***" -ExportBase64
```

Optional strict gate: `MAGIC_REQUIRE_TRUSTED_CODESIGN=1`  
Aliases: `MAGIC_CODESIGN_PFX_BASE64` / `MAGIC_CODESIGN_PASSWORD`.

## B) Free SignPath (recommended if you have no `.pfx`)

**Application pack (filled for this repo):** [SIGNPATH_APPLICATION.md](SIGNPATH_APPLICATION.md)  
**Required policy page:** [CODE_SIGNING_POLICY.md](CODE_SIGNING_POLICY.md)  
**Artifact XML:** [`.signpath/artifact-configurations/default.xml`](../.signpath/artifact-configurations/default.xml)

1. Open https://signpath.org/apply.html and sign in / apply with **GitHub `dlnraja`**
2. Paste fields from [SIGNPATH_APPLICATION.md](SIGNPATH_APPLICATION.md) (repo URL, MIT, description)
3. Wait for **Open Source / Foundation** approval (often days)
4. In SignPath: create project slug `win11-magic-upgrade`, policy `release-signing`, link GitHub trusted build system + this repo
5. Create an API token, then push secrets:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build\setup_signpath_github.ps1 `
  -ApiToken "YOUR_SIGNPATH_API_TOKEN" `
  -OrganizationId "YOUR-ORG-UUID" `
  -ProjectSlug "win11-magic-upgrade" `
  -SigningPolicySlug "release-signing"
```

| Kind | Name | Source |
|------|------|--------|
| Secret | `SIGNPATH_API_TOKEN` | SignPath → API Tokens |
| Variable | `SIGNPATH_ORGANIZATION_ID` | SignPath org (UUID in URL / org settings) |
| Variable | `SIGNPATH_PROJECT_SLUG` | e.g. `win11-magic-upgrade` |
| Variable | `SIGNPATH_SIGNING_POLICY_SLUG` | e.g. `release-signing` |

Release workflow will submit `Win11MagicUpgrade.exe` to SignPath after the build.
If Option A PFX is also set, SignPath **re-signs** the already-signed EXE (final Foundation / SmartScreen path), unless repo variable `MAGIC_SIGNPATH_SKIP_IF_PFX=1`.

6. Tag a new `v*` release so CI signs with the Foundation certificate.
## What “intelligent” signing does

1. Prefer `MAGIC_CODESIGN_PFX` path if the file exists  
2. Else decode `CODESIGN_PFX_BASE64` in CI  
3. Validate code-signing EKU + private key + expiry  
4. Inspect certificate chain (`smartscreen_ready` in `PUBLISHER.json`)  
5. Sign with multiple timestamp servers (DigiCert / Sectigo / GlobalSign / Apple)  
6. Optional SignPath re-sign for OSS (CA-trusted)  
7. Fall back to self-signed only when no PFX / SignPath is configured  

Artifacts after build:

- `dist/PUBLISHER.txt` — human summary (`SmartScreenReady: True/False`)  
- `dist/PUBLISHER.json` — machine fields (`smartscreen_ready`, `mode`, `issuer`)

## After trusted signing is configured

1. Rebuild / re-tag a release so CI signs with the CA cert  
2. Prefer the **ZIP** download for Chrome ([DOWNLOAD.md](DOWNLOAD.md))  
3. Reputation still builds over downloads — EV / established OV is fastest for SmartScreen  

## Buy a certificate

Search for **“OV code signing certificate”** or **“EV code signing”** from a public CA.  
EV usually requires hardware token / attestation; OV is often file-based `.pfx` (policies change — follow your CA).
