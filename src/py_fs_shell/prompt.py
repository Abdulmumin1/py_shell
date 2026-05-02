"""LLM prompt helpers for the `state` filesystem API.

Mirrors `@cloudflare/shell/prompt.ts` — provides type definitions
and a system prompt that can be injected into agent instructions.

Usage:
    from py_fs_shell.prompt import STATE_TYPES, STATE_SYSTEM_PROMPT

    system = STATE_SYSTEM_PROMPT.replace("{{types}}", STATE_TYPES)
"""

from __future__ import annotations

STATE_TYPES = """
# ── Primitive types ────────────────────────────────────────────────────

StateEntryType = Literal["file", "directory", "symlink"]

StateStat = {
    "type": StateEntryType,
    "size": int,
    "mtime": datetime,
    "mode": int | None,
}

StateDirent = {
    "name": str,
    "type": StateEntryType,
}

# ── Options ──────────────────────────────────────────────────────────────

StateMkdirOptions     = {"recursive": bool}
StateRmOptions        = {"recursive": bool, "force": bool}
StateCopyOptions      = {"recursive": bool}
StateMoveOptions      = {"recursive": bool}
StateTreeOptions      = {"max_depth": int}
StateJsonWriteOptions = {"spaces": int | None}
StateHashOptions      = {"algorithm": "md5" | "sha1" | "sha256"}

StateSearchOptions = {
    "case_sensitive": bool,
    "regex": bool,
    "whole_word": bool,
    "context_before": int,
    "context_after": int,
    "max_matches": int | None,
}

StateReplaceInFilesOptions = StateSearchOptions & {
    "dry_run": bool,
    "rollback_on_error": bool,
}

StateApplyEditsOptions = {
    "dry_run": bool,
    "rollback_on_error": bool,
}

StateFindOptions = {
    "name": str | None,
    "path_pattern": str | None,
    "type": StateEntryType | list[StateEntryType] | None,
    "min_depth": int | None,
    "max_depth": int | None,
    "empty": bool | None,
    "size_min": int | None,
    "size_max": int | None,
    "mtime_after": str | datetime | None,
    "mtime_before": str | datetime | None,
}

# ── Result types ─────────────────────────────────────────────────────────

StateTextMatch = {
    "line": int,
    "column": int,
    "match": str,
    "line_text": str,
    "before_lines": list[str],
    "after_lines": list[str],
}

StateFindEntry = {
    "path": str,
    "name": str,
    "type": StateEntryType,
    "depth": int,
    "size": int,
    "mtime": datetime,
}

StateTreeNode = {
    "path": str,
    "name": str,
    "type": StateEntryType,
    "size": int,
    "children": list[StateTreeNode] | None,
}

StateTreeSummary = {
    "files": int,
    "directories": int,
    "symlinks": int,
    "total_bytes": int,
    "max_depth": int,
}

StateFileDetection = {
    "mime": str,
    "description": str,
    "extension": str | None,
    "binary": bool,
}

StateFileSearchResult = {
    "path": str,
    "matches": list[StateTextMatch],
}

StateReplaceResult = {"replaced": int, "content": str}

StateFileReplaceResult = {
    "path": str,
    "replaced": int,
    "content": str,
    "diff": str,
}

StateReplaceInFilesResult = {
    "dry_run": bool,
    "files": list[StateFileReplaceResult],
    "total_files": int,
    "total_replacements": int,
}

StateJsonUpdateOperation =
    | {"op": "set",    "path": str, "value": Any}
    | {"op": "delete", "path": str}

StateJsonUpdateResult = {
    "value": Any,
    "content": str,
    "diff": str,
    "operations_applied": int,
}

StateArchiveEntry = {"path": str, "type": "file"|"directory", "size": int}

StateArchiveCreateResult = {
    "path": str,
    "entries": list[StateArchiveEntry],
    "bytes_written": int,
}

StateArchiveExtractResult = {
    "destination": str,
    "entries": list[StateArchiveEntry],
}

StateCompressionResult = {
    "path": str,
    "destination": str,
    "bytes_written": int,
}

# ── Edit planning ────────────────────────────────────────────────────────

StateEdit = {"path": str, "content": str}

StateEditInstruction =
    | {"kind": "write",     "path": str, "content": str}
    | {"kind": "replace",   "path": str, "search": str, "replacement": str, "options": StateSearchOptions | None}
    | {"kind": "writeJson", "path": str, "value": Any, "options": StateJsonWriteOptions | None}

StatePlannedEdit = {
    "instruction": StateEditInstruction,
    "path": str,
    "changed": bool,
    "content": str,
    "diff": str,
}

StateEditPlan = {
    "edits": list[StatePlannedEdit],
    "total_changed": int,
    "total_instructions": int,
}

StateAppliedEditResult = {
    "path": str,
    "changed": bool,
    "content": str,
    "diff": str,
}

StateApplyEditsResult = {
    "dry_run": bool,
    "edits": list[StateAppliedEditResult],
    "total_changed": int,
}

# ── state object API ─────────────────────────────────────────────────────

class StateBackend:
    # File I/O
    async def read_file(path: str) -> str: ...
    async def read_file_bytes(path: str) -> bytes: ...
    async def write_file(path: str, content: str) -> None: ...
    async def write_file_bytes(path: str, content: bytes) -> None: ...
    async def append_file(path: str, content: str | bytes) -> None: ...

    # JSON helpers
    async def read_json(path: str) -> Any: ...
    async def write_json(path: str, value: Any, options: StateJsonWriteOptions | None = None) -> None: ...
    async def query_json(path: str, query: str) -> Any: ...
    async def update_json(path: str, operations: list[StateJsonUpdateOperation]) -> StateJsonUpdateResult: ...

    # Metadata & directories
    async def exists(path: str) -> bool: ...
    async def stat(path: str) -> StateStat | None: ...
    async def lstat(path: str) -> StateStat | None: ...
    async def mkdir(path: str, options: StateMkdirOptions | None = None) -> None: ...
    async def readdir(path: str) -> list[str]: ...
    async def readdir_with_file_types(path: str) -> list[StateDirent]: ...

    # Tree traversal
    async def find(path: str, options: StateFindOptions | None = None) -> list[StateFindEntry]: ...
    async def walk_tree(path: str, options: StateTreeOptions | None = None) -> StateTreeNode: ...
    async def summarize_tree(path: str, options: StateTreeOptions | None = None) -> StateTreeSummary: ...

    # Search & replace
    async def search_text(path: str, query: str, options: StateSearchOptions | None = None) -> list[StateTextMatch]: ...
    async def search_files(pattern: str, query: str, options: StateSearchOptions | None = None) -> list[StateFileSearchResult]: ...
    async def replace_in_file(path: str, search: str, replacement: str, options: StateSearchOptions | None = None) -> StateReplaceResult: ...
    async def replace_in_files(pattern: str, search: str, replacement: str, options: StateReplaceInFilesOptions | None = None) -> StateReplaceInFilesResult: ...

    # File operations
    async def rm(path: str, options: StateRmOptions | None = None) -> None: ...
    async def cp(src: str, dest: str, options: StateCopyOptions | None = None) -> None: ...
    async def mv(src: str, dest: str, options: StateMoveOptions | None = None) -> None: ...
    async def symlink(target: str, link_path: str) -> None: ...
    async def readlink(path: str) -> str: ...
    async def realpath(path: str) -> str: ...
    async def resolve_path(base: str, path: str) -> str: ...
    async def glob(pattern: str) -> list[str]: ...
    async def diff(path_a: str, path_b: str) -> str: ...
    async def diff_content(path: str, new_content: str) -> str: ...
    async def remove_tree(path: str) -> None: ...
    async def copy_tree(src: str, dest: str) -> None: ...
    async def move_tree(src: str, dest: str) -> None: ...

    # Archives & compression
    async def create_archive(path: str, sources: list[str]) -> StateArchiveCreateResult: ...
    async def list_archive(path: str) -> list[StateArchiveEntry]: ...
    async def extract_archive(path: str, destination: str) -> StateArchiveExtractResult: ...
    async def compress_file(path: str, destination: str | None = None) -> StateCompressionResult: ...
    async def decompress_file(path: str, destination: str | None = None) -> StateCompressionResult: ...
    async def hash_file(path: str, options: StateHashOptions | None = None) -> str: ...
    async def detect_file(path: str) -> StateFileDetection: ...

    # Structured edit planning
    async def plan_edits(instructions: list[StateEditInstruction]) -> StateEditPlan: ...
    async def apply_edit_plan(plan: StateEditPlan, options: StateApplyEditsOptions | None = None) -> StateApplyEditsResult: ...
    async def apply_edits(edits: list[StateEdit], options: StateApplyEditsOptions | None = None) -> StateApplyEditsResult: ...
""".strip()


STATE_SYSTEM_PROMPT = """
You can write Python code that runs inside an isolated sandbox with access to a
persistent virtual filesystem through the `state` object.

Rules:
- Write an async function: `async () -> { ... return result; }`
- Do NOT use `import` statements — all helpers are available through `state`.
- Always `return` the final value you want back.
- For multi-file refactors, prefer `plan_edits()` + `apply_edit_plan()` over many individual writes.
- For search-and-replace across a tree, use `replace_in_files()` — it is transactional by default.

Available API (Python reference):

```python
{{types}}
```
""".strip()
