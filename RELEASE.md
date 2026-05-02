# Release process

This project uses `semversioner` for release notes and semantic versioning.

## Add a change

```bash
uv run semversioner add-change --type patch --description "Fix path handling bug."
uv run semversioner add-change --type minor --description "Add a new workspace backend."
uv run semversioner add-change --type major --description "Change the FileSystem API."
```

Commit the generated file under `.semversioner/next-release/` with your code change.

## Preview the next version

```bash
uv run semversioner next-version
uv run semversioner status
```

## Cut a release

```bash
uv run semversioner release
uv run semversioner changelog > CHANGELOG.md
```

Then update `pyproject.toml`'s `project.version` to match the semversioner release, commit the result, tag it, and publish.

For the first release, the Semversioner baseline is `0.0.0`; the initial `patch`
changeset produces `0.0.1`.

## Validate before publishing

```bash
uv run semversioner check
uv run pytest
uv run ruff check .
uv build
uv run twine check dist/*
```

## Build and publish

```bash
uv build
uv publish
```
