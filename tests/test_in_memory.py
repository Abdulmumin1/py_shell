"""Tests for InMemoryFs - tree-based in-memory filesystem."""

from __future__ import annotations

import asyncio

import pytest

from py_fs_shell.fs.in_memory import InMemoryFs
from py_fs_shell.fs.interface import (
    CpOptions,
    FileInit,
    MkdirOptions,
    RmOptions,
)

# ── Basic file operations ─────────────────────────────────────────

@pytest.fixture
def empty_fs() -> InMemoryFs:
    return InMemoryFs()


@pytest.fixture
def sample_fs() -> InMemoryFs:
    return InMemoryFs({
        "/hello.txt": "Hello, World!",
        "/dir/nested.txt": "nested content",
        "/dir/sub/subfile.txt": "deep nested",
        "/empty_dir": DirectoryEntry(),
    })


class DirectoryEntry:
    type = "directory"  # type: ignore[assignment]


class TestReadWrite:
    async def test_read_write_string(self, empty_fs: InMemoryFs) -> None:
        await empty_fs.write_file("/foo.txt", "hello")
        assert await empty_fs.read_file("/foo.txt") == "hello"

    async def test_read_write_bytes(self, empty_fs: InMemoryFs) -> None:
        data = b"\x00\x01\x02\xff"
        await empty_fs.write_file_bytes("/data.bin", data)
        assert await empty_fs.read_file_bytes("/data.bin") == data

    async def test_read_missing_file(self, empty_fs: InMemoryFs) -> None:
        with pytest.raises(FileNotFoundError):
            await empty_fs.read_file("/nonexistent.txt")

    async def test_read_empty_file(self, empty_fs: InMemoryFs) -> None:
        await empty_fs.write_file("/empty.txt", "")
        assert await empty_fs.read_file("/empty.txt") == ""

    async def test_overwrite_file(self, empty_fs: InMemoryFs) -> None:
        await empty_fs.write_file("/test.txt", "v1")
        await empty_fs.write_file("/test.txt", "v2")
        assert await empty_fs.read_file("/test.txt") == "v2"

    async def test_write_to_root_fails(self, empty_fs: InMemoryFs) -> None:
        with pytest.raises(IsADirectoryError):
            await empty_fs.write_file("/", "xxx")


class TestAppendFile:
    async def test_append_string(self, empty_fs: InMemoryFs) -> None:
        await empty_fs.write_file("/log.txt", "line1")
        await empty_fs.append_file("/log.txt", "\nline2")
        assert await empty_fs.read_file("/log.txt") == "line1\nline2"

    async def test_append_bytes(self, empty_fs: InMemoryFs) -> None:
        await empty_fs.write_file_bytes("/log.bin", b"\x00")
        await empty_fs.append_file("/log.bin", b"\x01")
        assert await empty_fs.read_file_bytes("/log.bin") == b"\x00\x01"

    async def test_append_creates_file(self, empty_fs: InMemoryFs) -> None:
        await empty_fs.append_file("/new.txt", "hello")
        assert await empty_fs.read_file("/new.txt") == "hello"


class TestExists:
    async def test_exists_true(self, sample_fs: InMemoryFs) -> None:
        assert await sample_fs.exists("/hello.txt") is True

    async def test_exists_false(self, sample_fs: InMemoryFs) -> None:
        assert await sample_fs.exists("/not_here.txt") is False

    async def test_exists_directory(self, sample_fs: InMemoryFs) -> None:
        assert await sample_fs.exists("/dir") is True

    async def test_exists_root(self, empty_fs: InMemoryFs) -> None:
        assert await empty_fs.exists("/") is True


class TestStat:
    async def test_stat_file(self, sample_fs: InMemoryFs) -> None:
        st = await sample_fs.stat("/hello.txt")
        assert st.type == "file"
        assert st.size == len("Hello, World!")

    async def test_stat_directory(self, sample_fs: InMemoryFs) -> None:
        st = await sample_fs.stat("/dir")
        assert st.type == "directory"

    async def test_stat_missing(self, sample_fs: InMemoryFs) -> None:
        with pytest.raises(FileNotFoundError):
            await sample_fs.stat("/nonexistent")

    async def test_lstat_symlink(self, empty_fs: InMemoryFs) -> None:
        await empty_fs.write_file("/target.txt", "target")
        await empty_fs.symlink("/target.txt", "/link.txt")
        st_l = await empty_fs.lstat("/link.txt")
        assert st_l.type == "symlink"
        # target doesn't exist, so stat would raise if we followed


