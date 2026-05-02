"""StateBackend interface - high-level filesystem/state operations.

This mirrors the @cloudflare/shell backend.ts, providing a rich API
on top of the basic FileSystem interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

# ── Types ────────────────────────────────────────────────────────────

StateEntryType = Literal["file", "directory", "symlink"]


@dataclass(frozen=True)
class StateCapabilities:
    chmod: bool = True
    utimes: bool = True
    hard_links: bool = True


@dataclass(frozen=True)
class StateDirent:
    name: str
    type: StateEntryType


@dataclass(frozen=True)
class StateStat:
    type: StateEntryType
    size: int
    mtime: datetime
    mode: int | None = None


@dataclass(frozen=True)
class StateMkdirOptions:
    recursive: bool = False


@dataclass(frozen=True)
class StateRmOptions:
    recursive: bool = False
    force: bool = False


@dataclass(frozen=True)
class StateCopyOptions:
    recursive: bool = False


@dataclass(frozen=True)
class StateMoveOptions:
    recursive: bool = False


@dataclass(frozen=True)
class StateJsonWriteOptions:
    spaces: int | None = None


@dataclass(frozen=True)
class StateSearchOptions:
    case_sensitive: bool = False
    regex: bool = False
    whole_word: bool = False
    context_before: int = 0
    context_after: int = 0
    max_matches: int | None = None


@dataclass(frozen=True)
class StateTextMatch:
    line: int
    column: int
    match: str
    line_text: str
    before_lines: list[str] = field(default_factory=list)
    after_lines: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StateFindOptions:
    name: str | None = None
    path_pattern: str | None = None
    type: StateEntryType | list[StateEntryType] | None = None
    min_depth: int | None = None
    max_depth: int | None = None
    empty: bool | None = None
    size_min: int | None = None
    size_max: int | None = None
    mtime_after: str | datetime | None = None
    mtime_before: str | datetime | None = None


@dataclass(frozen=True)
class StateFindEntry:
    path: str
    name: str
    type: StateEntryType
    depth: int
    size: int
    mtime: datetime


@dataclass
class StateJsonUpdateOperation:
    op: Literal["set", "delete"]
    path: str  # JSON pointer style path like "/foo/bar"
    value: Any = field(default=None)


@dataclass(frozen=True)
class StateJsonUpdateResult:
    value: Any
    content: str
    diff: str
    operations_applied: int


@dataclass(frozen=True)
class StateArchiveEntry:
    path: str
    type: Literal["file", "directory"]
    size: int


@dataclass(frozen=True)
class StateArchiveCreateResult:
    path: str
    entries: list[StateArchiveEntry]
    bytes_written: int


@dataclass(frozen=True)
class StateArchiveExtractResult:
    destination: str
    entries: list[StateArchiveEntry]


@dataclass(frozen=True)
class StateCompressionResult:
    path: str
    destination: str
    bytes_written: int


@dataclass(frozen=True)
class StateTreeOptions:
    max_depth: int | None = None


@dataclass(frozen=True)
class StateTreeNode:
    path: str
    name: str
    type: StateEntryType
    size: int
    children: list[StateTreeNode] | None = None


@dataclass(frozen=True)
class StateTreeSummary:
    files: int
    directories: int
    symlinks: int
    total_bytes: int
    max_depth: int


@dataclass(frozen=True)
class StateFileDetection:
    mime: str
    description: str
    extension: str | None = None
    binary: bool = True


@dataclass(frozen=True)
class StateHashOptions:
    algorithm: Literal["md5", "sha1", "sha256"] = "sha256"


@dataclass(frozen=True)
class StateReplaceResult:
    replaced: int
    content: str


@dataclass(frozen=True)
class StateFileSearchResult:
    path: str
    matches: list[StateTextMatch]


@dataclass(frozen=True)
class StateReplaceInFilesOptions(StateSearchOptions):
    dry_run: bool = False
    rollback_on_error: bool = True


@dataclass(frozen=True)
class StateFileReplaceResult:
    path: str
    replaced: int
    content: str
    diff: str


@dataclass(frozen=True)
class StateReplaceInFilesResult:
    dry_run: bool
    files: list[StateFileReplaceResult]
    total_files: int
    total_replacements: int


@dataclass(frozen=True)
class StateEdit:
    path: str
    content: str


@dataclass(frozen=True)
class StateWriteEditInstruction:
    kind: Literal["write"] = "write"
    path: str = ""
    content: str = ""


@dataclass(frozen=True)
class StateReplaceEditInstruction:
    kind: Literal["replace"] = "replace"
    path: str = ""
    search: str = ""
    replacement: str = ""
    options: StateSearchOptions | None = None


@dataclass(frozen=True)
class StateWriteJsonEditInstruction:
    kind: Literal["writeJson"] = "writeJson"
    path: str = ""
    value: Any = field(default=None)
    options: StateJsonWriteOptions | None = None


StateEditInstruction = StateWriteEditInstruction | StateReplaceEditInstruction | StateWriteJsonEditInstruction


@dataclass(frozen=True)
class StateApplyEditsOptions:
    dry_run: bool = False
    rollback_on_error: bool = True


@dataclass(frozen=True)
class StateAppliedEditResult:
    path: str
    changed: bool
    content: str
    diff: str


@dataclass(frozen=True)
class StateApplyEditsResult:
    dry_run: bool
    edits: list[StateAppliedEditResult]
    total_changed: int


@dataclass(frozen=True)
class StatePlannedEdit:
    instruction: StateEditInstruction
    path: str
    changed: bool
    content: str
    diff: str


@dataclass(frozen=True)
class StateEditPlan:
    edits: list[StatePlannedEdit]
    total_changed: int
    total_instructions: int


@dataclass(frozen=True)
class StateExecuteResult:
    result: Any
    error: str | None = None
    logs: list[str] | None = None


class StateBatchOperationError(Exception):
    """Error raised when a batch operation fails and rollback is attempted."""

    def __init__(
        self,
        operation: str,
        message: str,
        rolled_back: bool,
        rollback_error: str | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.rolled_back = rolled_back
        self.rollback_error = rollback_error


# ── StateBackend ABC ─────────────────────────────────────────────────

class StateBackend(ABC):
    """High-level state backend interface.

    Wraps a FileSystem with additional operations useful for agent workflows:
    JSON helpers, search/replace, compression, diff, structured editing, etc.
    """

    @abstractmethod
    async def get_capabilities(self) -> StateCapabilities:
        ...

    @abstractmethod
    async def read_file(self, path: str) -> str:
        ...

    @abstractmethod
    async def read_file_bytes(self, path: str) -> bytes:
        ...

    @abstractmethod
    async def write_file(self, path: str, content: str) -> None:
        ...

    @abstractmethod
    async def write_file_bytes(self, path: str, content: bytes) -> None:
        ...

    @abstractmethod
    async def append_file(self, path: str, content: str | bytes) -> None:
        ...

    @abstractmethod
    async def read_json(self, path: str) -> Any:
        ...

    @abstractmethod
    async def write_json(self, path: str, value: Any, options: StateJsonWriteOptions | None = None) -> None:
        ...

    @abstractmethod
    async def query_json(self, path: str, query: str) -> Any:
        ...

    @abstractmethod
    async def update_json(self, path: str, operations: list[StateJsonUpdateOperation]) -> StateJsonUpdateResult:
        ...

    @abstractmethod
    async def exists(self, path: str) -> bool:
        ...

    @abstractmethod
    async def stat(self, path: str) -> StateStat | None:
        ...

    @abstractmethod
    async def lstat(self, path: str) -> StateStat | None:
        ...

    @abstractmethod
    async def mkdir(self, path: str, options: StateMkdirOptions | None = None) -> None:
        ...

    @abstractmethod
    async def readdir(self, path: str) -> list[str]:
        ...

    @abstractmethod
    async def readdir_with_file_types(self, path: str) -> list[StateDirent]:
        ...

    @abstractmethod
    async def find(self, path: str, options: StateFindOptions | None = None) -> list[StateFindEntry]:
        ...

    @abstractmethod
    async def walk_tree(self, path: str, options: StateTreeOptions | None = None) -> StateTreeNode:
        ...

    @abstractmethod
    async def summarize_tree(self, path: str, options: StateTreeOptions | None = None) -> StateTreeSummary:
        ...

    @abstractmethod
    async def search_text(self, path: str, query: str, options: StateSearchOptions | None = None) -> list[StateTextMatch]:
        ...

    @abstractmethod
    async def search_files(self, pattern: str, query: str, options: StateSearchOptions | None = None) -> list[StateFileSearchResult]:
        ...

    @abstractmethod
    async def replace_in_file(self, path: str, search: str, replacement: str, options: StateSearchOptions | None = None) -> StateReplaceResult:
        ...

    @abstractmethod
    async def replace_in_files(self, pattern: str, search: str, replacement: str, options: StateReplaceInFilesOptions | None = None) -> StateReplaceInFilesResult:
        ...

    @abstractmethod
    async def rm(self, path: str, options: StateRmOptions | None = None) -> None:
        ...

    @abstractmethod
    async def cp(self, src: str, dest: str, options: StateCopyOptions | None = None) -> None:
        ...

    @abstractmethod
    async def mv(self, src: str, dest: str, options: StateMoveOptions | None = None) -> None:
        ...

    @abstractmethod
    async def symlink(self, target: str, link_path: str) -> None:
        ...

    @abstractmethod
    async def readlink(self, path: str) -> str:
        ...

    @abstractmethod
    async def realpath(self, path: str) -> str:
        ...

    @abstractmethod
    async def resolve_path(self, base: str, path: str) -> str:
        ...

    @abstractmethod
    async def glob(self, pattern: str) -> list[str]:
        ...

    @abstractmethod
    async def diff(self, path_a: str, path_b: str) -> str:
        ...

    @abstractmethod
    async def diff_content(self, path: str, new_content: str) -> str:
        ...

    @abstractmethod
    async def create_archive(self, path: str, sources: list[str]) -> StateArchiveCreateResult:
        ...

    @abstractmethod
    async def list_archive(self, path: str) -> list[StateArchiveEntry]:
        ...

    @abstractmethod
    async def extract_archive(self, path: str, destination: str) -> StateArchiveExtractResult:
        ...

    @abstractmethod
    async def compress_file(self, path: str, destination: str | None = None) -> StateCompressionResult:
        ...

    @abstractmethod
    async def decompress_file(self, path: str, destination: str | None = None) -> StateCompressionResult:
        ...

    @abstractmethod
    async def hash_file(self, path: str, options: StateHashOptions | None = None) -> str:
        ...

    @abstractmethod
    async def detect_file(self, path: str) -> StateFileDetection:
        ...

    @abstractmethod
    async def remove_tree(self, path: str) -> None:
        ...

    @abstractmethod
    async def copy_tree(self, src: str, dest: str) -> None:
        ...

    @abstractmethod
    async def move_tree(self, src: str, dest: str) -> None:
        ...

    @abstractmethod
    async def plan_edits(self, instructions: list[StateEditInstruction]) -> StateEditPlan:
        ...

    @abstractmethod
    async def apply_edit_plan(self, plan: StateEditPlan, options: StateApplyEditsOptions | None = None) -> StateApplyEditsResult:
        ...

    @abstractmethod
    async def apply_edits(self, edits: list[StateEdit], options: StateApplyEditsOptions | None = None) -> StateApplyEditsResult:
        ...
