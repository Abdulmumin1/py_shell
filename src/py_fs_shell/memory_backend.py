"""FileSystemStateBackend - wraps any FileSystem into a StateBackend.

This is the core "state" object that agent workflows use. It takes
the low-level FileSystem interface and layers on JSON helpers,
search/replace, diff, structured editing, file detection, etc.

Inspired by @cloudflare/shell memory.ts / backend.ts.
"""

from __future__ import annotations

import difflib
import gzip
import hashlib
import io
import json
import mimetypes
import re
import tarfile
import traceback as tb_mod
from collections.abc import Awaitable
from contextlib import suppress
from datetime import datetime
from typing import Any

from py_fs_shell.backend import (
    StateAppliedEditResult,
    StateApplyEditsOptions,
    StateApplyEditsResult,
    StateArchiveCreateResult,
    StateArchiveEntry,
    StateArchiveExtractResult,
    StateBackend,
    StateBatchOperationError,
    StateCapabilities,
    StateCompressionResult,
    StateCopyOptions,
    StateDirent,
    StateEdit,
    StateEditInstruction,
    StateEditPlan,
    StateFileDetection,
    StateFileReplaceResult,
    StateFileSearchResult,
    StateFindEntry,
    StateFindOptions,
    StateHashOptions,
    StateJsonUpdateOperation,
    StateJsonUpdateResult,
    StateJsonWriteOptions,
    StateMkdirOptions,
    StateMoveOptions,
    StatePlannedEdit,
    StateReplaceEditInstruction,
    StateReplaceInFilesOptions,
    StateReplaceInFilesResult,
    StateReplaceResult,
    StateRmOptions,
    StateSearchOptions,
    StateStat,
    StateTextMatch,
    StateTreeNode,
    StateTreeOptions,
    StateTreeSummary,
    StateWriteEditInstruction,
    StateWriteJsonEditInstruction,
)
from py_fs_shell.fs.interface import (
    FileContent,
    FileSystem,
    MkdirOptions,
    RmOptions,
)
from py_fs_shell.fs.path_utils import (
    join_path,
    normalize_path,
    parent_dir,
)

# ── JSON Pointer helpers ─────────────────────────────────────────────

def _json_pointer_get(obj: Any, pointer: str) -> Any:
    """Get value via JSON Pointer (RFC 6901). E.g. '/foo/bar'."""
    if pointer == "":
        return obj
    parts = pointer.lstrip("/").split("/")
    current = obj
    for part in parts:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                idx = int(part)
                current = current[idx] if 0 <= idx < len(current) else None
            except ValueError:
                return None
        else:
            return None
    return current


def _json_pointer_set(obj: Any, pointer: str, value: Any) -> Any:
    """Set value via JSON Pointer. Returns modified object."""
    if pointer == "":
        return value
    parts = pointer.lstrip("/").split("/")
    current = obj
    stack: list[tuple[Any, str | int, Any]] = []

    for i, part in enumerate(parts):
        part = part.replace("~1", "/").replace("~0", "~")
        is_last = i == len(parts) - 1

        if isinstance(current, dict):
            if is_last:
                current[part] = value
                break
            nxt = current.get(part)
            if nxt is None:
                nxt = {} if i + 1 < len(parts) and not parts[i + 1].isdigit() else []
                current[part] = nxt
            stack.append((current, part, nxt))
            current = nxt
        elif isinstance(current, list):
            try:
                idx = int(part)
            except ValueError as exc:
                raise ValueError(f"Cannot use key '{part}' on list") from exc
            if is_last:
                if 0 <= idx <= len(current):
                    if idx == len(current):
                        current.append(value)
                    else:
                        current[idx] = value
                break
            nxt = current[idx] if 0 <= idx < len(current) else None
            if nxt is None:
                nxt = {} if i + 1 < len(parts) and not parts[i + 1].isdigit() else []
                if idx == len(current):
                    current.append(nxt)
                else:
                    current[idx] = nxt
            stack.append((current, idx, nxt))
            current = nxt
        else:
            raise ValueError(f"Cannot traverse into {type(current).__name__}")

    return obj