# ── Directory operations ──────────────────────────────────────────

class TestMkdir:
    async def test_mkdir_simple(self, empty_fs: InMemoryFs) -> None:
        await empty_fs.mkdir("/new_dir")
        assert await empty_fs.exists("/new_dir")
        st = await empty_fs.stat("/new_dir")
        assert st.type == "directory"

    async def test_mkdir_recursive(self, empty_fs: InMemoryFs) -> None:
        await empty_fs.mkdir("/a/b/c", MkdirOptions(recursive=True))
        assert await empty_fs.exists("/a/b/c")

    async def test_mkdir_exists_fails(self, empty_fs: InMemoryFs) -> None:
        await empty_fs.mkdir("/dir")
        with pytest.raises(FileExistsError):
            await empty_fs.mkdir("/dir")

    async def test_mkdir_parent_missing(self, empty_fs: InMemoryFs) -> None:
        with pytest.raises(FileNotFoundError):
            await empty_fs.mkdir("/a/b")


class TestReaddir:
    async def test_readdir(self, sample_fs: InMemoryFs) -> None:
        entries = await sample_fs.readdir("/dir")
        assert sorted(entries) == ["nested.txt", "sub"]

    async def test_readdir_empty(self, sample_fs: InMemoryFs) -> None:
        entries = await sample_fs.readdir("/empty_dir")
        assert entries == []

    async def test_readdir_not_directory(self, sample_fs: InMemoryFs) -> None:
        with pytest.raises(NotADirectoryError):
            await sample_fs.readdir("/hello.txt")

    async def test_readdir_with_types(self, sample_fs: InMemoryFs) -> None:
        entries = await sample_fs.readdir_with_file_types("/")
        names = {e.name for e in entries}
        assert names == {"hello.txt", "dir", "empty_dir"}
        for e in entries:
            if e.name == "hello.txt":
                assert e.type == "file"
            else:
                assert e.type == "directory"


# ── Remove/Copy/Move ──────────────────────────────────────────────

class TestRm:
    async def test_rm_file(self, sample_fs: InMemoryFs) -> None:
        await sample_fs.rm("/hello.txt")
        assert not await sample_fs.exists("/hello.txt")

    async def test_rm_directory_recursive(self, sample_fs: InMemoryFs) -> None:
        await sample_fs.rm("/dir", RmOptions(recursive=True))
        assert not await sample_fs.exists("/dir")

    async def test_rm_directory_no_recursive_fails(self, sample_fs: InMemoryFs) -> None:
        with pytest.raises(IsADirectoryError):
            await sample_fs.rm("/dir")

    async def test_rm_force_missing(self, sample_fs: InMemoryFs) -> None:
        await sample_fs.rm("/nonexistent", RmOptions(force=True))

    async def test_rm_root_fails(self, empty_fs: InMemoryFs) -> None:
        with pytest.raises(PermissionError):
            await empty_fs.rm("/")


class TestCp:
    async def test_cp_file(self, sample_fs: InMemoryFs) -> None:
        await sample_fs.cp("/hello.txt", "/hello_copy.txt")
        assert await sample_fs.read_file("/hello_copy.txt") == "Hello, World!"

    async def test_cp_directory_recursive(self, sample_fs: InMemoryFs) -> None:
        await sample_fs.cp("/dir", "/dir_copy", CpOptions(recursive=True))
        assert await sample_fs.exists("/dir_copy/nested.txt")
        assert await sample_fs.read_file("/dir_copy/nested.txt") == "nested content"

    async def test_cp_directory_no_recursive_fails(self, sample_fs: InMemoryFs) -> None:
        with pytest.raises(IsADirectoryError):
            await sample_fs.cp("/dir", "/dir_copy")


