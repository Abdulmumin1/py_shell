"""Durable workspace abstractions backed by metadata and blob stores."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from py_fs_shell.fs.interface import (
    CpOptions,
    FileSystem,
    FileSystemDirent,
    FileSystemEntryType,
    FsStat,
    MkdirOptions,
    RmOptions,
)
from py_fs_shell.fs.path_utils import (
    create_eisdir,
    create_enoent,
    create_enotdir,
    join_path,
    normalize_path,
    parent_dir,
    split_path,
)


def _basename(norm: str, operation: str) -> str:
    name = norm.rsplit("/", 1)[-1] if norm != "/" else ""
    if not name:
        raise create_eisdir(norm, operation)
    return name


@dataclass(frozen=True)
class WorkspaceEntry:
    path: str
    type: FileSystemEntryType
    size: int = 0
    mtime: datetime = None  # type: ignore[assignment]
    mode: int = 0o644
    blob_key: str | None = None
    target: str | None = None

    def __post_init__(self) -> None:
        if self.mtime is None:
            object.__setattr__(self, "mtime", datetime.now(UTC))


class MetadataStore(ABC):
    @abstractmethod
    async def get(self, path: str) -> WorkspaceEntry | None: ...

    @abstractmethod
    async def put(self, entry: WorkspaceEntry) -> None: ...

    @abstractmethod
    async def delete(self, path: str) -> None: ...

    @abstractmethod
    async def list_children(self, path: str) -> list[WorkspaceEntry]: ...

    @abstractmethod
    async def list_all(self) -> list[WorkspaceEntry]: ...


class BlobStore(ABC):
    @abstractmethod
    async def put(self, data: bytes) -> str: ...

    @abstractmethod
    async def get(self, key: str) -> bytes: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...


class MemoryMetadataStore(MetadataStore):
    def __init__(self) -> None:
        self._entries: dict[str, WorkspaceEntry] = {}

    async def get(self, path: str) -> WorkspaceEntry | None:
        return self._entries.get(normalize_path(path))

    async def put(self, entry: WorkspaceEntry) -> None:
        self._entries[normalize_path(entry.path)] = replace(entry, path=normalize_path(entry.path))

    async def delete(self, path: str) -> None:
        self._entries.pop(normalize_path(path), None)

    async def list_children(self, path: str) -> list[WorkspaceEntry]:
        base = normalize_path(path)
        prefix = "/" if base == "/" else base + "/"
        results = []
        for entry_path, entry in self._entries.items():
            if entry_path == base or not entry_path.startswith(prefix):
                continue
            rest = entry_path[len(prefix):]
            if rest and "/" not in rest:
                results.append(entry)
        return sorted(results, key=lambda e: e.path)

    async def list_all(self) -> list[WorkspaceEntry]:
        return sorted(self._entries.values(), key=lambda e: e.path)


class SQLiteMetadataStore(MetadataStore):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, detect_types=sqlite3.PARSE_DECLTYPES)

    def _init_db(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS entries (
                    path TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    mtime TEXT NOT NULL,
                    mode INTEGER NOT NULL,
                    blob_key TEXT,
                    target TEXT
                )
                """
            )

    def _row_to_entry(self, row: tuple) -> WorkspaceEntry:
        return WorkspaceEntry(
            path=row[0],
            type=row[1],
            size=row[2],
            mtime=datetime.fromisoformat(row[3]),
            mode=row[4],
            blob_key=row[5],
            target=row[6],
        )

    async def get(self, path: str) -> WorkspaceEntry | None:
        norm = normalize_path(path)
        async with self._lock:
            def run() -> WorkspaceEntry | None:
                with self._connect() as db:
                    row = db.execute(
                        "SELECT path,type,size,mtime,mode,blob_key,target FROM entries WHERE path=?",
                        (norm,),
                    ).fetchone()
                    return self._row_to_entry(row) if row else None
            return await asyncio.to_thread(run)

    async def put(self, entry: WorkspaceEntry) -> None:
        entry = replace(entry, path=normalize_path(entry.path))
        async with self._lock:
            def run() -> None:
                with self._connect() as db:
                    db.execute(
                        """
                        INSERT INTO entries(path,type,size,mtime,mode,blob_key,target)
                        VALUES(?,?,?,?,?,?,?)
                        ON CONFLICT(path) DO UPDATE SET
                            type=excluded.type,
                            size=excluded.size,
                            mtime=excluded.mtime,
                            mode=excluded.mode,
                            blob_key=excluded.blob_key,
                            target=excluded.target
                        """,
                        (
                            entry.path,
                            entry.type,
                            entry.size,
                            entry.mtime.isoformat(),
                            entry.mode,
                            entry.blob_key,
                            entry.target,
                        ),
                    )
            await asyncio.to_thread(run)

    async def delete(self, path: str) -> None:
        norm = normalize_path(path)
        async with self._lock:
            def run() -> None:
                with self._connect() as db:
                    db.execute("DELETE FROM entries WHERE path=?", (norm,))
            await asyncio.to_thread(run)

    async def list_children(self, path: str) -> list[WorkspaceEntry]:
        base = normalize_path(path)
        prefix = "/" if base == "/" else base + "/"
        async with self._lock:
            def run() -> list[WorkspaceEntry]:
                with self._connect() as db:
                    rows = db.execute(
                        "SELECT path,type,size,mtime,mode,blob_key,target FROM entries WHERE path LIKE ? ORDER BY path",
                        (prefix + "%",),
                    ).fetchall()
                entries = []
                for row in rows:
                    entry_path = row[0]
                    rest = entry_path[len(prefix):]
                    if rest and "/" not in rest:
                        entries.append(self._row_to_entry(row))
                return entries
            return await asyncio.to_thread(run)

    async def list_all(self) -> list[WorkspaceEntry]:
        async with self._lock:
            def run() -> list[WorkspaceEntry]:
                with self._connect() as db:
                    rows = db.execute(
                        "SELECT path,type,size,mtime,mode,blob_key,target FROM entries ORDER BY path"
                    ).fetchall()
                    return [self._row_to_entry(row) for row in rows]
            return await asyncio.to_thread(run)