def _json_pointer_delete(obj: Any, pointer: str) -> Any:
    """Delete value via JSON Pointer. Returns modified object."""
    if pointer == "":
        raise ValueError("Cannot delete root")
    parts = pointer.lstrip("/").split("/")
    current = obj

    for i, part in enumerate(parts):
        part = part.replace("~1", "/").replace("~0", "~")
        is_last = i == len(parts) - 1

        if isinstance(current, dict):
            if is_last:
                if part not in current:
                    raise KeyError(f"Key '{part}' not found")
                del current[part]
                return obj
            current = current.get(part)
        elif isinstance(current, list):
            try:
                idx = int(part)
            except ValueError as exc:
                raise ValueError(f"Cannot use key '{part}' on list") from exc
            if is_last:
                if not (0 <= idx < len(current)):
                    raise IndexError(f"Index {idx} out of range")
                del current[idx]
                return obj
            current = current[idx] if 0 <= idx < len(current) else None
        else:
            raise ValueError(f"Cannot traverse into {type(current).__name__}")

    return obj


# ── Unified diff helper ──────────────────────────────────────────────

def _unified_diff(a: str, b: str, a_path: str = "a/file", b_path: str = "b/file") -> str:
    """Generate unified diff between two strings."""
    a_lines = a.splitlines(keepends=True)
    b_lines = b.splitlines(keepends=True)
    # Ensure lines end with newline for clean diff
    if a_lines and not a_lines[-1].endswith("\n"):
        a_lines[-1] += "\n"
    if b_lines and not b_lines[-1].endswith("\n"):
        b_lines[-1] += "\n"
    diff = list(difflib.unified_diff(
        a_lines, b_lines,
        fromfile=a_path, tofile=b_path,
        lineterm="",
    ))
    return "".join(diff)


def _archive_destination_path(destination: str, member_name: str) -> str:
    base = normalize_path(destination)
    dest = join_path(base, member_name.lstrip("/"))
    if dest != base and not dest.startswith(base.rstrip("/") + "/"):
        raise ValueError(f"archive entry escapes destination: {member_name!r}")
    return dest


# ── Search helpers ───────────────────────────────────────────────────

def _search_text(
    content: str,
    query: str,
    options: StateSearchOptions | None = None,
) -> list[StateTextMatch]:
    """Search for query string in content, return matches."""
    opts = options or StateSearchOptions()
    matches: list[StateTextMatch] = []
    lines = content.splitlines()

    if opts.regex:
        flags = 0 if opts.case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(query, flags)
        except re.error as e:
            raise ValueError(f"Invalid regex: {e}") from e
    else:
        search_text = query if opts.case_sensitive else query.lower()

    for line_idx, line in enumerate(lines, start=1):
        search_line = line if opts.case_sensitive else line.lower()

        if opts.regex:
            for m in pattern.finditer(line):
                match_str = m.group(0)
                if opts.whole_word and not (
                    (m.start() == 0 or not search_line[m.start() - 1].isalnum())
                    and (m.end() == len(line) or not search_line[m.end()].isalnum())
                ):
                    continue
                before = lines[max(0, line_idx - 1 - opts.context_before):line_idx - 1]
                after = lines[line_idx:min(len(lines), line_idx + opts.context_after)]
                matches.append(StateTextMatch(
                    line=line_idx,
                    column=m.start() + 1,
                    match=match_str,
                    line_text=line,
                    before_lines=before,
                    after_lines=after,
                ))
        else:
            start = 0
            while True:
                pos = search_line.find(search_text, start)
                if pos == -1:
                    break
                if opts.whole_word and not (
                    (pos == 0 or not search_line[pos - 1].isalnum())
                    and (pos + len(search_text) == len(line) or not search_line[pos + len(search_text)].isalnum())
                ):
                    start = pos + 1
                    continue
                before = lines[max(0, line_idx - 1 - opts.context_before):line_idx - 1]
                after = lines[line_idx:min(len(lines), line_idx + opts.context_after)]
                matches.append(StateTextMatch(
                    line=line_idx,
                    column=pos + 1,
                    match=query,
                    line_text=line,
                    before_lines=before,
                    after_lines=after,
                ))
                start = pos + 1

        if opts.max_matches and len(matches) >= opts.max_matches:
            break

    if opts.max_matches:
        return matches[:opts.max_matches]
    return matches


def _is_whole_word_match(content: str, start: int, end: int) -> bool:
    return (
        (start == 0 or not content[start - 1].isalnum())
        and (end == len(content) or not content[end].isalnum())
    )


