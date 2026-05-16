---
name: release-skill
description: Repository-specific release workflow for py-fs-shell using branches, semversioner, validation, and the GitHub release pipeline. Use when preparing a release, adding release notes, validating package metadata, or pushing release prep work without pushing directly to main.
---

# Release Skill

Use this skill for release preparation and release hygiene in this repository.

## When to Use

Use this skill when the task involves:

- preparing a patch, minor, or major release
- adding a Semversioner change entry
- validating changelog, version, build, and package metadata
- pushing release work to a branch instead of directly to `main`
- understanding how the repository's GitHub release automation works
- cleaning up release prep steps before handoff

## Skill Format Notes

This skill follows the local skill pattern used by installed skills:

1. YAML frontmatter with `name` and `description`
2. A top-level heading naming the skill
3. Clear sections like `When to Use`, `Workflow`, and `Validation`
4. Concrete commands and repo-specific conventions

## Repository Release Facts

- Release notes and semantic versioning are managed with `semversioner`
- Release instructions live in `RELEASE.md`
- CI checks release entries on pull requests in `.github/workflows/ci.yml`
- Automated publishing is handled by `.github/workflows/release.yml`
- This repo should use branches and PRs; do not push release prep straight to `main`

## Recommended Workflow

1. Start from an up-to-date `main` locally.
2. Create a release prep branch:

   ```bash
   git checkout -b chump/release-<topic>
   ```

3. Confirm the working tree is clean or intentionally dirty:

   ```bash
   git status --short
   ```

4. Add a Semversioner entry:

   ```bash
   uv run semversioner add-change --type patch --description "Fix ..."
   ```

   Or use `minor` / `major` as appropriate.

5. Preview release state:

   ```bash
   uv run semversioner next-version
   uv run semversioner status
   ```

6. Run release validation:

   ```bash
   uv run semversioner check
   uv run pytest
   uv run ruff check .
   uv build
   uv run twine check dist/*
   ```

7. Commit the release prep work:

   ```bash
   git add .
   git commit -m "chore: prepare release"
   ```

8. Push the branch, not `main`:

   ```bash
   git push -u origin chump/release-<topic>
   ```

9. Open a PR. After merge to `main`, the GitHub release workflow can:
   - run `semversioner release`
   - regenerate `CHANGELOG.md`
   - sync `pyproject.toml` version
   - build and validate the distribution
   - publish to PyPI
   - create a Git tag and GitHub release

## Safety Rules

- Never push directly to `main` for release prep work
- Always include a Semversioner change file for user-visible fixes
- Validate `dist/*` with Twine before treating a release as ready
- Keep changelog/version automation aligned with the GitHub workflow rather than hand-editing random files
- If a branch already contains local commits made on `main`, create a branch from the current HEAD before pushing

## Quick Commands

```bash
uv run semversioner add-change --type patch --description "Fix ..."
uv run semversioner check
uv run semversioner next-version
uv run pytest
uv run ruff check .
uv build
uv run twine check dist/*
git push -u origin <branch>
```
