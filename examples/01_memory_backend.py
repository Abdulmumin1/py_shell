"""Basic in-memory StateBackend usage."""

from __future__ import annotations

import asyncio

from py_fs_shell import StateFindOptions, create_memory_state_backend


async def main() -> None:
    state = create_memory_state_backend(
        {
            "/src/main.py": "print('hello world')\n",
            "/src/utils.py": "def greet(name):\n    return f'hello {name}'\n",
            "/README.md": "# Demo\n",
        }
    )

    await state.write_json("/config.json", {"debug": True, "workers": 4})
    config = await state.read_json("/config.json")

    result = await state.replace_in_files("*.py", "hello", "hi")
    files = await state.find("/", StateFindOptions(type="file"))

    print("config:", config)
    print("replacements:", result.total_replacements)
    print("files:", [entry.path for entry in files])
    print("main.py:", await state.read_file("/src/main.py"))


if __name__ == "__main__":
    asyncio.run(main())
