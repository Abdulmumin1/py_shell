# py_shell

A Python virtual filesystem with a structured state backend, inspired by
[@cloudflare/shell](https://github.com/cloudflare/agents/tree/main/packages/shell).

## What is this?

`py_shell` provides a layered filesystem abstraction designed for agent
workflows, testing, and scripting. It mirrors the architecture of the JS
`@cloudflare/shell` package but is built Python-first with async/await
throughout.

## Architecture

```
┌─────────────────────────────────────────────┐
│  StateBackend (high-level API)              │
│  - JSON helpers, search/replace             │
│  - diff, compression, archives              │
│  - structured editing (plan/apply)          │
│  - file detection, hashing                  │
│  - LLM prompt system                        │
└───────────────────┬─────────────────────────┘
                    │ wraps 1 FileSystem
┌───────────────────▼─────────────────────────┐
│  FileSystem (low-level ABC)                 │
│  - read_file, write_file, mkdir             │
│  - readdir, glob, cp, mv, rm, stat          │
│  - symlink, realpath, readlink              │
└───────────┬───────────────────┬─────────────┘
            │                   │
┌───────────▼────────┐ ┌────────▼────────────┐
│  InMemoryFs        │ │  LocalFileSystem    │
│  tree-based        │ │  real OS access     │
│  in-memory VFS     │ │  via aio threads    │
│                    │ │                     │
│  - O(1) lookups    │ │  - tmp dirs         │
│  - symlinks        │ │  - sandboxed root   │
│  - lazy files      │ │  - pathlib-based    │
└────────────────────┘ └─────────────────────┘
```

## Quick Start

```python
import asyncio
from py_shell import create_memory_state_backend, StateWriteEditInstruction

async def main():
    # Create a state backend backed by an in-memory filesystem
    state = create_memory_state_backend({
        "/src/main.py": "print('hello world')",
        "/src/utils.py": "def helper(): pass",
    })

    # Read/write files
    content = await state.read_file("/src/main.py")
    await state.write_file("/README.md", "# My Project")

    # JSON helpers
    await state.write_json("/config.json", {"debug": True, "workers": 4})
    config = await state.read_json("/config.json")

    # Search & replace
    result = await state.replace_in_files("*.py", "hello", "hi")
    print(f"Replaced in {result.total_files} files")

    # Find all files
    entries = await state.find("/", type="file")
    for entry in entries:
        print(f"  {entry.path} ({entry.size} bytes)")

    # Structured edits (plan then apply)
    plan = await state.plan_edits([
        StateWriteEditInstruction(path="/new_feature.py", content="# TODO"),
    ])
    await state.apply_edit_plan(plan)

asyncio.run(main())
```

## Core Components

### `FileSystem` (ABC)

The minimal filesystem contract. All methods are async.

Key methods:
- `read_file(path) → str`
- `write_file(path, content)`
- `read_file_bytes(path) → bytes`
- `write_file_bytes(path, content)`
- `append_file(path, content)`
- `mkdir(path, options)`
- `readdir(path) → list[str]`
- `readdir_with_file_types(path) → list[FileSystemDirent]`
- `glob(pattern) → list[str]`
- `cp(src, dest, options)`
- `mv(src, dest)`
- `rm(path, options)`
- `stat(path) → FsStat` / `lstat(path) → FsStat`
- `symlink(target, link_path)` / `readlink(path) → str`

### `InMemoryFs`

A tree-based in-memory implementation using `_VFileNode`, `_VDirNode`, and
`_VSymlinkNode`. Provides:

- O(1) path lookups (no linear scan)
- Full symlink support with depth limits
- Sync helpers for setup (`write_file_sync`, `mkdir_sync`)
- Lazy file support for deferred content loading

```python
from py_shell import InMemoryFs

fs = InMemoryFs({
    "/hello.txt": "hello",
    "/data/config.json": FileInit(content='{"key": "val"}', mode=0o600),
})
```

### `Workspace`

A durable VFS built from a metadata store and a blob store. Easy presets hide the
metadata/blob setup:

```python
from py_shell import workspace

ws = await workspace.memory()          # MemoryMetadataStore + MemoryBlobStore
ws = await workspace.local(".workspace") # SQLiteMetadataStore + LocalBlobStore
ws = await workspace.s3(bucket="my-bucket", prefix="runs/123") # S3MetadataStore + S3BlobStore

state = ws.state()
await state.write_file_bytes("/video.mp4", data)
```

Advanced users can compose the stores directly:

```python
from py_shell import Workspace, SQLiteMetadataStore, S3BlobStore

ws = await workspace.s3(
    bucket="my-bucket",
    prefix="runs/123",
    metadata=SQLiteMetadataStore("workspace/metadata.sqlite3"),
)

# or compose directly
ws = await Workspace(
    metadata=SQLiteMetadataStore("workspace/metadata.sqlite3"),
    blobs=S3BlobStore(bucket="my-bucket", prefix="runs/123"),
).init()
```

### `LocalFileSystem`

Adapts `FileSystem` to the real OS filesystem. All operations are threaded
via `asyncio.to_thread` for non-blocking I/O. Root directory isolation
prevents sandbox escape.

```python
from py_shell import LocalFileSystem
import tempfile

with tempfile.TemporaryDirectory() as tmp:
    fs = LocalFileSystem(tmp)
    await fs.write_file("/output.txt", "results")
    # File is at {tmp}/output.txt
```

### `FileSystemStateBackend`

Wraps any `FileSystem` with high-level operations useful for agents and
workflows:

| Category | Methods |
|----------|---------|
| **JSON** | `read_json`, `write_json`, `query_json`, `update_json` |
| **Search** | `search_text`, `search_files` |
| **Replace** | `replace_in_file`, `replace_in_files` |
| **Diff** | `diff`, `diff_content` |
| **Directories** | `find`, `walk_tree`, `summarize_tree` |
| **Archives** | `create_archive`, `list_archive`, `extract_archive` |
| **Compression** | `compress_file`, `decompress_file` |
| **Crypto** | `hash_file` (md5, sha1, sha256) |
| **Detection** | `detect_file` (MIME, binary/text) |
| **Editing** | `plan_edits`, `apply_edit_plan`, `apply_edits` |

All batch operations support dry-run and rollback on error.

### `prompt` module

Provides the same LLM prompt types and system prompt as `@cloudflare/shell`:

```python
from py_shell.prompt import STATE_TYPES, STATE_SYSTEM_PROMPT

system_prompt = STATE_SYSTEM_PROMPT.replace("{{types}}", STATE_TYPES)
```

## Examples

Runnable examples live in [`examples/`](examples/):

```bash
PYTHONPATH=src python examples/01_memory_backend.py
PYTHONPATH=src python examples/02_in_memory_fs.py
PYTHONPATH=src python examples/03_local_filesystem.py
PYTHONPATH=src python examples/04_edit_plan.py
PYTHONPATH=src python examples/05_workspace.py
```

## Install

```bash
pip install -e .
```

## Development

```bash
# Run all tests
PYTHONPATH=src python tests/test_in_memory_simple.py
PYTHONPATH=src python tests/test_state_backend.py
PYTHONPATH=src python tests/test_local_fs.py

# Quick manual test
PYTHONPATH=src python -c "
import asyncio
from py_shell import create_memory_state_backend

async def main():
    s = create_memory_state_backend({'/hi.txt': 'hello'})
    print(await s.read_file('/hi.txt'))

asyncio.run(main())
"
```

## Differences from @cloudflare/shell

| Feature | JS `@cloudflare/shell` | `py_shell` |
|---------|------------------------|------------|
| Core | JS Promises | Python async/await |
| Workspace | SQLite + R2 | Not yet implemented |
| Shell exec | ABIs into Deno isolate | Not implemented |
| Prompts | STATE_TYPES | Complete mirror |
| InMemoryFs | VFileNode tree | Python dataclass tree |
| LocalFS | Via `node:fs` | Via `pathlib` + threads |

## License

MIT
