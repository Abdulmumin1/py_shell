"""Low-level InMemoryFs usage."""

from __future__ import annotations

import asyncio

from py_shell import InMemoryFs, MkdirOptions, RmOptions


async def main() -> None:
    fs = InMemoryFs()

    await fs.mkdir("/workspace/src", MkdirOptions(recursive=True))
    await fs.write_file("/workspace/src/app.py", "print('hello')\n")
    await fs.append_file("/workspace/src/app.py", "print('again')\n")
    await fs.symlink("/workspace/src/app.py", "/workspace/app-link.py")

    print(await fs.readdir("/workspace"))
    print(await fs.read_file("/workspace/app-link.py"))
    print(await fs.glob("**/*.py"))

    await fs.rm("/workspace/src", RmOptions(recursive=True))
    print(await fs.exists("/workspace/src/app.py"))


if __name__ == "__main__":
    asyncio.run(main())