class MemoryBlobStore(BlobStore):
    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    async def put(self, data: bytes) -> str:
        key = hashlib.sha256(data).hexdigest()
        self._blobs[key] = bytes(data)
        return key

    async def get(self, key: str) -> bytes:
        try:
            return self._blobs[key]
        except KeyError as exc:
            raise FileNotFoundError(key) from exc

    async def delete(self, key: str) -> None:
        self._blobs.pop(key, None)


class LocalBlobStore(BlobStore):
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / key[2:]

    async def put(self, data: bytes) -> str:
        key = hashlib.sha256(data).hexdigest()
        path = self._path(key)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_bytes, data)
        return key

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise FileNotFoundError(key)
        return await asyncio.to_thread(path.read_bytes)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            await asyncio.to_thread(path.unlink)


class S3MetadataStore(MetadataStore):
    def __init__(self, bucket: str, key: str, client=None) -> None:
        self.bucket = bucket
        self.key = key.strip("/")
        self._lock = asyncio.Lock()
        if client is None:
            try:
                import boto3
            except ImportError as exc:
                raise ImportError("S3MetadataStore requires boto3. Install with `pip install py-fs-shell[s3]`.") from exc
            client = boto3.client("s3")
        self.client = client

    def _entry_to_dict(self, entry: WorkspaceEntry) -> dict:
        return {
            "path": entry.path,
            "type": entry.type,
            "size": entry.size,
            "mtime": entry.mtime.isoformat(),
            "mode": entry.mode,
            "blob_key": entry.blob_key,
            "target": entry.target,
        }

    def _dict_to_entry(self, data: dict) -> WorkspaceEntry:
        return WorkspaceEntry(
            path=data["path"],
            type=data["type"],
            size=data.get("size", 0),
            mtime=datetime.fromisoformat(data["mtime"]),
            mode=data.get("mode", 0o644),
            blob_key=data.get("blob_key"),
            target=data.get("target"),
        )

    def _load_sync(self) -> dict[str, WorkspaceEntry]:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=self.key)
        except Exception as exc:
            name = exc.__class__.__name__
            code = getattr(getattr(exc, "response", {}), "get", lambda *_: {})('Error', {}).get('Code')
            if isinstance(exc, FileNotFoundError) or name in {"NoSuchKey", "NoSuchBucket"} or code in {"NoSuchKey", "404", "NoSuchBucket"}:
                return {}
            raise
        raw = obj["Body"].read()
        if not raw:
            return {}
        payload = json.loads(raw.decode("utf-8"))
        return {
            path: self._dict_to_entry(entry)
            for path, entry in payload.get("entries", {}).items()
        }

    def _save_sync(self, entries: dict[str, WorkspaceEntry]) -> None:
        payload = {
            "version": 1,
            "entries": {
                path: self._entry_to_dict(entry)
                for path, entry in sorted(entries.items())
            },
        }
        self.client.put_object(
            Bucket=self.bucket,
            Key=self.key,
            Body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            ContentType="application/json",
        )

    async def get(self, path: str) -> WorkspaceEntry | None:
        norm = normalize_path(path)
        async with self._lock:
            entries = await asyncio.to_thread(self._load_sync)
            return entries.get(norm)

    async def put(self, entry: WorkspaceEntry) -> None:
        entry = replace(entry, path=normalize_path(entry.path))
        async with self._lock:
            entries = await asyncio.to_thread(self._load_sync)
            entries[entry.path] = entry
            await asyncio.to_thread(self._save_sync, entries)

    async def delete(self, path: str) -> None:
        norm = normalize_path(path)
        async with self._lock:
            entries = await asyncio.to_thread(self._load_sync)
            entries.pop(norm, None)
            await asyncio.to_thread(self._save_sync, entries)

    async def list_children(self, path: str) -> list[WorkspaceEntry]:
        base = normalize_path(path)
        prefix = "/" if base == "/" else base + "/"
        async with self._lock:
            entries = await asyncio.to_thread(self._load_sync)
        results = []
        for entry_path, entry in entries.items():
            if entry_path == base or not entry_path.startswith(prefix):
                continue
            rest = entry_path[len(prefix):]
            if rest and "/" not in rest:
                results.append(entry)
        return sorted(results, key=lambda e: e.path)

    async def list_all(self) -> list[WorkspaceEntry]:
        async with self._lock:
            entries = await asyncio.to_thread(self._load_sync)
        return sorted(entries.values(), key=lambda e: e.path)


