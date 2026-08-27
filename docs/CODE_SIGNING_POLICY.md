# Code signing policy

**Free code signing provided by [SignPath.io](https://signpath.io), certificate by [SignPath Foundation](https://signpath.org/).**

This document satisfies the SignPath Foundation website/repository requirements for open-source projects.

## Project

| Field | Value |
|-------|--------|
| Name | Win11 Magic Upgrade |
| Repository | https://github.com/dlnraja/win11-magic-upgrade |
| License | [MIT](../LICENSE) (OSI-approved) |
| Releases | https://github.com/dlnraja/win11-magic-upgrade/releases |
| Publisher (Authenticode) | SignPath Foundation (when OSS signing is enabled) · interim self-signed `dlnraja` until approved |

## What is signed

- `Win11MagicUpgrade.exe` built by GitHub Actions workflow `.github/workflows/release.yml` on tags `v*`
- Portable ZIP packages containing that EXE
- Product name / company metadata: **Win11 Magic Upgrade** / **dlnraja** (see `build/version_info.txt`)

## Team roles

| Role | Who | Responsibility |
|------|-----|----------------|
| Authors / committers | [@dlnraja](https://github.com/dlnraja) | Trusted to push to `main` and maintain the project |
| Reviewers | [@dlnraja](https://github.com/dlnraja) (+ PR reviewers when collaborators are added) | Review pull requests before merge |
| Approvers (signing) | [@dlnraja](https://github.com/dlnraja) | Approve SignPath signing requests for releases |

Repository owner: https://github.com/dlnraja/win11-magic-upgrade/settings/access

## Privacy policy

This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it.

Optional features that may use the network **only when the user runs the app / One-Click**:

- Download of **official Microsoft Windows ISO** media from Microsoft CDN
- Optional GitHub Releases / diagnostic issue filing (sanitized; see [SECURITY.md](../SECURITY.md))
- Optional VirusTotal / Kaspersky OpenTIP sample submit **from CI** when repository secrets are configured (not from One-Click)

No telemetry is sold. No advertising ID. Logs stay under `%LOCALAPPDATA%\Win11MagicUpgrade\` unless the user opens / files a sanitized GitHub diagnostic.

## Security practices

- All maintainers must use **multi-factor authentication** on GitHub (and on SignPath when granted access).
- Release signing is restricted to the GitHub Actions Release workflow for this repository (trusted build system).
- Users are warned that the tool requires Administrator rights and modifies system configuration for Windows Setup (see README / UI notes).

## Links

- [CODESIGN.md](CODESIGN.md) — PFX / SignPath CI secrets
- [DOWNLOAD.md](DOWNLOAD.md) — Chrome / SmartScreen help
- [SECURITY.md](../SECURITY.md) — security policy
- SignPath Foundation terms: https://signpath.org/terms.html
