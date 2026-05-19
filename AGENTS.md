# AGENTS.md

## Project overview

`py-fs-shell` is a Python library for virtual and sandboxed filesystem workflows.

Core pieces:
- `InMemoryFs` for ephemeral filesystems
- `LocalFileSystem` for sandboxed access to local disk
- `Workspace` for metadata + blob-backed durable storage
- `FileSystemStateBackend` for high-level operations (search/replace, JSON edits, diffs, archives, edit planning)

## Repository structure

- `src/py_fs_shell/` — library source
- `tests/` — pytest test suite
- `examples/` — runnable examples
- `README.md` — usage and installation
- `pyproject.toml` — package metadata + tool config

## Setup

Use Python 3.11+.

```bash
uv sync --extra dev --extra s3
```

## Common commands

Run from repo root:

```bash
# run tests
PYTHONPATH=src pytest

# lint
ruff check .

# format check (optional if needed)
ruff format --check .
```

Run examples:

```bash
PYTHONPATH=src python examples/01_memory_backend.py
```

## Working rules

- Keep changes small and focused.
- Preserve path normalization and sandboxing invariants.
- Prefer existing types/utilities in `src/py_fs_shell/fs/path_utils.py` and related modules over new helpers.
- Add or update tests in `tests/` for behavior changes.
- Do not edit generated caches (`__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `dist/`).

## Notes

There is a separate `agents/` directory that contains AGENTS guidance for another project tree. For this repository’s Python package work, follow this root `AGENTS.md`.
