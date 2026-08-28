# Privacy Policy — Win11 Magic Upgrade

**Publisher:** [dlnraja](https://github.com/dlnraja)  
**Project:** [win11-magic-upgrade](https://github.com/dlnraja/win11-magic-upgrade)  
**Last updated:** 2026-08-28

## Summary

Win11 Magic Upgrade is a local, open-source Windows migration helper. It does **not** sell data, run advertising trackers, or phone home for analytics.

This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it.

## What the software does on your PC

When you run One-Click / diagnose (as Administrator), the tool may:

- Read local system information (OS build, disk layout, TPM/Secure Boot flags, OEM model) to plan an upgrade
- Write logs and state under `%LOCALAPPDATA%\Win11MagicUpgrade\`
- Modify Windows registry and Setup-related settings required for an in-place upgrade
- Download **official Microsoft Windows ISO** media from Microsoft CDN (only when you start an upgrade path that needs an ISO)
- Mount ISOs and launch Windows Setup (`setupprep` / `setup`)

## Network use

| Activity | When | Destination |
|----------|------|-------------|
| Microsoft ISO download | User-initiated upgrade | Microsoft CDN |
| Optional GitHub diagnostic issue | User chooses to file support | GitHub |
| VirusTotal / Kaspersky OpenTIP | **CI/Release only** (repo secrets) — not from One-Click | Third-party AV APIs |

No telemetry is sold. No advertising ID.

## Code signing notice

Official release builds may be Authenticode-signed by **[SignPath Foundation](https://signpath.org/)** / [SignPath.io](https://signpath.io). Signing policy: [CODE_SIGNING_POLICY.md](CODE_SIGNING_POLICY.md).

## Contact

- GitHub issues: https://github.com/dlnraja/win11-magic-upgrade/issues  
- Maintainer: [@dlnraja](https://github.com/dlnraja) · dylan.rajasekaram@gmail.com

## Related

- [SECURITY.md](../SECURITY.md)
- [CODE_SIGNING_POLICY.md](CODE_SIGNING_POLICY.md)
- [DOWNLOAD.md](DOWNLOAD.md)
