"""Tests for LocalFileSystem - real OS filesystem adapter."""

import asyncio
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from py_fs_shell.fs.interface import MkdirOptions
from py_fs_shell.local_fs import LocalFileSystem


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

    print("=== LocalFileSystem ===")

    async def t_basic():
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fs = LocalFileSystem(tmp)

            await fs.write_file("/hello.txt", "world")
            assert (tmp_path / "hello.txt").read_text() == "world"

            content = await fs.read_file("/hello.txt")
            assert content == "world"

            assert await fs.exists("/hello.txt")
            assert not await fs.exists("/missing.txt")
    await check("basic_rw", t_basic)

    async def t_mkdir():
        with tempfile.TemporaryDirectory() as tmp:
            fs = LocalFileSystem(tmp)
            await fs.mkdir("/a/b/c", MkdirOptions(recursive=True))
            assert await fs.exists("/a/b/c")
            st = await fs.stat("/a/b/c")
            assert st.type == "directory"
            # Also test simple mkdir (one level)
            await fs.mkdir("/a/b/c/d")
            assert await fs.exists("/a/b/c/d")
    await check("mkdir", t_mkdir)

    async def t_readdir():
        with tempfile.TemporaryDirectory() as tmp:
            fs = LocalFileSystem(tmp)
            await fs.write_file("/dir/a.txt", "a")
            await fs.write_file("/dir/b.txt", "b")
            entries = await fs.readdir("/dir")
            assert sorted(entries) == ["a.txt", "b.txt"]
    await check("readdir", t_readdir)

    async def t_cp_mv_rm():
        with tempfile.TemporaryDirectory() as tmp:
            fs = LocalFileSystem(tmp)
            await fs.write_file("/src.txt", "content")
            await fs.cp("/src.txt", "/dst.txt")
            assert await fs.read_file("/dst.txt") == "content"

            await fs.mv("/dst.txt", "/moved.txt")
            assert not await fs.exists("/dst.txt")
            assert await fs.exists("/moved.txt")

            await fs.rm("/moved.txt")
            assert not await fs.exists("/moved.txt")
    await check("cp_mv_rm", t_cp_mv_rm)

    async def t_bytes():
        with tempfile.TemporaryDirectory() as tmp:
            fs = LocalFileSystem(tmp)
            data = b"\x00\x01\xff"
            await fs.write_file_bytes("/bin", data)
            assert await fs.read_file_bytes("/bin") == data
    await check("bytes_rw", t_bytes)

    async def t_append():
        with tempfile.TemporaryDirectory() as tmp:
            fs = LocalFileSystem(tmp)
            await fs.write_file("/log.txt", "line1")
            await fs.append_file("/log.txt", "\nline2")
            assert await fs.read_file("/log.txt") == "line1\nline2"
    await check("append", t_append)

    async def t_write_via_inside_symlink_stays_inside_root():
        with tempfile.TemporaryDirectory() as tmp:
            fs = LocalFileSystem(tmp)
            await fs.write_file("/target.txt", "before")
            await fs.symlink("target.txt", "/link.txt")
            await fs.write_file("/link.txt", "after")
            assert await fs.read_file("/target.txt") == "after"
    await check("write_via_inside_symlink_stays_inside_root", t_write_via_inside_symlink_stays_inside_root)

    async def t_write_via_escape_symlink_denied():
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / "secret.txt"
            secret.write_text("secret")
            fs = LocalFileSystem(tmp)
            await fs.symlink(str(secret), "/escape.txt")
            with pytest.raises(PermissionError):
                await fs.write_file("/escape.txt", "owned")
            assert secret.read_text() == "secret"
    await check("write_via_escape_symlink_denied", t_write_via_escape_symlink_denied)

    async def t_stat():
        with tempfile.TemporaryDirectory() as tmp:
            fs = LocalFileSystem(tmp)
            await fs.write_file("/file.txt", "hello")
            st = await fs.stat("/file.txt")
            assert st.type == "file"
            assert st.size == 5
    await check("stat", t_stat)

    async def t_glob():
        with tempfile.TemporaryDirectory() as tmp:
            fs = LocalFileSystem(tmp)
            await fs.write_file("/a.txt", "a")
            await fs.write_file("/b.txt", "b")
            await fs.write_file("/c.py", "c")
            results = await fs.glob("*.txt")
            assert "/a.txt" in results
            assert "/b.txt" in results
            assert "/c.py" not in results
    await check("glob", t_glob)

    print("\n" + "=" * 50)
    print(f"Results: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    ok = asyncio.run(run_tests())
    if not ok:
        sys.exit(1)
