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

### Publisher identity (dlnraja)

- Version resource / LegalCopyright / CompanyName: **dlnraja**
- Authenticode is **automated in GitHub Actions** (Release + CI smoke):
  1. `build/ci_prepare_codesign.ps1` loads secrets or falls back to self-signed `CN=dlnraja`
  2. `build/sign_exe.ps1` signs the EXE (timestamped when possible)
  3. `build/verify_signature.ps1` gates the Release job
- Repo secrets (Settings → Secrets → Actions), optional but recommended for SmartScreen:
  - `CODESIGN_PFX_BASE64` — base64 of your `.pfx` (`[Convert]::ToBase64String([IO.File]::ReadAllBytes('cert.pfx'))`)
  - `CODESIGN_PASSWORD` — PFX password
  - Aliases: `MAGIC_CODESIGN_PFX_BASE64` / `MAGIC_CODESIGN_PASSWORD`
- Local: `MAGIC_CODESIGN_PFX` + `MAGIC_CODESIGN_PASSWORD`, or self-signed via `build/sign_exe.ps1`
- Portable package includes `PUBLISHER.txt` / `PUBLISHER.json`

## Autonomous diagnostics (privacy)

On hard failures (e.g. ESP/SRP cannot continue), the app may open a GitHub **Issue**
(and optionally a draft **PR** with `MAGIC_GH_DIAG_PR=1`) containing only scrubbed data:

- No usernames, hostnames, emails, SIDs, MACs, IPs, product keys, or `C:\Users\…` paths
- Allow-listed hardware/OS facts + sanitized setup log tails
- Local copy: `%LOCALAPPDATA%\Win11MagicUpgrade\autodiag\`
- Auth via logged-in `gh`, or `MAGIC_GITHUB_TOKEN` / `GITHUB_TOKEN` (never baked into the EXE)
- Browser fallback opens a pre-filled issues form with the same scrubbed body

Set `MAGIC_GH_REPO` to override the target repository (default `dlnraja/win11-magic-upgrade`).

Unhandled exceptions (GUI worker, Tk callbacks, CLI, threads) use the same pipeline
with kind `unhandled-exception` / `gui-*-exception` and labels `autodiag` + `unhandled`.

## UIA / automation / AI-agent guard (app)

The elevated One-Click path is a high-value target for **UI Automation (UIA)**, AutoHotkey,
RDP-driven bots, and **AI agents** that can click through GUIs.

Defenses (`engine/uia_guard.py`):

- Detect remote desktop, known automation processes, agent/CI environment hints
- Require an explicit human Yes/No when risk score is high
- Refuse silent `--cli` / `--oneclick` under automation risk
- Override only with `MAGIC_ALLOW_AUTOMATION=1` (intentional automation)
- Optional always-confirm: `MAGIC_CONFIRM=1` or `MAGIC_UIA_STRICT=1`

This is defense-in-depth, not a hard security boundary against a malicious admin.

## Repository branch security (GitHub)

`main` is **protected**:

- No force-push / no branch deletion (ruleset + classic protection)
- Required status checks: `Python engine compile`, `Secret / token hygiene`, `i18n JSON`
- Pull requests + CODEOWNERS review for non-admin contributors
- Conversations must be resolved before merge
- Admins retain bypass for automated release pushes
- Secret scanning + push protection + Dependabot security updates enabled
- Release tags `v*` protected against deletion / non-fast-forward

Settings: https://github.com/dlnraja/win11-magic-upgrade/settings/branches

## CI/CD hardening (supply-chain + AI)

Workflows are hardened against classic and AI-assisted CI/CD attacks:

- **No `pull_request_target`** / issue_comment release triggers (untrusted code + secrets)
- **Least-privilege** `permissions:` (default `contents: read`; write only on Release publish)
- **Actions pinned to full commit SHAs** (tags are mutable; Dependabot can bump SHAs)
- **`persist-credentials: false`** on checkout
- **Untrusted inputs** only via `env:` (never interpolated raw into `run:`)
- **Secret hygiene** job blocks committed tokens / `av_keys.json` / `.env`
- **Dependency review** on pull requests (`fail-on-severity: high`)
- **CODEOWNERS** on `.github/workflows/` and build scripts
- **Release** job documents optional GitHub Environment `release` (add required reviewers, then uncomment `environment:` in the workflow)
- Autodiag issue bodies neutralize common **LLM prompt-injection** prefixes

Recommendations (repo settings → Actions / Branches):

1. Require PR reviews + status checks before merge to `main`
2. Limit Actions to pinned SHAs / verified creators when available
3. Do not pipe untrusted PR titles/bodies into AI agents without isolation
4. Keep secrets out of `workflow_dispatch` inputs and fork PR workflows

## MBR / EFI boot edits (sensitive path)

Boot layout changes use `engine/boot_safe.py`:

1. **Preflight** — verify system disk #, firmware, BitLocker, C: free space; export BCD;
   snapshot ESP critical files; **full ESP/SRP partition backup** (file tree + optional WIM + metadata)
2. **Native tiers** — cleanup → `bcdboot` → verified `diskpart` expand (never invent disk 0)
3. **Retries** — failed ESP/SRP expands re-run after restore
4. **Guarantee bootable** — on success *or* failure intelligent ladder:
   **partition restore / dynamic regenerate** → BCD import → ESP file restore → `bcdboot` →
   `bcdedit` heal → `bootrec` → deep verification → emergency (no double-restore) →
   **one** temporary PE path (WinRE first, else WinPE one-shot) → FreeDOS + GParted packages.
   Reboot only if Windows is verified **or** a confirmed one-shot PE is staged
   (`/boottore` or `/bootsequence` rc=0 — menu-only does not count).
5. **Postflight** — ESP boot files + `{bootmgr}` + deep scorecard
6. **Fallback** — stage **GParted Live** + **FreeDOS** media + guides (never auto-flash / auto-boot)
7. **Autodiag** — on persistent failure, sanitized GitHub Issue (+ optional PR) with restore facts only (no PII)
   - `%LOCALAPPDATA%\\Win11MagicUpgrade\\rescue\\`
   - `%LOCALAPPDATA%\\Win11MagicUpgrade\\partition-backups\\` (generations, LAST.txt)
   - Desktop `Win11MagicUpgrade-GParted-Rescue.txt` / FreeDOS guide
8. Env:
   - `MAGIC_GPARTED_FALLBACK=0` skip GParted ISO download
   - `MAGIC_FREEDOS_FALLBACK=0` skip FreeDOS staging
   - `MAGIC_PS_STORAGE_FALLBACK=0` skip PowerShell Storage expand
   - `MAGIC_PARTITION_BACKUP=0` skip full partition backups
   - `MAGIC_PARTITION_BACKUP_ALWAYS=1` refresh backup every preflight
   - `MAGIC_REQUIRE_BCD_BACKUP=1` hard-require BCD export
   - `MAGIC_WINRE_FALLBACK=0` / `MAGIC_WINPE_FALLBACK=0` disable PE staging
   - `MAGIC_WINRE_BOOT=1` / `MAGIC_WINPE_BOOT=1` force temporary PE staging
