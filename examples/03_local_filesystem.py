"""Use LocalFileSystem against a sandboxed real directory."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from py_fs_shell import FileSystemStateBackend, LocalFileSystem, StateMkdirOptions


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        fs = LocalFileSystem(tmp)
        state = FileSystemStateBackend(fs)

        await state.mkdir("/project/src", StateMkdirOptions(recursive=True))
        await state.write_file("/project/src/main.py", "print('hello from disk')\n")
        await state.write_json("/project/package.json", {"name": "demo"})

        print("root:", tmp)
        print("tree:", await state.readdir("/project"))
        print("disk file exists:", (Path(tmp) / "project" / "src" / "main.py").exists())
        print(await state.read_file("/project/src/main.py"))


if __name__ == "__main__":
    asyncio.run(main())
