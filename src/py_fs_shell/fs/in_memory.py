"""Tree-based in-memory filesystem implementation."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from py_fs_shell.fs.interface import (
    CpOptions,
    FileContent,
    FileInit,
    FileSystem,
    FileSystemDirent,
    FileSystemEntryType,
    FsStat,
    InitialFiles,
    LazyFileProvider,
    MkdirOptions,
    RmOptions,
)
from py_fs_shell.fs.path_utils import (
    DEFAULT_DIR_MODE,
    DEFAULT_FILE_MODE,
    MAX_SYMLINK_DEPTH,
    SYMLINK_MODE,
    create_eisdir,
    create_eloop,
    create_enoent,
    create_eexist,
    create_enotdir,
    join_path,
    normalize_path,
    parent_dir,
    split_path,
    validate_path,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable


# ── Tree node types ──────────────────────────────────────────────────
#
# Storage is a rooted tree where each directory holds a dict of its
# children. This gives O(children) directory listing and natural
# recursive operations instead of scanning every key in a flat map.

@dataclass
class _VFileNode:
    kind: str = "file"
    content: bytes = field(default_factory=bytes)
    mode: int = DEFAULT_FILE_MODE
    mtime: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class _VLazyNode:
    kind: str = "lazy"
    provider: Callable[[], FileContent | Awaitable[FileContent]] = field(default=lambda: b"")
    mode: int = DEFAULT_FILE_MODE
    mtime: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class _VDirNode:
    kind: str = "dir"
    children: dict[str, _VNode] = field(default_factory=dict)
    mode: int = DEFAULT_DIR_MODE
    mtime: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class _VSymlinkNode:
    kind: str = "symlink"
    target: str = ""
    mode: int = SYMLINK_MODE
    mtime: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_VNode = _VFileNode | _VLazyNode | _VDirNode | _VSymlinkNode


@dataclass
class _Located:
    node: _VNode
    parent: _VDirNode
    key: str


# ── Helpers ──────────────────────────────────────────────────────────

_UTF8 = "utf-8"


def _fresh_dir() -> _VDirNode:
    return _VDirNode()


def _kind_to_type(node: _VNode) -> FileSystemEntryType:
    if node.kind in ("file", "lazy"):
        return "file"
    if node.kind == "dir":
        return "directory"
    return "symlink"


def _node_size(node: _VNode) -> int:
    if node.kind == "file":
        return len(node.content)
    if node.kind == "symlink":
        return len(node.target)
    return 0


def _entry_type(v: object) -> str | None:
    if isinstance(v, dict):
        raw = v.get("type")
        return raw if isinstance(raw, str) else None
    raw = getattr(v, "type", None)
    return raw if isinstance(raw, str) else None


def _get_attr(v: object, key: str, default: object = None) -> object:
    if isinstance(v, dict):
        return v.get(key, default)
    return getattr(v, key, default)


def _is_init_obj(v: object) -> bool:
    return isinstance(v, FileInit) or (
        isinstance(v, dict)
        and "content" in v
    )


def _is_lazy_provider(v: FileContent | FileInit | LazyFileProvider) -> bool:
    return callable(v) and not isinstance(v, (str, bytes))


def _to_bytes(content: FileContent) -> bytes:
    if isinstance(content, str):
        return content.encode(_UTF8)
    return content


def _to_str(content: bytes) -> str:
    return content.decode(_UTF8)


def _make_stat(node: _VNode) -> FsStat:
    return FsStat(
        type=_kind_to_type(node),
        size=_node_size(node),
        mtime=node.mtime,
        mode=node.mode,
    )


# ── InMemoryFs class ─────────────────────────────────────────────────

class InMemoryFs(FileSystem):
    """Tree-based in-memory filesystem.

    Stores files/directories in a tree structure for efficient lookups
    and natural recursive operations.
    """

    def __init__(self, initial_files: InitialFiles | None = None) -> None:
        self._tree = _fresh_dir()
        if initial_files:
            for path, value in initial_files.items():
                entry_type = _entry_type(value)
                if entry_type == "directory":
                    node = self._ensure_dir(path, MkdirOptions(recursive=True))
                    node.mode = int(_get_attr(value, "mode", node.mode))
                    mtime = _get_attr(value, "mtime", None)
                    if isinstance(mtime, datetime):
                        node.mtime = mtime
                elif entry_type == "symlink":
                    self._insert_symlink(
                        path,
                        str(_get_attr(value, "target", "")),
                        int(_get_attr(value, "mode", SYMLINK_MODE)),
                        _get_attr(value, "mtime", None),
                    )
                elif entry_type == "file" and _get_attr(value, "lazy", None) is not None:
                    self._insert_lazy(
                        path,
                        _get_attr(value, "lazy"),
                        _get_attr(value, "mode", None),
                        _get_attr(value, "mtime", None),
                    )
                elif entry_type == "file":
                    self._insert_content(
                        path,
                        _get_attr(value, "content", b""),
                        _get_attr(value, "mode", None),
                        _get_attr(value, "mtime", None),
                    )
                elif _is_lazy_provider(value):
                    self._insert_lazy(path, value)
                elif _is_init_obj(value):
                    self._insert_content(
                        path,
                        _get_attr(value, "content", b""),
                        _get_attr(value, "mode", None),
                        _get_attr(value, "mtime", None),
                    )
                else:
                    self._insert_content(path, value)

    # ── Internal tree operations ─────────────────────────────────────

    def _locate(
        self,
        path: str,
        follow_symlinks: bool = True,
        depth: int = 0,
    ) -> _Located:
        """Find a node in the tree. Optionally follow symlinks.

        Raises FileNotFoundError if path doesn't exist.
        """
        if depth > MAX_SYMLINK_DEPTH:
            raise create_eloop(path)

        norm = normalize_path(path)
        if norm == "/":
            return _Located(node=self._tree, parent=self._tree, key="")

        segs = split_path(norm)
        parent = self._tree
        current: _VNode = parent
        key = ""

        current_path = "/"
        for i, seg in enumerate(segs):
            if current.kind != "dir":
                raise create_enotdir(current_path)

            parent = current
            key = seg
            child = parent.children.get(seg)

            if child is None:
                raise create_enoent(path)

            current = child
            current_path = join_path(current_path, seg)

            is_last = i == len(segs) - 1
            if current.kind == "symlink" and (not is_last or follow_symlinks):
                target = current.target
                link_parent = parent_dir(current_path)
                resolved = normalize_path(target) if target.startswith("/") else join_path(link_parent, target)
                rest = segs[i + 1:]
                if rest:
                    resolved = join_path(resolved, *rest)
                return self._locate(resolved, follow_symlinks=True, depth=depth + 1)

        return _Located(node=current, parent=parent, key=key)

    def _resolve_normal(self, path: str) -> _VNode:
        """Resolve path, following symlinks. Returns the node."""
        located = self._locate(path, follow_symlinks=True)
        return located.node

    def _ensure_dir(self, path: str, options: MkdirOptions | None = None) -> _VDirNode:
        """Ensure a directory exists, creating parent dirs if needed.

        Returns the directory node.
        """
        norm = normalize_path(path)
        if norm == "/":
            return self._tree

        segs = split_path(norm)
        current = self._tree

        for i, seg in enumerate(segs):
            child = current.children.get(seg)
            if child is None:
                new_dir = _fresh_dir()
                current.children[seg] = new_dir
                current = new_dir
            elif child.kind == "dir":
                current = child
            elif i == len(segs) - 1:
                # Last segment exists but is not a dir
                if not (options and options.recursive):
                    raise create_eexist(path)
                # Can't replace a file with a dir without removing it first
                raise create_eexist(path)
            else:
                # Intermediate exists but is not a dir
                if options and options.recursive:
                    new_dir = _fresh_dir()
                    current.children[seg] = new_dir
                    current = new_dir
                else:
                    raise create_enotdir(join_path(*segs[: i + 1]))

        return current

    def _insert_content(
        self,
        path: str,
        content: FileContent,
        mode: int | None = None,
        mtime: datetime | None = None,
    ) -> None:
        """Insert or overwrite a file with content."""
        norm = validate_path(path, "writeFile")
        parent_path = parent_dir(norm)

        # Ensure parent directory exists
        parent_dir_node = self._ensure_dir(parent_path, MkdirOptions(recursive=True))

        file_name = norm[norm.rfind("/") + 1 :] if "/" in norm[1:] else norm[1:]
        if not file_name:
            raise create_eisdir(path, "writeFile")

        parent_dir_node.children[file_name] = _VFileNode(
            content=_to_bytes(content),
            mode=mode or DEFAULT_FILE_MODE,
            mtime=mtime or datetime.now(timezone.utc),
        )

    def _insert_lazy(
        self,
        path: str,
        provider: LazyFileProvider,
        mode: int | None = None,
        mtime: datetime | None = None,
    ) -> None:
        """Insert a lazy file that loads content on demand."""
        norm = validate_path(path, "writeFile")
        parent_path = parent_dir(norm)

        parent_dir_node = self._ensure_dir(parent_path, MkdirOptions(recursive=True))
        file_name = norm[norm.rfind("/") + 1 :] if "/" in norm[1:] else norm[1:]

        parent_dir_node.children[file_name] = _VLazyNode(
            provider=provider,
            mode=mode or DEFAULT_FILE_MODE,
            mtime=mtime or datetime.now(timezone.utc),
        )

    def _insert_symlink(
        self,
        path: str,
        target: str,
        mode: int | None = None,
        mtime: datetime | None = None,
    ) -> None:
        """Insert or overwrite a symlink."""
        norm = validate_path(path, "symlink")
        parent_path = parent_dir(norm)
        parent_dir_node = self._ensure_dir(parent_path, MkdirOptions(recursive=True))
        file_name = norm[norm.rfind("/") + 1 :] if "/" in norm[1:] else norm[1:]
        parent_dir_node.children[file_name] = _VSymlinkNode(
            target=target,
            mode=mode or SYMLINK_MODE,
            mtime=mtime or datetime.now(timezone.utc),
        )

    async def _resolve_lazy(self, node: _VNode, path: str) -> _VNode:
        if node.kind != "lazy":
            return node

        result = node.provider()
        if hasattr(result, "__await__"):
            result = await result

        file_node = _VFileNode(
            content=_to_bytes(result),
            mode=node.mode,
            mtime=node.mtime,
        )

        located = self._locate(path, follow_symlinks=False)
        if located.node is node:
            located.parent.children[located.key] = file_node

        return file_node

    def _deep_copy(self, node: _VNode) -> _VNode:
        """Create a deep copy of a node."""
        if node.kind == "dir":
            return _VDirNode(
                children={k: self._deep_copy(v) for k, v in node.children.items()},
                mode=node.mode,
                mtime=node.mtime,
            )
        if node.kind == "file":
            return _VFileNode(
                content=node.content[:],
                mode=node.mode,
                mtime=node.mtime,
            )
        if node.kind == "lazy":
            return _VLazyNode(
                provider=node.provider,
                mode=node.mode,
                mtime=node.mtime,
            )
        return _VSymlinkNode(
            target=node.target,
            mode=node.mode,
            mtime=node.mtime,
        )

    def _collect_paths(self, node: _VNode, prefix: str) -> list[str]:
        """Collect all paths under a node."""
        paths = []
        if node.kind != "dir":
            paths.append(prefix)
            return paths

        for name, child in node.children.items():
            child_path = join_path(prefix, name)
            if child.kind == "dir":
                paths.append(child_path)
                paths.extend(self._collect_paths(child, child_path))
            else:
                paths.append(child_path)
        return paths

    # ── Sync helpers (used by consumers and constructor) ─────────────

    def write_file_sync(
        self,
        path: str,
        content: FileContent,
        mode: int | None = None,
        mtime: datetime | None = None,
    ) -> None:
        """Synchronous write_file."""
        self._insert_content(path, content, mode, mtime)

    def write_file_lazy(
        self,
        path: str,
        provider: LazyFileProvider,
        mode: int | None = None,
        mtime: datetime | None = None,
    ) -> None:
        """Synchronous lazy file insertion."""
        self._insert_lazy(path, provider, mode, mtime)

    def mkdir_sync(self, path: str, options: MkdirOptions | None = None) -> None:
        """Synchronous mkdir."""
        norm = normalize_path(path)
        if norm == "/":
            return

        opts = options or MkdirOptions()
        segs = split_path(norm)
        current = self._tree

        for i, seg in enumerate(segs):
            last = i == len(segs) - 1
            child = current.children.get(seg)

            if child:
                if child.kind == "dir":
                    if last and not opts.recursive:
                        raise create_eexist(path)
                    current = child
                elif last:
                    raise create_eexist(path)
                elif opts.recursive:
                    new_dir = _fresh_dir()
                    current.children[seg] = new_dir
                    current = new_dir
                else:
                    raise create_enotdir(path)
            elif last:
                current.children[seg] = _fresh_dir()
            elif opts.recursive:
                new_dir = _fresh_dir()
                current.children[seg] = new_dir
                current = new_dir
            else:
                raise create_enoent(path, "mkdir")

    # ── FileSystem implementation (async) ────────────────────────────

    async def read_file(self, path: str) -> str:
        node = self._resolve_normal(path)
        node = await self._resolve_lazy(node, path)

        if node.kind != "file":
            if node.kind == "dir":
                raise create_eisdir(path, "readFile")
            raise create_enoent(path, "readFile")

        return _to_str(node.content)

    async def read_file_bytes(self, path: str) -> bytes:
        node = self._resolve_normal(path)
        node = await self._resolve_lazy(node, path)

        if node.kind != "file":
            if node.kind == "dir":
                raise create_eisdir(path, "readFile")
            raise create_enoent(path, "readFile")

        return node.content[:]

    async def write_file(self, path: str, content: str) -> None:
        self._insert_content(path, content)

    async def write_file_bytes(self, path: str, content: bytes) -> None:
        self._insert_content(path, content)

    async def append_file(self, path: str, content: FileContent) -> None:
        norm = normalize_path(path)
        try:
            located = self._locate(norm, follow_symlinks=False)
        except FileNotFoundError:
            self._insert_content(norm, content)
            return

        if located.node.kind == "symlink":
            # Follow the symlink to the real file
            real_path = await self.realpath(norm)
            return await self.append_file(real_path, content)

        if located.node.kind == "dir":
            raise create_eisdir(path, "appendFile")

        if located.node.kind == "lazy":
            located.node = await self._resolve_lazy(located.node, path)

        new_content = located.node.content + _to_bytes(content)
        located.parent.children[located.key] = _VFileNode(
            content=new_content,
            mode=located.node.mode,
            mtime=datetime.now(timezone.utc),
        )

    async def exists(self, path: str) -> bool:
        try:
            self._resolve_normal(path)
            return True
        except (FileNotFoundError, OSError):
            return False

    async def stat(self, path: str) -> FsStat:
        node = self._resolve_normal(path)
        node = await self._resolve_lazy(node, path)
        return _make_stat(node)

    async def lstat(self, path: str) -> FsStat:
        located = self._locate(path, follow_symlinks=False)
        return _make_stat(located.node)

    async def mkdir(self, path: str, options: MkdirOptions | None = None) -> None:
        self.mkdir_sync(path, options)

    async def readdir(self, path: str) -> list[str]:
        node = self._resolve_normal(path)
        if node.kind != "dir":
            raise create_enotdir(path, "readdir")
        return sorted(node.children.keys())

    async def readdir_with_file_types(self, path: str) -> list[FileSystemDirent]:
        node = self._resolve_normal(path)
        if node.kind != "dir":
            raise create_enotdir(path, "readdir")
        return [
            FileSystemDirent(name=name, type=_kind_to_type(child))
            for name, child in sorted(node.children.items())
        ]

    async def rm(self, path: str, options: RmOptions | None = None) -> None:
        opts = options or RmOptions()
        norm = normalize_path(path)

        if norm == "/":
            raise PermissionError("rm: cannot remove root directory '/'")

        try:
            located = self._locate(norm, follow_symlinks=False)
        except FileNotFoundError:
            if opts.force:
                return
            raise

        if located.node.kind == "dir" and not opts.recursive:
            raise IsADirectoryError(f"EISDIR: is a directory, rm '{path}'")

        del located.parent.children[located.key]

    async def cp(self, src: str, dest: str, options: CpOptions | None = None) -> None:
        src_node = self._resolve_normal(src)
        dest_norm = normalize_path(dest)

        if src_node.kind == "dir":
            opts = options or CpOptions()
            if not opts.recursive:
                raise IsADirectoryError(
                    f"EISDIR: is a directory, cp '{src}'"
                )
            # Recursive copy
            await self._cp_recursive(src_node, src, dest_norm)
            return

        # Single file copy
        src_node = await self._resolve_lazy(src_node, src)
        parent = parent_dir(dest_norm)
        self._ensure_dir(parent, MkdirOptions(recursive=True))

        file_name = dest_norm[dest_norm.rfind("/") + 1 :] if "/" in dest_norm[1:] else dest_norm[1:]
        dest_parent = self._locate(parent, follow_symlinks=False)
        if dest_parent.node.kind != "dir":
            raise create_enotdir(dest)

        dest_parent.node.children[file_name] = self._deep_copy(src_node)

    async def _cp_recursive(self, src_node: _VDirNode, src_path: str, dest_path: str) -> None:
        """Recursively copy a directory tree."""
        self._ensure_dir(dest_path, MkdirOptions(recursive=True))

        for name, child in src_node.children.items():
            child_src = join_path(src_path, name)
            child_dest = join_path(dest_path, name)

            if child.kind == "dir":
                await self._cp_recursive(child, child_src, child_dest)
            else:
                resolved = await self._resolve_lazy(child, child_src)
                dest_parent = self._locate(parent_dir(child_dest), follow_symlinks=False)
                dest_parent.node.children[name] = self._deep_copy(resolved)

    async def mv(self, src: str, dest: str) -> None:
        src_norm = normalize_path(src)
        dest_norm = normalize_path(dest)

        if src_norm == dest_norm:
            return

        src_located = self._locate(src_norm, follow_symlinks=False)

        # Ensure dest parent exists
        dest_parent_path = parent_dir(dest_norm)
        dest_parent = self._ensure_dir(dest_parent_path, MkdirOptions(recursive=True))

        dest_name = dest_norm[dest_norm.rfind("/") + 1 :] if "/" in dest_norm[1:] else dest_norm[1:]

        # Move the node
        dest_parent.children[dest_name] = src_located.node
        del src_located.parent.children[src_located.key]

        # Update mtime
        src_located.node.mtime = datetime.now(timezone.utc)

    async def symlink(self, target: str, link_path: str) -> None:
        norm = normalize_path(link_path)
        parent = parent_dir(norm)
        parent_node = self._ensure_dir(parent, MkdirOptions(recursive=True))

        name = norm[norm.rfind("/") + 1 :] if "/" in norm[1:] else norm[1:]
        parent_node.children[name] = _VSymlinkNode(
            target=target,
        )

    async def readlink(self, path: str) -> str:
        located = self._locate(path, follow_symlinks=False)
        if located.node.kind != "symlink":
            raise OSError(f"EINVAL: not a symlink, readlink '{path}'")
        return located.node.target

    async def realpath(self, path: str) -> str:
        norm = normalize_path(path)
        if norm == "/":
            return "/"

        resolved = "/"
        for seg in split_path(norm):
            candidate = join_path(resolved, seg)
            located = self._locate(candidate, follow_symlinks=False)
            if located.node.kind == "symlink":
                target = located.node.target
                resolved = normalize_path(target) if target.startswith("/") else join_path(resolved, target)
            else:
                resolved = candidate
        self._locate(resolved, follow_symlinks=True)
        return resolved

    def resolve_path(self, base: str, path: str) -> str:
        if path.startswith("/"):
            return normalize_path(path)
        return normalize_path(join_path(base, path))

    async def glob(self, pattern: str) -> list[str]:
        """Simple glob implementation. Supports * and ** wildcards."""
        all_paths = self._collect_paths(self._tree, "/")

        # Convert pattern to simple matcher
        # For now, use fnmatch for basic patterns
        # A more sophisticated implementation would handle ** properly
        matched = []
        for p in all_paths:
            # Strip leading /
            relative = p[1:] if p.startswith("/") else p
            pattern_relative = pattern[1:] if pattern.startswith("/") else pattern
            if fnmatch.fnmatch(relative, pattern_relative) or fnmatch.fnmatch(p, pattern):
                matched.append(p)

        return sorted(set(matched))

    # ── Extra utilities ─────────────────────────────────────────────

    def get_data(self) -> dict[str, Any]:
        """Get a flat dictionary representation of all files.

        Useful for testing and serialization.
        """
        result = {}
        paths = self._collect_paths(self._tree, "/")
        for path in paths:
            try:
                node = self._resolve_normal(path)
                if node.kind == "file":
                    result[path] = _to_str(node.content)
                elif node.kind == "dir":
                    pass  # Skip directories
                elif node.kind == "symlink":
                    result[path] = f"SYMLINK->{node.target}"
            except FileNotFoundError:
                pass
        return result

    def __repr__(self) -> str:
        return f"InMemoryFs(files={len(self.get_data())})"
