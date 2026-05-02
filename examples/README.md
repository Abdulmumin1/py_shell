# Examples

Run examples from the repository root with `PYTHONPATH=src`:

```bash
PYTHONPATH=src python examples/01_memory_backend.py
PYTHONPATH=src python examples/02_in_memory_fs.py
PYTHONPATH=src python examples/03_local_filesystem.py
PYTHONPATH=src python examples/04_edit_plan.py
PYTHONPATH=src python examples/05_workspace.py
```

## Files

- `01_memory_backend.py` — high-level in-memory `StateBackend`
- `02_in_memory_fs.py` — low-level `InMemoryFs`
- `03_local_filesystem.py` — sandboxed real filesystem adapter
- `04_edit_plan.py` — plan/apply structured edits with diffs
- `05_workspace.py` — Workspace presets with SQLite metadata + blob stores
