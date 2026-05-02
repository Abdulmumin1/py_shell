"""Standalone tests for InMemoryFs - no pytest required."""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "src")

from py_fs_shell.fs.in_memory import InMemoryFs
from py_fs_shell.fs.interface import CpOptions, MkdirOptions, RmOptions


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

    # --- Read/Write ---
    print("=== Read/Write ===")

    async def t1():
        fs = InMemoryFs()
        await fs.write_file("/foo.txt", "hello")
        assert await fs.read_file("/foo.txt") == "hello"
    await check("read_write_string", t1)

    async def t2():
        fs = InMemoryFs()
        data = b"\x00\x01\x02\xff"
        await fs.write_file_bytes("/data.bin", data)
        assert await fs.read_file_bytes("/data.bin") == data
    await check("read_write_bytes", t2)

    async def t3():
        fs = InMemoryFs()
        try:
            await fs.read_file("/nonexistent.txt")
            raise AssertionError("Should have raised")
        except FileNotFoundError:
            pass
    await check("read_missing", t3)

    async def t4():
        fs = InMemoryFs()
        await fs.write_file("/test.txt", "v1")
        await fs.write_file("/test.txt", "v2")
        assert await fs.read_file("/test.txt") == "v2"
    await check("overwrite", t4)

    # --- Append ---
    print("\n=== Append ===")

    async def t5():
        fs = InMemoryFs()
        await fs.write_file("/log.txt", "line1")
        await fs.append_file("/log.txt", "\nline2")
        assert await fs.read_file("/log.txt") == "line1\nline2"
    await check("append_string", t5)

    # --- Exists ---
    print("\n=== Exists ===")

    async def t6():
        fs = InMemoryFs({"/hello.txt": "Hello, World!"})
        assert await fs.exists("/hello.txt")
        assert not await fs.exists("/not_here.txt")
        assert await fs.exists("/")
    await check("exists", t6)

    # --- Stat ---
    print("\n=== Stat ===")

    async def t7():
        fs = InMemoryFs({"/hello.txt": "Hello, World!"})
        st = await fs.stat("/hello.txt")
        assert st.type == "file"
        assert st.size == len("Hello, World!")
    await check("stat_file", t7)

    # --- Mkdir ---
    print("\n=== Mkdir ===")

    async def t8():
        fs = InMemoryFs()
        await fs.mkdir("/new_dir")
        assert await fs.exists("/new_dir")
        st = await fs.stat("/new_dir")
        assert st.type == "directory"
    await check("mkdir_simple", t8)

    async def t9():
        fs = InMemoryFs()
        await fs.mkdir("/a/b/c", MkdirOptions(recursive=True))
        assert await fs.exists("/a/b/c")
    await check("mkdir_recursive", t9)

    # --- Readdir ---
    print("\n=== Readdir ===")

    async def t10():
        fs = InMemoryFs({"/dir/nested.txt": "nested"})
        entries = await fs.readdir("/dir")
        assert entries == ["nested.txt"]
    await check("readdir", t10)

    # --- RM ---
    print("\n=== RM ===")

    async def t11():
        fs = InMemoryFs({"/hello.txt": "hello"})
        await fs.rm("/hello.txt")
        assert not await fs.exists("/hello.txt")
    await check("rm_file", t11)

    async def t12():
        fs = InMemoryFs({"/dir/sub.txt": "sub"})
        await fs.rm("/dir", RmOptions(recursive=True))
        assert not await fs.exists("/dir")
    await check("rm_dir_recursive", t12)

    # --- CP ---
    print("\n=== CP ===")

    async def t13():
        fs = InMemoryFs({"/hello.txt": "Hello, World!"})
        await fs.cp("/hello.txt", "/hello_copy.txt")
        assert await fs.read_file("/hello_copy.txt") == "Hello, World!"
    await check("cp_file", t13)

    async def t14():
        fs = InMemoryFs({"/dir/nested.txt": "nested"})
        await fs.cp("/dir", "/dir_copy", CpOptions(recursive=True))
        assert await fs.read_file("/dir_copy/nested.txt") == "nested"
    await check("cp_dir_recursive", t14)

    # --- MV ---
    print("\n=== MV ===")

    async def t15():
        fs = InMemoryFs({"/hello.txt": "Hello, World!"})
        await fs.mv("/hello.txt", "/hello_moved.txt")
        assert not await fs.exists("/hello.txt")
        assert await fs.read_file("/hello_moved.txt") == "Hello, World!"
    await check("mv_file", t15)

    # --- Symlinks ---
    print("\n=== Symlinks ===")

    async def t16():
        fs = InMemoryFs()
        await fs.write_file("/target.txt", "target content")
        await fs.symlink("/target.txt", "/link.txt")
        assert await fs.read_file("/link.txt") == "target content"
    await check("symlink_read", t16)

    async def t17():
        fs = InMemoryFs()
        await fs.symlink("/target.txt", "/link.txt")
        assert await fs.readlink("/link.txt") == "/target.txt"
    await check("readlink", t17)

    async def t18():
        fs = InMemoryFs()
        await fs.write_file("/target.txt", "final")
        await fs.symlink("/target.txt", "/link1")
        await fs.symlink("/link1", "/link2")
        assert await fs.read_file("/link2") == "final"
    await check("symlink_chain", t18)

    # --- Glob ---
    print("\n=== Glob ===")

    async def t19():
        fs = InMemoryFs({
            "/a.txt": "a",
            "/b.txt": "b",
            "/c/1.txt": "1",
        })
        matches = await fs.glob("*.txt")
        assert "/a.txt" in matches
        assert "/b.txt" in matches
    await check("glob_simple", t19)

    # --- Constructor sync helpers ---
    print("\n=== Sync helpers ===")

    async def t20():
        fs = InMemoryFs()
        fs.write_file_sync("/sync.txt", "sync content")
        fs.mkdir_sync("/sync_dir")
        assert await fs.exists("/sync.txt")
    await check("sync_helpers", t20)

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    exit(0 if success else 1)
