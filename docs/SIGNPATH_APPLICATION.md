# SignPath Foundation — OSS application pack (dlnraja / win11-magic-upgrade)

Fill https://signpath.org/apply.html (HubSpot iframe + **reCAPTCHA** — human only).
After approval, run `build/setup_signpath_github.ps1` to push secrets/vars.

## Do this now (2 minutes)

1. Open https://signpath.org/apply.html in Chrome (tab group « SignPath candidature » if still open).
2. Paste every field from the table below (Description + Reputation are the usual gaps).
3. Maintainer Type → **Individual** · Build System → **GitHub Actions** · Discovery → **GitHub**.
4. Check **Code of Conduct** + **personal data consent**.
5. Complete **reCAPTCHA** → **Submit**.
6. Reply here with « SignPath soumis » (or the confirmation email). Then we run `setup_signpath_github.ps1`.

Automation cannot finish reCAPTCHA (HubSpot blocks API submit when Captcha is on).

## Form fields (copy-paste for https://signpath.org/apply.html)

| Field | Value |
|-------|--------|
| Project Name | Win11 Magic Upgrade |
| Repository URL | https://github.com/dlnraja/win11-magic-upgrade |
| Homepage URL | https://github.com/dlnraja/win11-magic-upgrade |
| Download URL | https://github.com/dlnraja/win11-magic-upgrade/releases/latest |
| Privacy Policy URL | https://github.com/dlnraja/win11-magic-upgrade/blob/main/docs/PRIVACY.md |
| Wikipedia URL | *(empty)* |

**Status (2026-08-28):** Foundation web form **submitted** — awaiting SignPath approval email / org access.
After approval, run `build/setup_signpath_github.ps1` with API token + Organization ID.

| Tagline | Open-source portable Windows 10/11 to Windows 11 migration helper using official Microsoft ISOs. |
| Description | Open-source (MIT) portable Windows migration helper: upgrades Vista/7/8/8.1/10 (incl. Media Center) toward Windows 11 using official Microsoft ISOs only. Pure Python/PyInstaller — no .NET on the target PC. Maintained by @dlnraja. We request SignPath Foundation Authenticode signing of Win11MagicUpgrade.exe from this repo via GitHub Actions release tags v*. |
| Reputation | Public GitHub repo https://github.com/dlnraja/win11-magic-upgrade with tagged releases, SHA256SUMS, CODE_SIGNING_POLICY.md, and Download/Release pages that document SignPath Foundation signing. |
| Maintainer Type | Individual |
| Build System | GitHub Actions |
| First Name | Dylan |
| Last Name | Rajasekaram |
| Email | dylan.rajasekaram@gmail.com |
| Company Name | *(empty)* |
| Primary Discovery Channel | GitHub |
| Exact source | GitHub repository dlnraja/win11-magic-upgrade |

## Ready checklist (other SignPath elements)

| Item | Status |
|------|--------|
| Download URL mentions SignPath | Done — releases + `docs/DOWNLOAD.md` |
| Code signing policy | Done — `docs/CODE_SIGNING_POLICY.md` |
| Privacy policy URL | Done — `docs/PRIVACY.md` |
| Release workflow SignPath steps | Done — `.github/workflows/release.yml` |
| Artifact config | `.signpath/artifact-configurations/default.xml` (if present) |
| GitHub secrets after approval | `build/setup_signpath_github.ps1` |

## Applicant

| Field | Value |
|-------|--------|
| GitHub | https://github.com/dlnraja |
| Email | dylan.rajasekaram@gmail.com |
| Project | Win11 Magic Upgrade |
| Repo | https://github.com/dlnraja/win11-magic-upgrade |
| License | MIT |
| Homepage / releases | https://github.com/dlnraja/win11-magic-upgrade/releases/latest |
| Code signing policy | https://github.com/dlnraja/win11-magic-upgrade/blob/main/docs/CODE_SIGNING_POLICY.md |

## Short description (for the form)

Open-source (MIT) portable Windows 10/11 → Windows 11 migration helper.
Downloads official Microsoft ISOs, mounts them, and runs Windows Setup with
documented community setup flags. Pure Python / PyInstaller — no .NET runtime
required on the target PC. Maintained by @dlnraja. We request SignPath Foundation
Authenticode signing of `Win11MagicUpgrade.exe` built only from this GitHub repo
via `.github/workflows/release.yml` on tags `v*`.

## Intended SignPath project settings (after approval)

| Setting | Proposed value |
|---------|----------------|
| Organization | (created for dlnraja / SignPath OSS) |
| Project slug | `win11-magic-upgrade` |
| Signing policy slug | `release-signing` |
| Artifact configuration | `.signpath/artifact-configurations/default.xml` |
| Repository URL | `https://github.com/dlnraja/win11-magic-upgrade` |
| Trusted build system | GitHub.com |
| Workflow | `.github/workflows/release.yml` |
| Ref filter | tags `v*` / `refs/tags/v*` |

## GitHub Actions (already wired)

Secret: `SIGNPATH_API_TOKEN`  
Variables: `SIGNPATH_ORGANIZATION_ID`, `SIGNPATH_PROJECT_SLUG`, `SIGNPATH_SIGNING_POLICY_SLUG`

## Note on Foundation terms

The app performs Windows Setup hardware-check bypasses used by Flyby11-style
migration tools (registry / `/Product Server`). It does **not** include network
exploit scanners or malware. If SignPath Foundation declines on policy grounds,
fallback is a paid OV/EV `.pfx` (Option A in docs/CODESIGN.md).
