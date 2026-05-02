# py_fs_shell

Python virtual filesystem primitives for agent workflows, tests, and sandboxed file operations.

It provides:

- `InMemoryFs` for ephemeral virtual filesystems
- `LocalFileSystem` for sandboxed real-disk access
- `Workspace` for metadata + blob backed storage
- `FileSystemStateBackend` for JSON, search/replace, diffs, archives, hashing, and edit planning

## Installation

With `uv`:

```bash
uv add py-fs-shell
```

With `pip`:

```bash
pip install py-fs-shell
```

For S3-backed workspaces:

```bash
uv add 'py-fs-shell[s3]'
pip install 'py-fs-shell[s3]'
```

For local development from this repository:

```bash
uv sync --extra dev --extra s3
```

## Examples

In-memory workspace:

```python
import asyncio
from py_fs_shell import StateWriteEditInstruction, workspace

async def main():
    ws = await workspace.memory()
    state = ws.state()

    await state.write_file("/src/main.py", "print('hello world')\n")
    await state.write_json("/config.json", {"debug": True})

    matches = await state.search_files("**/*.py", "hello")
    print(matches[0].path)

    plan = await state.plan_edits([
        StateWriteEditInstruction(
            path="/README.md",
            content="# Demo\n",
        ),
    ])
    await state.apply_edit_plan(plan)

    await state.write_file_bytes("/video.mp4", b"video bytes")

asyncio.run(main())
```

Local workspace:

```python
import asyncio
from pathlib import Path
from py_fs_shell import workspace

async def main():
    ws = await workspace.local(Path(".py_fs_shell_workspace"))
    state = ws.state()

    await state.write_file("/notes/todo.md", "- ship release\n")
    await state.write_json("/metadata.json", {"backend": "local"})

    print(await state.read_file("/notes/todo.md"))

asyncio.run(main())
```

S3-backed workspace:

```python
import asyncio
from py_fs_shell import workspace

async def main():
    ws = await workspace.s3(bucket="my-bucket", prefix="runs/123")
    state = ws.state()

    await state.write_file("/outputs/result.txt", "done\n")
    await state.write_json("/metadata.json", {"backend": "s3"})

    print(await state.read_file("/outputs/result.txt"))

asyncio.run(main())
```

## Releases

Releases are tracked with `semversioner`. See [RELEASE.md](RELEASE.md) and [CHANGELOG.md](CHANGELOG.md).

## License

MIT. See [LICENSE](LICENSE).

Inspired by Cloudflare's `@cloudflare/shell` package.
