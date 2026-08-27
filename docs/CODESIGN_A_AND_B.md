# Dual path: Option A (paid OV/EV .pfx) + Option B (free SignPath Foundation)

You asked for **both**. The Release workflow now supports **A and B together**:

1. **Option A** — sign during `build_exe.ps1` with `CODESIGN_PFX_BASE64`
2. **Option B** — SignPath re-signs the EXE (final SmartScreen / Foundation cert) when SignPath secrets/vars are set

Optional: set repo variable `MAGIC_SIGNPATH_SKIP_IF_PFX=1` to use **A only** and skip SignPath.

## Current machine / repo status

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build\setup_codesign_both.ps1
```

As of setup: **no `.pfx` on this PC**, **no GitHub signing secrets yet**.

---

## Option A — buy + upload your `.pfx`

1. Buy an **OV** (or EV) **code signing** certificate (examples):
   - https://www.ssl.com/certificates/code-signing/
   - https://sectigo.com/ssl-certificates-tls/code-signing
   - https://www.digicert.com/signing/code-signing-certificates
2. Complete CA identity validation (company/person). Export / receive a `.pfx` + password.
3. Upload to GitHub with your `dlnraja` login:

```powershell
gh auth login
powershell -NoProfile -ExecutionPolicy Bypass -File .\build\upload_codesign_github.ps1 `
  -PfxPath "C:\path\to\your-codesign.pfx" `
  -Password "your-pfx-password" `
  -RequireTrustedChain
```

Secrets set: `CODESIGN_PFX_BASE64`, `CODESIGN_PASSWORD`.

---

## Option B — SignPath Foundation (free OSS)

Already prepared:

- Email sent to `support@signpath.io`
- Policy: [CODE_SIGNING_POLICY.md](CODE_SIGNING_POLICY.md)
- Apply pack: [SIGNPATH_APPLICATION.md](SIGNPATH_APPLICATION.md)
- Artifact XML: `.signpath/artifact-configurations/default.xml`

Still required from you:

1. Submit https://signpath.org/apply.html (HubSpot + reCAPTCHA)
2. After approval, create API token + project `win11-magic-upgrade` / policy `release-signing`
3. Push secrets:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build\setup_signpath_github.ps1 `
  -ApiToken "..." `
  -OrganizationId "..." `
  -ProjectSlug "win11-magic-upgrade" `
  -SigningPolicySlug "release-signing"
```

---

## When both are ready

Tag any new `v*` release. CI:

1. Builds + signs with **A** (if present)
2. Re-signs with **B** (if present) → published EXE / ZIP

Tell the agent: **« PFX prêt: C:\...\file.pfx »** and/or **« SignPath OK »** with org id + token (or run the scripts yourself).
