"""Local FileSystem adapter."""

from __future__ import annotations

import asyncio
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from py_shell.fs.interface import (
    CpOptions,
    FileContent,
    FileSystem,
    FileSystemDirent,
    FileSystemEntryType,
    FsStat,
    MkdirOptions,
    RmOptions,
)
from py_shell.fs.path_utils import create_enoent, create_enotdir, join_path, normalize_path


class LocalFileSystem(FileSystem):
    def __init__(self, root: str | Path = ".") -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _assert_inside_root(self, path: Path) -> Path:
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise PermissionError(f"path escapes filesystem root: '{path}'") from exc
        return resolved

    def _to_os(self, path: str) -> Path:
        norm = normalize_path(path)
        raw = self._root if norm == "/" else self._root / norm.lstrip("/")
        self._assert_inside_root(raw.parent if raw != self._root else raw)
        return raw

    def _target(self, path: str, operation: str) -> Path:
        os_path = self._to_os(path)
        if not os_path.exists():
            raise create_enoent(path, operation)
        return self._assert_inside_root(os_path)

    def _to_virtual(self, os_path: Path) -> str:
        rel = self._assert_inside_root(os_path).relative_to(self._root)
        return "/" if str(rel) == "." else "/" + str(rel).replace(os.sep, "/")

    def _make_stat_from_os(self, st: os.stat_result, entry_type: FileSystemEntryType) -> FsStat:
        return FsStat(
            type=entry_type,
            size=st.st_size,
            mtime=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
            mode=st.st_mode & 0o777,
        )

    async def read_file(self, path: str) -> str:
        os_path = self._target(path, "readFile")
        return await asyncio.to_thread(os_path.read_text, encoding="utf-8")

    async def read_file_bytes(self, path: str) -> bytes:
        os_path = self._target(path, "readFile")
        return await asyncio.to_thread(os_path.read_bytes)

    async def write_file(self, path: str, content: str) -> None:
        os_path = self._to_os(path)
        os_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(os_path.write_text, content, encoding="utf-8")

    async def write_file_bytes(self, path: str, content: bytes) -> None:
        os_path = self._to_os(path)
        os_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(os_path.write_bytes, content)

    async def append_file(self, path: str, content: FileContent) -> None:
        os_path = self._to_os(path)
        os_path.parent.mkdir(parents=True, exist_ok=True)
        data = content if isinstance(content, bytes) else content.encode("utf-8")

        def append() -> None:
            with open(os_path, "ab") as f:
                f.write(data)

        await asyncio.to_thread(append)

    async def exists(self, path: str) -> bool:
        try:
            self._target(path, "exists")
            return True
        except (FileNotFoundError, PermissionError):
            return False

    async def stat(self, path: str) -> FsStat:
        os_path = self._target(path, "stat")
        st = await asyncio.to_thread(os_path.stat)
        entry_type: FileSystemEntryType = "directory" if os_path.is_dir() else "file"
        return self._make_stat_from_os(st, entry_type)

    async def lstat(self, path: str) -> FsStat:
        os_path = self._to_os(path)
        if not os_path.exists() and not os_path.is_symlink():
            raise create_enoent(path, "lstat")
        st = await asyncio.to_thread(os.lstat, os_path)
        if os_path.is_symlink():
            entry_type: FileSystemEntryType = "symlink"
        elif os_path.is_dir():
            entry_type = "directory"
        else:
            entry_type = "file"
        return self._make_stat_from_os(st, entry_type)

    async def mkdir(self, path: str, options: MkdirOptions | None = None) -> None:
        os_path = self._to_os(path)
        opts = options or MkdirOptions()
        await asyncio.to_thread(os_path.mkdir, parents=opts.recursive, exist_ok=opts.recursive)

    async def readdir(self, path: str) -> list[str]:
        os_path = self._target(path, "readdir")
        if not os_path.is_dir():
            raise create_enotdir(path, "readdir")
        return sorted(await asyncio.to_thread(lambda: [c.name for c in os_path.iterdir()]))

    async def readdir_with_file_types(self, path: str) -> list[FileSystemDirent]:
        os_path = self._target(path, "readdir")
        if not os_path.is_dir():
            raise create_enotdir(path, "readdir")

        def read() -> list[FileSystemDirent]:
            result = []
            for child in os_path.iterdir():
                if child.is_symlink():
                    entry_type: FileSystemEntryType = "symlink"
                elif child.is_dir():
                    entry_type = "directory"
                else:
                    entry_type = "file"
                result.append(FileSystemDirent(name=child.name, type=entry_type))
            return sorted(result, key=lambda e: e.name)

        return await asyncio.to_thread(read)

    async def rm(self, path: str, options: RmOptions | None = None) -> None:
        opts = options or RmOptions()
        os_path = self._to_os(path)

        def remove() -> None:
            if not os_path.exists() and not os_path.is_symlink():
                if not opts.force:
                    raise create_enoent(path, "rm")
                return
            if os_path.is_dir() and not os_path.is_symlink():
                if not opts.recursive:
                    raise IsADirectoryError(f"EISDIR: is a directory, rm '{path}'")
                shutil.rmtree(os_path)
            else:
                os_path.unlink()

        await asyncio.to_thread(remove)

    async def cp(self, src: str, dest: str, options: CpOptions | None = None) -> None:
        opts = options or CpOptions()
        src_path = self._target(src, "cp")
        dest_path = self._to_os(dest)

        def copy() -> None:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            if src_path.is_dir():
                if not opts.recursive:
                    raise IsADirectoryError(f"EISDIR: is a directory, cp '{src}'")
                shutil.copytree(src_path, dest_path, dirs_exist_ok=True)
            else:
                shutil.copy2(src_path, dest_path)

        await asyncio.to_thread(copy)

    async def mv(self, src: str, dest: str) -> None:
        src_path = self._target(src, "mv")
        dest_path = self._to_os(dest)

        def move() -> None:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_path), str(dest_path))

        await asyncio.to_thread(move)

    async def symlink(self, target: str, link_path: str) -> None:
        os_path = self._to_os(link_path)
        os_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(os.symlink, target, os_path)

    async def readlink(self, path: str) -> str:
        os_path = self._to_os(path)
        if not os_path.is_symlink():
            raise OSError(f"EINVAL: not a symlink, readlink '{path}'")
        return await asyncio.to_thread(os.readlink, os_path)

    async def realpath(self, path: str) -> str:
        return self._to_virtual(self._target(path, "realpath"))

    def resolve_path(self, base: str, path: str) -> str:
        if path.startswith("/"):
            return normalize_path(path)
        return normalize_path(join_path(base, path))

    async def glob(self, pattern: str) -> list[str]:
        pat = normalize_path(pattern).lstrip("/")

        def run() -> list[str]:
            return [self._to_virtual(p) for p in self._root.rglob(pat) if self._assert_inside_root(p)]

        return sorted(set(await asyncio.to_thread(run)))
