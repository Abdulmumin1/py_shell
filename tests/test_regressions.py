"""Regression tests for correctness and sandbox behavior."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from py_shell import InMemoryFs, LocalFileSystem


async def run_tests():
    passed = 0
    failed = 0

    async def check(name, test):
        nonlocal passed, failed
        try:
            await test()
            passed += 1
            print(f"  PASS: {name}")
        except Exception as e:
            failed += 1
            print(f"  FAIL: {name} - {e}")
            import traceback
            traceback.print_exc()

    print("=== Regressions ===")

    async def t_relative_symlink_nested_path():
        fs = InMemoryFs({"/a/b/target.txt": "ok"})
        await fs.symlink("target.txt", "/a/b/link.txt")
        assert await fs.read_file("/a/b/link.txt") == "ok"
        assert await fs.realpath("/a/b/link.txt") == "/a/b/target.txt"
    await check("relative_symlink_nested_path", t_relative_symlink_nested_path)

    async def t_async_lazy_file_resolves_once():
        calls = 0

        async def provider():
            nonlocal calls
            calls += 1
            return "lazy"

        fs = InMemoryFs({"/lazy.txt": provider})
        assert await fs.read_file("/lazy.txt") == "lazy"
        assert await fs.read_file("/lazy.txt") == "lazy"
        assert calls == 1
    await check("async_lazy_file_resolves_once", t_async_lazy_file_resolves_once)

    async def t_local_symlink_escape_denied():
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / "secret.txt"
            secret.write_text("secret")
            fs = LocalFileSystem(tmp)
            await fs.symlink(str(secret), "/escape")
            try:
                await fs.read_file("/escape")
                raise AssertionError("expected PermissionError")
            except PermissionError:
                pass
    await check("local_symlink_escape_denied", t_local_symlink_escape_denied)

    print("\n" + "=" * 50)
    print(f"Results: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    ok = asyncio.run(run_tests())
    if not ok:
        sys.exit(1)
