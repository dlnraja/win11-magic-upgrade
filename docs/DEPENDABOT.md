# Dependabot — quiet mode

This repo only tracks **GitHub Actions** (no pip/npm Dependabot). Config: `.github/dependabot.yml`.

## What we did to reduce spam

| Setting | Effect |
|---------|--------|
| **Monthly** (Monday 09:00 Brussels) | Not daily/weekly |
| **1 PR max** + **grouped** | One combined `deps(actions…)` PR instead of four |
| **Ignore semver-major** | No repeat of v4→v7 floods; bump majors manually when you want |
| **No reviewers/assignees** | Dependabot does not @mention you on open |
| **rebase-strategy: disabled** | Fewer “force-pushed” notification emails |

Actions in workflows stay **SHA-pinned**; Dependabot only proposes comment/tag bumps.

Security advisories can still open **separate** PRs (rare). That is intentional.

## GitHub notifications (recommended)

1. [github.com/settings/notifications](https://github.com/settings/notifications)
2. **System** → **Dependabot alerts**: Email **Off** (or Web only) if you watch the repo on the web
3. **Custom routing** (optional): repo `dlnraja/win11-magic-upgrade` → **Not watching** or **Participating only**
4. For PR noise: **Email** → uncheck **Pull request reviews** if CODEOWNERS on `.github/workflows/` still pings you on human PRs

## Gmail filter (optional)

Create a filter:

- **From:** `notifications@github.com`
- **Has the words:** `dependabot` OR `dependencies` OR `deps(actions`
- **Action:** Skip Inbox, apply label `GitHub-Deps`, mark as read (or archive)

You still see security alerts in GitHub **Security** tab.

## Maintainer: apply grouped updates

When the monthly PR appears:

1. Check CI green
2. Merge (squash OK)
3. If a **major** bump is needed later, edit workflow SHAs manually or temporarily remove the `ignore` block in `dependabot.yml`

## Turn off version PRs entirely

Set `open-pull-requests-limit: 0` in `dependabot.yml` — only security PRs remain. Use this if even monthly grouped PRs are too much.
