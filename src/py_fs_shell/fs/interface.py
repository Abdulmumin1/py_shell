"""Minimal filesystem abstraction interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections.abc import Awaitable
from typing import Callable, Literal, TypeAlias, Union

# ── Types ────────────────────────────────────────────────────────────

FileSystemEntryType: TypeAlias = Literal["file", "directory", "symlink"]
BufferEncoding: TypeAlias = Literal[
    "utf8", "utf-8", "ascii", "binary", "base64", "hex", "latin1"
]

FileContent: TypeAlias = Union[str, bytes]


# ── Stat / Dirent ────────────────────────────────────────────────────

@dataclass(frozen=True)
class FsStat:
    """Stat result returned by FileSystem.stat / FileSystem.lstat."""

    type: FileSystemEntryType
    size: int
    mtime: datetime
    mode: int = field(default=0o644)


@dataclass(frozen=True)
class FileSystemDirent:
    """Directory entry returned by FileSystem.readdir_with_file_types."""

    name: str
    type: FileSystemEntryType


# ── Options ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MkdirOptions:
    recursive: bool = False


@dataclass(frozen=True)
class RmOptions:
    recursive: bool = False
    force: bool = False


@dataclass(frozen=True)
class CpOptions:
    recursive: bool = False


# ── InMemoryFs constructor helpers ───────────────────────────────────

@dataclass
class FileEntry:
    type: Literal["file"]
    content: FileContent
    mode: int = 0o644
    mtime: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class DirectoryEntry:
    type: Literal["directory"]
    mode: int = 0o755
    mtime: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class SymlinkEntry:
    type: Literal["symlink"]
    target: str
    mode: int = 0o777
    mtime: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class LazyFileEntry:
    type: Literal["file"]
    lazy: Callable[[], FileContent | Awaitable[FileContent]]
    mode: int = 0o644
    mtime: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


FsEntry: TypeAlias = Union[FileEntry, LazyFileEntry, DirectoryEntry, SymlinkEntry]


@dataclass
class FileInit:
    content: FileContent
    mode: int | None = None
    mtime: datetime | None = None


LazyFileProvider: TypeAlias = Callable[..., FileContent | Awaitable[FileContent]]
InitialFiles: TypeAlias = dict[str, FileContent | FileInit | FsEntry | LazyFileProvider]


# ── FileSystem Protocol ──────────────────────────────────────────────

class FileSystem(ABC):
    """Minimal filesystem abstraction.
    Contracts:
       - ``read_file`` / ``read_file_bytes`` / ``stat`` / ``lstat`` raise
        ``FileNotFoundError`` when the path does not exist (never return None).
      - ``exists`` never raises.
      - ``glob`` returns absolute paths matching the pattern, sorted.
    """

    @abstractmethod
    async def read_file(self, path: str) -> str:
        """Read file as string. Raises FileNotFoundError if missing."""
        ...

    @abstractmethod
    async def read_file_bytes(self, path: str) -> bytes:
        """Read file as bytes. Raises FileNotFoundError if missing."""
        ...

    @abstractmethod
    async def write_file(self, path: str, content: str) -> None:
        """Write string content to file."""
        ...

    @abstractmethod
    async def write_file_bytes(self, path: str, content: bytes) -> None:
        """Write bytes content to file."""
        ...

    @abstractmethod
    async def append_file(self, path: str, content: FileContent) -> None:
        """Append content to file."""
        ...

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Check if path exists. Never raises."""
        ...

    @abstractmethod
    async def stat(self, path: str) -> FsStat:
        """Stat following symlinks. Raises FileNotFoundError if missing."""
        ...

    @abstractmethod
    async def lstat(self, path: str) -> FsStat:
        """Stat not following final symlink. Raises FileNotFoundError if missing."""
        ...

    @abstractmethod
    async def mkdir(self, path: str, options: MkdirOptions | None = None) -> None:
        """Create directory."""
        ...

    @abstractmethod
    async def readdir(self, path: str) -> list[str]:
        """List directory entries (names only)."""
        ...

    @abstractmethod
    async def readdir_with_file_types(self, path: str) -> list[FileSystemDirent]:
        """List directory entries with types."""
        ...

    @abstractmethod
    async def rm(self, path: str, options: RmOptions | None = None) -> None:
        """Remove file or directory."""
        ...

    @abstractmethod
    async def cp(self, src: str, dest: str, options: CpOptions | None = None) -> None:
        """Copy file or directory."""
        ...

    @abstractmethod
    async def mv(self, src: str, dest: str) -> None:
        """Move file or directory."""
        ...

    @abstractmethod
    async def symlink(self, target: str, link_path: str) -> None:
        """Create symbolic link."""
        ...

    @abstractmethod
    async def readlink(self, path: str) -> str:
        """Read symlink target."""
        ...

    @abstractmethod
    async def realpath(self, path: str) -> str:
        """Resolve all symlinks to get canonical path."""
        ...

    @abstractmethod
    def resolve_path(self, base: str, path: str) -> str:
        """Resolve a relative path against a base."""
        ...

    @abstractmethod
    async def glob(self, pattern: str) -> list[str]:
        """Return sorted list of absolute paths matching glob pattern."""
        ...
