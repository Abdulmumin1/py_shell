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
uv add py-shell
```

With `pip`:

```bash
pip install py-shell
```

For S3-backed workspaces:

```bash
uv add 'py-shell[s3]'
pip install 'py-shell[s3]'
```

For local development from this repository:

```bash
uv sync --extra dev --extra s3
```

## Example

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

S3-backed workspace:

```python
from py_fs_shell import workspace

ws = await workspace.s3(bucket="my-bucket", prefix="runs/123")
state = ws.state()
```

## Releases

Releases are tracked with `semversioner`. See [RELEASE.md](RELEASE.md) and [CHANGELOG.md](CHANGELOG.md).

## License

MIT. See [LICENSE](LICENSE).

Inspired by Cloudflare's `@cloudflare/shell` package.