class S3BlobStore(BlobStore):
    def __init__(self, bucket: str, prefix: str = "", client=None) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        if client is None:
            try:
                import boto3
            except ImportError as exc:
                raise ImportError("S3BlobStore requires boto3. Install with `pip install py-fs-shell[s3]`.") from exc
            client = boto3.client("s3")
        self.client = client

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    async def put(self, data: bytes) -> str:
        key = hashlib.sha256(data).hexdigest()
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket,
            Key=self._key(key),
            Body=data,
        )
        return key

    async def get(self, key: str) -> bytes:
        def run() -> bytes:
            obj = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
            return obj["Body"].read()
        return await asyncio.to_thread(run)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=self._key(key))


class Workspace:
    def __init__(self, metadata: MetadataStore, blobs: BlobStore) -> None:
        self.metadata = metadata
        self.blobs = blobs

    async def init(self) -> Workspace:
        if await self.metadata.get("/") is None:
            await self.metadata.put(WorkspaceEntry(path="/", type="directory", mode=0o755))
        return self

    def fs(self) -> WorkspaceFileSystem:
        return WorkspaceFileSystem(self)

    def state(self):
        from py_fs_shell.memory_backend import FileSystemStateBackend
        return FileSystemStateBackend(self.fs())

    async def _ensure_parent(self, path: str) -> None:
        parent = parent_dir(path)
        if parent != "/" and await self.metadata.get(parent) is None:
            await self.mkdir(parent, MkdirOptions(recursive=True))
        entry = await self.metadata.get(parent)
        if entry is None:
            raise create_enoent(parent)
        if entry.type != "directory":
            raise create_enotdir(parent)

    async def _resolve(self, path: str, follow_final: bool = True, depth: int = 0) -> tuple[str, WorkspaceEntry]:
        if depth > 40:
            raise OSError(f"ELOOP: too many symbolic links: '{path}'")
        norm = normalize_path(path)
        if norm == "/":
            entry = await self.metadata.get("/")
            if entry is None:
                raise create_enoent(path)
            return "/", entry

        current = "/"
        segments = split_path(norm)
        for index, segment in enumerate(segments):
            candidate = join_path(current, segment)
            entry = await self.metadata.get(candidate)
            if entry is None:
                raise create_enoent(path)

            is_last = index == len(segments) - 1
            if entry.type == "symlink" and (follow_final or not is_last):
                target = entry.target or ""
                resolved = normalize_path(target) if target.startswith("/") else join_path(parent_dir(candidate), target)
                rest = segments[index + 1 :]
                if rest:
                    resolved = join_path(resolved, *rest)
                return await self._resolve(resolved, follow_final=follow_final, depth=depth + 1)

            current = candidate

        final_entry = await self.metadata.get(current)
        if final_entry is None:
            raise create_enoent(path)
        return current, final_entry

    async def read_file_bytes(self, path: str) -> bytes:
        _, entry = await self._resolve(path)
        if entry.type == "directory":
            raise create_eisdir(path, "readFile")
        if entry.type != "file" or not entry.blob_key:
            raise create_enoent(path, "readFile")
        return await self.blobs.get(entry.blob_key)

    async def read_file(self, path: str) -> str:
        return (await self.read_file_bytes(path)).decode("utf-8")

    async def write_file_bytes(self, path: str, content: bytes) -> None:
        norm = normalize_path(path)
        if norm == "/":
            raise create_eisdir(path, "writeFile")
        await self._ensure_parent(norm)
        key = await self.blobs.put(content)
        await self.metadata.put(
            WorkspaceEntry(
                path=norm,
                type="file",
                size=len(content),
                mode=0o644,
                blob_key=key,
            )
        )

    async def write_file(self, path: str, content: str) -> None:
        await self.write_file_bytes(path, content.encode("utf-8"))

    async def append_file(self, path: str, content: str | bytes) -> None:
        existing = await self.read_file_bytes(path)
        data = content if isinstance(content, bytes) else content.encode("utf-8")
        await self.write_file_bytes(path, existing + data)

    async def exists(self, path: str) -> bool:
        try:
            await self._resolve(path)
            return True
        except (FileNotFoundError, OSError):
            return False

    async def stat(self, path: str) -> FsStat:
        _, entry = await self._resolve(path)
        return FsStat(entry.type, entry.size, entry.mtime, entry.mode)

    async def lstat(self, path: str) -> FsStat:
        _, entry = await self._resolve(path, follow_final=False)
        return FsStat(entry.type, entry.size, entry.mtime, entry.mode)

    async def mkdir(self, path: str, options: MkdirOptions | None = None) -> None:
        norm = normalize_path(path)
        opts = options or MkdirOptions()
        if norm == "/":
            await self.init()
            return
        if opts.recursive:
            current = "/"
            await self.init()
            for segment in split_path(norm):
                current = join_path(current, segment)
                entry = await self.metadata.get(current)
                if entry is None:
                    await self.metadata.put(WorkspaceEntry(path=current, type="directory", mode=0o755))
                elif entry.type != "directory":
                    raise create_enotdir(current)
            return
        await self._ensure_parent(norm)
        if await self.metadata.get(norm) is not None:
            raise FileExistsError(norm)
        await self.metadata.put(WorkspaceEntry(path=norm, type="directory", mode=0o755))

    async def readdir(self, path: str) -> list[str]:
        _, entry = await self._resolve(path)
        if entry.type != "directory":
            raise create_enotdir(path, "readdir")
        return [child.path.rsplit("/", 1)[-1] for child in await self.metadata.list_children(path)]

    async def readdir_with_file_types(self, path: str) -> list[FileSystemDirent]:
        _, entry = await self._resolve(path)
        if entry.type != "directory":
            raise create_enotdir(path, "readdir")
        return [
            FileSystemDirent(name=child.path.rsplit("/", 1)[-1], type=child.type)
            for child in await self.metadata.list_children(path)
        ]

    async def rm(self, path: str, options: RmOptions | None = None) -> None:
        opts = options or RmOptions()
        norm = normalize_path(path)
        if norm == "/":
            raise PermissionError("rm: cannot remove root directory '/'")
        entry = await self.metadata.get(norm)
        if entry is None:
            if opts.force:
                return
            raise create_enoent(path, "rm")
        if entry.type == "directory":
            children = await self.metadata.list_children(norm)
            if children and not opts.recursive:
                raise IsADirectoryError(f"EISDIR: is a directory, rm '{path}'")
            for child in list(await self.metadata.list_all()):
                if child.path == norm or child.path.startswith(norm.rstrip("/") + "/"):
                    await self.metadata.delete(child.path)
            return
        await self.metadata.delete(norm)

    async def cp(self, src: str, dest: str, options: CpOptions | None = None) -> None:
        opts = options or CpOptions()
        src_norm, entry = await self._resolve(src, follow_final=False)
        dest_norm = normalize_path(dest)
        _basename(dest_norm, "cp")
        if entry.type == "directory":
            if not opts.recursive:
                raise IsADirectoryError(f"EISDIR: is a directory, cp '{src}'")
            await self.mkdir(dest_norm, MkdirOptions(recursive=True))
            for child in await self.metadata.list_all():
                if child.path == src_norm or not child.path.startswith(src_norm.rstrip("/") + "/"):
                    continue
                rel = child.path[len(src_norm.rstrip("/")):].lstrip("/")
                await self.metadata.put(replace(child, path=join_path(dest_norm, rel)))
            return
        await self._ensure_parent(dest_norm)
        await self.metadata.put(replace(entry, path=dest_norm))

    async def mv(self, src: str, dest: str) -> None:
        _basename(normalize_path(dest), "mv")
        await self.cp(src, dest, CpOptions(recursive=True))
        await self.rm(src, RmOptions(recursive=True, force=False))

    async def symlink(self, target: str, link_path: str) -> None:
        norm = normalize_path(link_path)
        _basename(norm, "symlink")
        await self._ensure_parent(norm)
        await self.metadata.put(
            WorkspaceEntry(path=norm, type="symlink", size=len(target), mode=0o777, target=target)
        )

    async def readlink(self, path: str) -> str:
        _, entry = await self._resolve(path, follow_final=False)
        if entry.type != "symlink" or entry.target is None:
            raise OSError(f"EINVAL: not a symlink, readlink '{path}'")
        return entry.target

    async def realpath(self, path: str) -> str:
        resolved, _ = await self._resolve(path)
        return resolved

    async def glob(self, pattern: str) -> list[str]:
        pat = pattern[1:] if pattern.startswith("/") else pattern
        results = []
        for entry in await self.metadata.list_all():
            if entry.path == "/":
                continue
            rel = entry.path[1:]
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(entry.path, pattern):
                results.append(entry.path)
        return sorted(set(results))


