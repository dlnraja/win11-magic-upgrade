# SignPath Foundation — OSS application pack (dlnraja / win11-magic-upgrade)

Use this text to fill https://signpath.org/apply.html (HubSpot form + reCAPTCHA).
After approval, run `build/setup_signpath_github.ps1` to push secrets/vars.

## Form fields (copy-paste for https://signpath.org/apply.html)

| Field | Value |
|-------|--------|
| Project Name | Win11 Magic Upgrade |
| Repository URL | https://github.com/dlnraja/win11-magic-upgrade |
| Homepage URL | https://github.com/dlnraja/win11-magic-upgrade |
| Download URL | https://github.com/dlnraja/win11-magic-upgrade/releases/latest |
| Privacy Policy URL | *(empty)* |
| Wikipedia URL | *(empty)* |
| Tagline | Open-source portable Windows 10/11 to Windows 11 migration helper using official Microsoft ISOs. |
| Description | Open-source (MIT) portable Windows 10/11 to Windows 11 migration helper. Downloads official Microsoft ISOs, mounts them, and runs Windows Setup. Pure Python/PyInstaller, no .NET on target PC. Maintained by @dlnraja on GitHub. |
| Reputation | Public GitHub repo https://github.com/dlnraja/win11-magic-upgrade with releases for Windows migration tooling. |
| Maintainer Type | Individual |
| Build System | GitHub Actions |
| First Name | Dylan |
| Last Name | Rajasekaram |
| Email | dylan.rajasekaram@gmail.com |
| Company Name | *(empty)* |
| Primary Discovery Channel | GitHub |
| Exact source | GitHub repository dlnraja/win11-magic-upgrade |

Check **Code of Conduct** + **personal data consent**, complete **reCAPTCHA**, then **Submit**.

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
