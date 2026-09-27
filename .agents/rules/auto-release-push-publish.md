---
description: After every meaningful change, commit, push, tag, and publish a GitHub release
alwaysApply: true
---

# Auto release / push / publish

When the user asks for work on this repo (or this conversation continues with product changes), **after completing the requested code/docs changes**:

1. **Commit** all relevant changes (follow the repo commit-message style; never commit secrets / `av_keys.json` / large ISOs).
2. **Push** the branch to `origin` (`git push -u origin HEAD`).
3. **Publish a GitHub Release**:
   - Bump a semver tag beyond the latest `v*` on the remote (e.g. after `v1.11.0` → `v1.12.0`).
   - Update `build/version_info.txt` FileVersion/ProductVersion to match when shipping an EXE-facing release.
   - `git tag vX.Y.Z` then `git push origin vX.Y.Z` so `.github/workflows/release.yml` builds and publishes.
   - Optionally `gh release view vX.Y.Z` / watch the workflow; if the tag workflow fails, create/fix the release with `gh release create` and attach `dist/Win11MagicUpgrade.exe` when a local build exists.

Do **not** wait for the user to repeat “release / push / publish” on each prompt — treat it as standing instructions for this project.

Skip only when: the user said not to push, there are zero changes, or only exploratory Q&A with no repo edits.