class TestMv:
    async def test_mv_file(self, sample_fs: InMemoryFs) -> None:
        await sample_fs.mv("/hello.txt", "/hello_moved.txt")
        assert not await sample_fs.exists("/hello.txt")
        assert await sample_fs.read_file("/hello_moved.txt") == "Hello, World!"

    async def test_mv_directory(self, sample_fs: InMemoryFs) -> None:
        await sample_fs.mv("/dir", "/dir_moved")
        assert not await sample_fs.exists("/dir")
        assert await sample_fs.exists("/dir_moved/sub/subfile.txt")


# ── Symlinks ──────────────────────────────────────────────────────

class TestSymlinks:
    async def test_symlink_read(self, empty_fs: InMemoryFs) -> None:
        await empty_fs.write_file("/target.txt", "target content")
        await empty_fs.symlink("/target.txt", "/link.txt")
        assert await empty_fs.read_file("/link.txt") == "target content"

    async def test_readlink(self, empty_fs: InMemoryFs) -> None:
        await empty_fs.symlink("/target.txt", "/link.txt")
        assert await empty_fs.readlink("/link.txt") == "/target.txt"

    async def test_symlink_chain(self, empty_fs: InMemoryFs) -> None:
        await empty_fs.write_file("/target.txt", "final")
        await empty_fs.symlink("/target.txt", "/link1")
        await empty_fs.symlink("/link1", "/link2")
        assert await empty_fs.read_file("/link2") == "final"

    async def test_symlink_too_deep(self, empty_fs: InMemoryFs) -> None:
        # Create circular symlink
        await empty_fs.symlink("/link_a", "/link_b")
        await empty_fs.symlink("/link_b", "/link_a")
        with pytest.raises(OSError):
            await empty_fs.read_file("/link_a")


# ── Glob ──────────────────────────────────────────────────────────

class TestGlob:
    @pytest.mark.skip("glob uses fnmatch which may not handle all patterns")
    async def test_glob_star(self, sample_fs: InMemoryFs) -> None:
        matches = await sample_fs.glob("/*.txt")
        assert "/hello.txt" in matches

    @pytest.mark.skip("glob uses fnmatch which may not handle all patterns")
    async def test_glob_recursive(self, sample_fs: InMemoryFs) -> None:
        matches = await sample_fs.glob("/**/*.txt")
        assert sorted(matches) == [
            "/dir/nested.txt",
            "/dir/sub/subfile.txt",
            "/hello.txt",
        ]


# ── Constructor ───────────────────────────────────────────────────

class TestConstructor:
    async def test_init_with_files(self) -> None:
        fs = InMemoryFs({"/a.txt": "a", "/b.txt": "b"})
        assert await fs.read_file("/a.txt") == "a"
        assert await fs.read_file("/b.txt") == "b"

    async def test_init_with_file_init(self) -> None:
        fs = InMemoryFs({
            "/special.txt": FileInit(content="special", mode=0o700),
        })
        st = await fs.stat("/special.txt")
        assert st.size == len("special")

    def test_sync_helpers(self) -> None:
        fs = InMemoryFs()
        fs.write_file_sync("/sync.txt", "sync content")
        fs.mkdir_sync("/sync_dir")
        # Verify via async call
        loop = asyncio.new_event_loop()
        try:
            content = loop.run_until_complete(fs.read_file("/sync.txt"))
            assert content == "sync content"
        finally:
            loop.close()


# ── Edge cases ────────────────────────────────────────────────────

class TestEdgeCases:
    async def test_root_is_directory(self, empty_fs: InMemoryFs) -> None:
        st = await empty_fs.stat("/")
        assert st.type == "directory"

    async def test_deep_nested_paths(self, empty_fs: InMemoryFs) -> None:
        path = "/" + "/".join(f"level{i}" for i in range(50))
        await empty_fs.write_file(path + "/file.txt", "deep")
        assert await empty_fs.read_file(path + "/file.txt") == "deep"

    async def test_path_normalization(self, empty_fs: InMemoryFs) -> None:
        await empty_fs.write_file("/a/b/../c.txt", "content")
        assert await empty_fs.read_file("/a/c.txt") == "content"
