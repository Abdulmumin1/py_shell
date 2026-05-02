# py_shell

Python virtual filesystem primitives for agent workflows, tests, and sandboxed file operations.

It provides:

- `InMemoryFs` for ephemeral virtual filesystems
- `LocalFileSystem` for sandboxed real-disk access
- `Workspace` for metadata + blob backed storage
- `FileSystemStateBackend` for JSON, search/replace, diffs, archives, hashing, and edit planning

## Installation

```bash
pip install -e .
```

For S3-backed workspaces:

```bash
pip install -e '.[s3]'
```

## Example

```python
import asyncio
from py_shell import StateWriteEditInstruction, workspace

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
from py_shell import workspace

ws = await workspace.s3(bucket="my-bucket", prefix="runs/123")
state = ws.state()
```

## License

MIT. See [LICENSE](LICENSE).

Inspired by Cloudflare's `@cloudflare/shell` package.