def _replace_text(
    content: str,
    search: str,
    replacement: str,
    options: StateSearchOptions | None = None,
) -> tuple[int, str]:
    """Replace text in content. Returns (count, new_content)."""
    opts = options or StateSearchOptions()
    if search == "":
        raise ValueError("search string must not be empty")

    flags = 0 if opts.case_sensitive else re.IGNORECASE
    pattern_text = search if opts.regex else re.escape(search)
    try:
        pattern = re.compile(pattern_text, flags)
    except re.error as exc:
        raise ValueError(f"Invalid regex: {exc}") from exc

    count = 0

    def replace_match(match: re.Match[str]) -> str:
        nonlocal count
        if opts.whole_word and not _is_whole_word_match(match.string, match.start(), match.end()):
            return match.group(0)
        count += 1
        if opts.regex:
            return match.expand(replacement)
        return replacement

    new_content = pattern.sub(replace_match, content)

    return count, new_content


# ── FileSystemStateBackend ───────────────────────────────────────────

class FileSystemStateBackend(StateBackend):
    """Wraps any FileSystem into a StateBackend.

    Provides all high-level operations (JSON, search/replace, diff,
    compression, structured editing) while delegating the basic
    filesystem operations to an underlying FileSystem.
    """

    def __init__(self, fs: FileSystem) -> None:
        self._fs = fs

    @property
    def fs(self) -> FileSystem:
        """Access the underlying FileSystem directly."""
        return self._fs

    # ── Capabilities ───────────────────────────────────────────────

    async def get_capabilities(self) -> StateCapabilities:
        return StateCapabilities(
            chmod=False,  # Basic FileSystem doesn't expose chmod
            utimes=False,
            hard_links=False,
        )

    # ── Basic file ops ─────────────────────────────────────────────

    async def read_file(self, path: str) -> str:
        return await self._fs.read_file(path)

    async def read_file_bytes(self, path: str) -> bytes:
        return await self._fs.read_file_bytes(path)

    async def write_file(self, path: str, content: str) -> None:
        await self._fs.write_file(path, content)

    async def write_file_bytes(self, path: str, content: bytes) -> None:
        await self._fs.write_file_bytes(path, content)

    async def append_file(self, path: str, content: str | bytes) -> None:
        await self._fs.append_file(path, content)

    async def exists(self, path: str) -> bool:
        return await self._fs.exists(path)

    async def stat(self, path: str) -> StateStat | None:
        try:
            st = await self._fs.stat(path)
        except FileNotFoundError:
            return None
        return StateStat(
            type=st.type,
            size=st.size,
            mtime=st.mtime,
            mode=st.mode,
        )

    async def lstat(self, path: str) -> StateStat | None:
        try:
            st = await self._fs.lstat(path)
        except FileNotFoundError:
            return None
        return StateStat(
            type=st.type,
            size=st.size,
            mtime=st.mtime,
            mode=st.mode,
        )

    async def mkdir(self, path: str, options: StateMkdirOptions | None = None) -> None:
        opts = MkdirOptions(recursive=(options.recursive if options else False))
        await self._fs.mkdir(path, opts)

    async def readdir(self, path: str) -> list[str]:
        return await self._fs.readdir(path)

    async def readdir_with_file_types(self, path: str) -> list[StateDirent]:
        entries = await self._fs.readdir_with_file_types(path)
        return [StateDirent(name=e.name, type=e.type) for e in entries]

    # ── JSON helpers ───────────────────────────────────────────────

    async def read_json(self, path: str) -> Any:
        content = await self._fs.read_file(path)
        return json.loads(content)

    async def write_json(
        self,
        path: str,
        value: Any,
        options: StateJsonWriteOptions | None = None,
    ) -> None:
        opts = options or StateJsonWriteOptions()
        indent = opts.spaces if opts.spaces is not None else 2
        content = json.dumps(value, indent=indent, ensure_ascii=False)
        # Add trailing newline
        if not content.endswith("\n"):
            content += "\n"
        await self._fs.write_file(path, content)

    async def query_json(self, path: str, query: str) -> Any:
        data = await self.read_json(path)
        return _json_pointer_get(data, query)

    async def update_json(
        self,
        path: str,
        operations: list[StateJsonUpdateOperation],
    ) -> StateJsonUpdateResult:
        content = await self._fs.read_file(path)
        data = json.loads(content)
        applied = 0

        for op in operations:
            if op.op == "set":
                data = _json_pointer_set(data, op.path, op.value)
                applied += 1
            elif op.op == "delete":
                data = _json_pointer_delete(data, op.path)
                applied += 1

        new_content_obj = json.dumps(data, indent=2, ensure_ascii=False)
        if not new_content_obj.endswith("\n"):
            new_content_obj += "\n"
        diff = _unified_diff(content, new_content_obj)

        await self._fs.write_file(path, new_content_obj)
        return StateJsonUpdateResult(
            value=data,
            content=new_content_obj,
            diff=diff,
            operations_applied=applied,
        )

    # ── Directory traversal ────────────────────────────────────────

    async def find(self, path: str, options: StateFindOptions | None = None) -> list[StateFindEntry]:
        opts = options or StateFindOptions()
        results: list[StateFindEntry] = []
        norm = normalize_path(path)

        def _to_datetime(value: str | datetime | None) -> datetime | None:
            if value is None:
                return None
            if isinstance(value, datetime):
                return value
            return datetime.fromisoformat(value)

        mtime_after = _to_datetime(opts.mtime_after)
        mtime_before = _to_datetime(opts.mtime_before)

        async def _is_empty_entry(current_path: str, st: StateStat) -> bool:
            if st.type == "directory":
                return len(await self.readdir(current_path)) == 0
            return st.size == 0

        async def _walk(current_path: str, depth: int) -> None:
            st = await self.stat(current_path)
            if st is None:
                return

            name = current_path[current_path.rfind("/") + 1:]
            if current_path == "/":
                name = "/"

            # Check filters
            if opts.min_depth is not None and depth < opts.min_depth:
                pass
            elif opts.max_depth is not None and depth > opts.max_depth:
                return
            else:
                match_type = True
                if opts.type:
                    types = opts.type if isinstance(opts.type, list) else [opts.type]
                    match_type = st.type in types

                match_name = True
                if opts.name:
                    import fnmatch as _fnmatch
                    match_name = _fnmatch.fnmatch(name, opts.name)

                match_path = True
                if opts.path_pattern:
                    import fnmatch as _fnmatch
                    match_path = _fnmatch.fnmatch(current_path, opts.path_pattern)

                match_empty = True
                if opts.empty is not None:
                    match_empty = (await _is_empty_entry(current_path, st)) is opts.empty

                match_size = True
                if opts.size_min is not None and st.size < opts.size_min:
                    match_size = False
                if opts.size_max is not None and st.size > opts.size_max:
                    match_size = False

                match_mtime = True
                if mtime_after is not None and st.mtime < mtime_after:
                    match_mtime = False
                if mtime_before is not None and st.mtime > mtime_before:
                    match_mtime = False

                if match_type and match_name and match_path and match_empty and match_size and match_mtime:
                    results.append(StateFindEntry(
                        path=current_path,
                        name=name,
                        type=st.type,
                        depth=depth,
                        size=st.size,
                        mtime=st.mtime,
                    ))

            if st.type == "directory" and (opts.max_depth is None or depth < opts.max_depth):
                try:
                    entries = await self.readdir_with_file_types(current_path)
                    for entry in entries:
                        child = join_path(current_path, entry.name)
                        await _walk(child, depth + 1)
                except (FileNotFoundError, NotADirectoryError):
                    pass

        await _walk(norm, 0)
        return results

    async def walk_tree(self, path: str, options: StateTreeOptions | None = None) -> StateTreeNode:
        opts = options or StateTreeOptions()
        st = await self.stat(path)
        if st is None:
            raise FileNotFoundError(path)

        name = path[path.rfind("/") + 1:] if "/" in path[1:] else path[1:]
        if path == "/":
            name = "/"

        children = None
        if st.type == "directory":
            if opts.max_depth is None or opts.max_depth > 0:
                children = []
                try:
                    entries = await self.readdir_with_file_types(path)
                    for entry in entries:
                        child_path = join_path(path, entry.name)
                        child_opts = StateTreeOptions(
                            max_depth=opts.max_depth - 1 if opts.max_depth is not None else None,
                        )
                        child_node = await self.walk_tree(child_path, child_opts)
                        children.append(child_node)
                except (FileNotFoundError, NotADirectoryError):
                    pass
            else:
                children = None

        return StateTreeNode(
            path=path,
            name=name,
            type=st.type,
            size=st.size,
            children=children,
        )

    async def summarize_tree(self, path: str, options: StateTreeOptions | None = None) -> StateTreeSummary:
        opts = options or StateTreeOptions()
        files = 0
        directories = 0
        symlinks = 0
        total_bytes = 0
        max_depth = 0

        async def _summarize(current_path: str, depth: int) -> int:
            nonlocal files, directories, symlinks, total_bytes, max_depth
            st = await self.stat(current_path)
            if st is None:
                return 0

            if depth > max_depth:
                max_depth = depth

            if st.type == "file":
                files += 1
                total_bytes += st.size
            elif st.type == "directory":
                directories += 1
            elif st.type == "symlink":
                symlinks += 1

            if st.type == "directory" and (opts.max_depth is None or depth < opts.max_depth):
                try:
                    entries = await self.readdir_with_file_types(current_path)
                    for entry in entries:
                        child_path = join_path(current_path, entry.name)
                        await _summarize(child_path, depth + 1)
                except (FileNotFoundError, NotADirectoryError):
                    pass

            return max_depth

        max_depth = await _summarize(path, 0)
        return StateTreeSummary(
            files=files,
            directories=directories,
            symlinks=symlinks,
            total_bytes=total_bytes,
            max_depth=max_depth,
        )

    # ── Search & replace ───────────────────────────────────────────

    async def search_text(self, path: str, query: str, options: StateSearchOptions | None = None) -> list[StateTextMatch]:
        content = await self._fs.read_file(path)
        return _search_text(content, query, options)

    async def search_files(self, pattern: str, query: str, options: StateSearchOptions | None = None) -> list[StateFileSearchResult]:
        paths = await self.glob(pattern)
        results: list[StateFileSearchResult] = []
        for path in paths:
            try:
                st = await self.stat(path)
                if st is None or st.type != "file":
                    continue
                content = await self._fs.read_file(path)
                matches = _search_text(content, query, options)
                if matches:
                    results.append(StateFileSearchResult(path=path, matches=matches))
            except (FileNotFoundError, UnicodeDecodeError, IsADirectoryError):
                continue
        return results

    async def replace_in_file(
        self,
        path: str,
        search: str,
        replacement: str,
        options: StateSearchOptions | None = None,
    ) -> StateReplaceResult:
        content = await self._fs.read_file(path)
        count, new_content = _replace_text(content, search, replacement, options)
        await self._fs.write_file(path, new_content)
        return StateReplaceResult(replaced=count, content=new_content)

    async def replace_in_files(
        self,
        pattern: str,
        search: str,
        replacement: str,
        options: StateReplaceInFilesOptions | None = None,
    ) -> StateReplaceInFilesResult:
        opts = options or StateReplaceInFilesOptions()
        paths = await self.glob(pattern)
        files: list[StateFileReplaceResult] = []
        total_replacements = 0

        # Collect original content for rollback
        originals: dict[str, str] = {}
        modified: list[str] = []

        try:
            for path in sorted(paths):
                try:
                    st = await self.stat(path)
                    if st is None or st.type != "file":
                        continue
                    content = await self._fs.read_file(path)
                    originals[path] = content
                    count, new_content = _replace_text(content, search, replacement, opts)
                    if count == 0:
                        continue
                    diff = _unified_diff(content, new_content)
                    if not opts.dry_run:
                        await self._fs.write_file(path, new_content)
                        modified.append(path)
                    files.append(StateFileReplaceResult(
                        path=path,
                        replaced=count,
                        content=new_content,
                        diff=diff,
                    ))
                    total_replacements += count
                except (FileNotFoundError, UnicodeDecodeError, IsADirectoryError):
                    continue

        except Exception:
            if opts.rollback_on_error and not opts.dry_run and modified:
                for p in modified:
                    with suppress(FileNotFoundError):
                        await self._fs.write_file(p, originals[p])
            raise

        return StateReplaceInFilesResult(
            dry_run=opts.dry_run,
            files=files,
            total_files=len(files),
            total_replacements=total_replacements,
        )

    # ── Remove/copy/move ───────────────────────────────────────────

    async def rm(self, path: str, options: StateRmOptions | None = None) -> None:
        opts = RmOptions(
            recursive=(options.recursive if options else False),
            force=(options.force if options else False),
        )
        await self._fs.rm(path, opts)

    async def cp(self, src: str, dest: str, options: StateCopyOptions | None = None) -> None:
        from py_fs_shell.fs.interface import CpOptions as _CPOpts

        cp_opts = _CPOpts(recursive=(options.recursive if options else False))
        await self._fs.cp(src, dest, cp_opts)

    async def mv(self, src: str, dest: str, options: StateMoveOptions | None = None) -> None:
        await self._fs.mv(src, dest)

    async def symlink(self, target: str, link_path: str) -> None:
        await self._fs.symlink(target, link_path)

    async def readlink(self, path: str) -> str:
        return await self._fs.readlink(path)

    async def realpath(self, path: str) -> str:
        return await self._fs.realpath(path)

    async def resolve_path(self, base: str, path: str) -> str:
        # Try the FileSystem method first
        if hasattr(self._fs, "resolve_path"):
            method = self._fs.resolve_path
            if callable(method):
                result = method(base, path)
                if isinstance(result, Awaitable):
                    return await result
                return result
        # Fallback
        if path.startswith("/"):
            return normalize_path(path)
        return normalize_path(join_path(base, path))

    # ── Glob ───────────────────────────────────────────────────────

    async def glob(self, pattern: str) -> list[str]:
        return await self._fs.glob(pattern)

    # ── Diff ───────────────────────────────────────────────────────

    async def diff(self, path_a: str, path_b: str) -> str:
        content_a = await self._fs.read_file(path_a)
        content_b = await self._fs.read_file(path_b)
        return _unified_diff(content_a, content_b, f"a{path_a}", f"b{path_b}")

    async def diff_content(self, path: str, new_content: str) -> str:
        old_content = await self._fs.read_file(path)
        return _unified_diff(old_content, new_content, f"a{path}", f"b{path}")

    # ── Archive operations ─────────────────────────────────────────

    async def create_archive(self, path: str, sources: list[str]) -> StateArchiveCreateResult:
        """Create a tar.gz archive at `path` containing `sources`."""
        buf = io.BytesIO()
        entries: list[StateArchiveEntry] = []

        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for src in sources:
                norm = normalize_path(src)
                # Walk and add files
                async def _add(p: str) -> None:
                    st = await self.stat(p)
                    if st is None:
                        return
                    arcname = p.lstrip("/")
                    if st.type == "file":
                        content = await self._fs.read_file_bytes(p)
                        info = tarfile.TarInfo(name=arcname)
                        info.size = len(content)
                        tar.addfile(info, io.BytesIO(content))
                        entries.append(StateArchiveEntry(
                            path=p, type="file", size=len(content),
                        ))
                    elif st.type == "directory":
                        info = tarfile.TarInfo(name=arcname)
                        info.type = tarfile.DIRTYPE
                        tar.addfile(info)
                        entries.append(StateArchiveEntry(
                            path=p, type="directory", size=0,
                        ))
                        # Recurse
                        try:
                            dirents = await self.readdir_with_file_types(p)
                            for de in dirents:
                                await _add(join_path(p, de.name))
                        except (FileNotFoundError, NotADirectoryError):
                            pass

                await _add(norm)

        data = buf.getvalue()
        await self._fs.write_file_bytes(path, data)
        return StateArchiveCreateResult(
            path=path,
            entries=entries,
            bytes_written=len(data),
        )

    async def list_archive(self, path: str) -> list[StateArchiveEntry]:
        """List entries in a tar.gz archive."""
        content = await self._fs.read_file_bytes(path)
        entries: list[StateArchiveEntry] = []
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tar:
            for member in tar.getmembers():
                entry_type = "directory" if member.isdir() else "file"
                entries.append(StateArchiveEntry(
                    path=member.name,
                    type=entry_type,
                    size=member.size,
                ))
        return entries

    async def extract_archive(self, path: str, destination: str) -> StateArchiveExtractResult:
        """Extract a tar.gz archive."""
        content = await self._fs.read_file_bytes(path)
        entries: list[StateArchiveEntry] = []

        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tar:
            for member in tar.getmembers():
                dest = _archive_destination_path(destination, member.name)
                if member.isdir():
                    await self.mkdir(dest, StateMkdirOptions(recursive=True))
                    entries.append(StateArchiveEntry(
                        path=dest, type="directory", size=0,
                    ))
                else:
                    data = tar.extractfile(member)
                    file_content = data.read() if data else b""
                    parent = parent_dir(dest)
                    await self.mkdir(parent, StateMkdirOptions(recursive=True))
                    await self._fs.write_file_bytes(dest, file_content)
                    entries.append(StateArchiveEntry(
                        path=dest, type="file", size=len(file_content),
                    ))

        return StateArchiveExtractResult(
            destination=destination,
            entries=entries,
        )

    # ── Compression ────────────────────────────────────────────────

    async def compress_file(self, path: str, destination: str | None = None) -> StateCompressionResult:
        dest = destination or path + ".gz"
        content = await self._fs.read_file_bytes(path)
        compressed = gzip.compress(content)
        await self._fs.write_file_bytes(dest, compressed)
        return StateCompressionResult(
            path=path,
            destination=dest,
            bytes_written=len(compressed),
        )

    async def decompress_file(self, path: str, destination: str | None = None) -> StateCompressionResult:
        dest = destination or (
            path[: -3] if path.endswith(".gz") else path + ".decompressed"
        )
        content = await self._fs.read_file_bytes(path)
        decompressed = gzip.decompress(content)
        await self._fs.write_file_bytes(dest, decompressed)
        return StateCompressionResult(
            path=path,
            destination=dest,
            bytes_written=len(decompressed),
        )

    # ── Hash & detection ───────────────────────────────────────────

    async def hash_file(self, path: str, options: StateHashOptions | None = None) -> str:
        opts = options or StateHashOptions()
        content = await self._fs.read_file_bytes(path)
        if opts.algorithm == "md5":
            return hashlib.md5(content).hexdigest()
        if opts.algorithm == "sha1":
            return hashlib.sha1(content).hexdigest()
        return hashlib.sha256(content).hexdigest()

    async def detect_file(self, path: str) -> StateFileDetection:
        st = await self.stat(path)
        if st is None:
            raise FileNotFoundError(path)

        if st.type == "directory":
            return StateFileDetection(
                mime="inode/directory",
                description="Directory",
                binary=False,
            )
        if st.type == "symlink":
            return StateFileDetection(
                mime="inode/symlink",
                description="Symbolic Link",
                binary=False,
            )

        content = await self._fs.read_file_bytes(path)
        # Simple detection: check for null bytes
        is_binary = b"\x00" in content[:8000]

        # Try mimetypes
        mime, _ = mimetypes.guess_type(path)
        if mime is None:
            mime = "application/octet-stream" if is_binary else "text/plain"

        extension = None
        if "." in path:
            extension = path.rsplit(".", 1)[1].lower()

        description = mime.split("/")[0].capitalize()
        if mime.startswith("text/"):
            description = f"Text ({mime.split('/')[1]})"
        elif mime.startswith("image/"):
            description = f"Image ({mime.split('/')[1]})"
        elif mime.startswith("application/"):
            app = mime.split("/")[1]
            description = f"{app.upper()} document" if app != "octet-stream" else "Binary data"

        return StateFileDetection(
            mime=mime,
            description=description,
            extension=extension,
            binary=is_binary,
        )

    # ── Tree helpers ───────────────────────────────────────────────

    async def remove_tree(self, path: str) -> None:
        await self._fs.rm(path, RmOptions(recursive=True, force=True))

    async def copy_tree(self, src: str, dest: str) -> None:
        from py_fs_shell.fs.interface import CpOptions as _CPOpts
        await self._fs.cp(src, dest, _CPOpts(recursive=True))

    async def move_tree(self, src: str, dest: str) -> None:
        await self._fs.mv(src, dest)

    # ── Structured editing ─────────────────────────────────────────

    async def plan_edits(self, instructions: list[StateEditInstruction]) -> StateEditPlan:
        edits: list[StatePlannedEdit] = []
        total_changed = 0

        for inst in instructions:
            if isinstance(inst, StateWriteEditInstruction):
                old_content = ""
                if await self.exists(inst.path):
                    old_content = await self._fs.read_file(inst.path)
                new_content = inst.content
                changed = old_content != new_content
                diff = _unified_diff(old_content, new_content) if changed else ""
                edits.append(StatePlannedEdit(
                    instruction=inst,
                    path=inst.path,
                    changed=changed,
                    content=new_content,
                    diff=diff,
                ))
                if changed:
                    total_changed += 1

            elif isinstance(inst, StateReplaceEditInstruction):
                content = ""
                if await self.exists(inst.path):
                    content = await self._fs.read_file(inst.path)
                count, new_content = _replace_text(content, inst.search, inst.replacement, inst.options)
                changed = count > 0
                diff = _unified_diff(content, new_content) if changed else ""
                edits.append(StatePlannedEdit(
                    instruction=inst,
                    path=inst.path,
                    changed=changed,
                    content=new_content,
                    diff=diff,
                ))
                if changed:
                    total_changed += 1

            elif isinstance(inst, StateWriteJsonEditInstruction):
                old_content = ""
                if await self.exists(inst.path):
                    old_content = await self._fs.read_file(inst.path)
                indent = inst.options.spaces if inst.options and inst.options.spaces is not None else 2
                new_content = json.dumps(inst.value, indent=indent, ensure_ascii=False)
                if not new_content.endswith("\n"):
                    new_content += "\n"
                changed = old_content != new_content
                diff = _unified_diff(old_content, new_content) if changed else ""
                edits.append(StatePlannedEdit(
                    instruction=inst,
                    path=inst.path,
                    changed=changed,
                    content=new_content,
                    diff=diff,
                ))
                if changed:
                    total_changed += 1

        return StateEditPlan(
            edits=edits,
            total_changed=total_changed,
            total_instructions=len(instructions),
        )

    async def apply_edit_plan(
        self,
        plan: StateEditPlan,
        options: StateApplyEditsOptions | None = None,
    ) -> StateApplyEditsResult:
        opts = options or StateApplyEditsOptions()
        results: list[StateAppliedEditResult] = []
        total_changed = 0
        modified: dict[str, tuple[bool, str]] = {}

        try:
            for edit in plan.edits:
                if not edit.changed:
                    results.append(StateAppliedEditResult(
                        path=edit.path,
                        changed=False,
                        content=edit.content,
                        diff="",
                    ))
                    continue

                # Save original for rollback
                if opts.rollback_on_error and edit.path not in modified:
                    try:
                        modified[edit.path] = (True, await self._fs.read_file(edit.path))
                    except FileNotFoundError:
                        modified[edit.path] = (False, "")

                if not opts.dry_run:
                    await self._fs.write_file(edit.path, edit.content)

                results.append(StateAppliedEditResult(
                    path=edit.path,
                    changed=True,
                    content=edit.content,
                    diff=edit.diff,
                ))
                total_changed += 1

        except Exception:
            if opts.rollback_on_error and not opts.dry_run:
                for path, orig in modified.items():
                    existed, content = orig
                    if existed:
                        with suppress(FileNotFoundError):
                            await self._fs.write_file(path, content)
                    else:
                        await self._fs.rm(path, RmOptions(force=True))
            raise StateBatchOperationError(
                operation="apply_edit_plan",
                message=str(tb_mod.format_exc()),
                rolled_back=opts.rollback_on_error,
            ) from None

        return StateApplyEditsResult(
            dry_run=opts.dry_run,
            edits=results,
            total_changed=total_changed,
        )

    async def apply_edits(
        self,
        edits: list[StateEdit],
        options: StateApplyEditsOptions | None = None,
    ) -> StateApplyEditsResult:
        opts = options or StateApplyEditsOptions()
        results: list[StateAppliedEditResult] = []
        total_changed = 0
        modified: dict[str, tuple[bool, str]] = {}

        try:
            for edit in edits:
                # Save original
                if opts.rollback_on_error and edit.path not in modified:
                    try:
                        modified[edit.path] = (True, await self._fs.read_file(edit.path))
                    except FileNotFoundError:
                        modified[edit.path] = (False, "")

                old_content = modified.get(edit.path, (False, ""))[1]
                changed = old_content != edit.content
                diff = _unified_diff(old_content, edit.content) if changed else ""

                if changed and not opts.dry_run:
                    await self._fs.write_file(edit.path, edit.content)

                results.append(StateAppliedEditResult(
                    path=edit.path,
                    changed=changed,
                    content=edit.content,
                    diff=diff,
                ))
                if changed:
                    total_changed += 1

        except Exception:
            if opts.rollback_on_error and not opts.dry_run:
                for path, orig in modified.items():
                    existed, content = orig
                    if existed:
                        with suppress(FileNotFoundError):
                            await self._fs.write_file(path, content)
                    else:
                        await self._fs.rm(path, RmOptions(force=True))
            raise StateBatchOperationError(
                operation="apply_edits",
                message=str(tb_mod.format_exc()),
                rolled_back=opts.rollback_on_error,
            ) from None

        return StateApplyEditsResult(
            dry_run=opts.dry_run,
            edits=results,
            total_changed=total_changed,
        )


# ── Factory ──────────────────────────────────────────────────────────

def create_memory_state_backend(
    initial_files: dict[str, FileContent | Any] | None = None,
) -> FileSystemStateBackend:
    """Create a FileSystemStateBackend backed by an InMemoryFs.

    Convenience factory that auto-creates the InMemoryFs and wraps it.
    Useful for testing and ephemeral agent state.
    """
    from py_fs_shell.fs.in_memory import InMemoryFs

    normalized: dict[str, Any] = {}
    if initial_files:
        for path, value in initial_files.items():
            if isinstance(value, (str, bytes)):
                normalized[path] = value
            else:
                # JSON object → write as JSON
                normalized[path] = json.dumps(value, indent=2) + "\n"

    mem_fs = InMemoryFs(normalized)  # type: ignore[arg-type]
    return FileSystemStateBackend(mem_fs)