class WorkspaceFileSystem(FileSystem):
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    async def read_file(self, path: str) -> str:
        return await self.workspace.read_file(path)

    async def read_file_bytes(self, path: str) -> bytes:
        return await self.workspace.read_file_bytes(path)

    async def write_file(self, path: str, content: str) -> None:
        await self.workspace.write_file(path, content)

    async def write_file_bytes(self, path: str, content: bytes) -> None:
        await self.workspace.write_file_bytes(path, content)

    async def append_file(self, path: str, content: str | bytes) -> None:
        await self.workspace.append_file(path, content)

    async def exists(self, path: str) -> bool:
        return await self.workspace.exists(path)

    async def stat(self, path: str) -> FsStat:
        return await self.workspace.stat(path)

    async def lstat(self, path: str) -> FsStat:
        return await self.workspace.lstat(path)

    async def mkdir(self, path: str, options: MkdirOptions | None = None) -> None:
        await self.workspace.mkdir(path, options)

    async def readdir(self, path: str) -> list[str]:
        return await self.workspace.readdir(path)

    async def readdir_with_file_types(self, path: str) -> list[FileSystemDirent]:
        return await self.workspace.readdir_with_file_types(path)

    async def rm(self, path: str, options: RmOptions | None = None) -> None:
        await self.workspace.rm(path, options)

    async def cp(self, src: str, dest: str, options: CpOptions | None = None) -> None:
        await self.workspace.cp(src, dest, options)

    async def mv(self, src: str, dest: str) -> None:
        await self.workspace.mv(src, dest)

    async def symlink(self, target: str, link_path: str) -> None:
        await self.workspace.symlink(target, link_path)

    async def readlink(self, path: str) -> str:
        return await self.workspace.readlink(path)

    async def realpath(self, path: str) -> str:
        return await self.workspace.realpath(path)

    def resolve_path(self, base: str, path: str) -> str:
        if path.startswith("/"):
            return normalize_path(path)
        return normalize_path(join_path(base, path))

    async def glob(self, pattern: str) -> list[str]:
        return await self.workspace.glob(pattern)


async def memory() -> Workspace:
    return await Workspace(MemoryMetadataStore(), MemoryBlobStore()).init()


async def local(root: str | Path) -> Workspace:
    root = Path(root)
    return await Workspace(
        SQLiteMetadataStore(root / "metadata.sqlite3"),
        LocalBlobStore(root / "blobs"),
    ).init()


async def s3(
    bucket: str,
    prefix: str = "",
    client=None,
    metadata: MetadataStore | None = None,
    metadata_key: str | None = None,
) -> Workspace:
    clean_prefix = prefix.strip("/")
    metadata_key = metadata_key or (f"{clean_prefix}/.py_fs_shell/metadata.json" if clean_prefix else ".py_fs_shell/metadata.json")
    return await Workspace(
        metadata or S3MetadataStore(bucket=bucket, key=metadata_key, client=client),
        S3BlobStore(bucket=bucket, prefix=clean_prefix, client=client),
    ).init()


create_memory_workspace = memory
create_local_workspace = local
create_s3_workspace = s3
