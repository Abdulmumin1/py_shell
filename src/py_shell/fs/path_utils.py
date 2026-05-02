"""Path normalization and validation utilities."""

from __future__ import annotations

import posixpath
import re

DEFAULT_FILE_MODE = 0o644
DEFAULT_DIR_MODE = 0o755
SYMLINK_MODE = 0o777
MAX_SYMLINK_DEPTH = 40
MAX_PATH_LENGTH = 4096
MAX_SYMLINK_TARGET_LENGTH = 4096
MAX_MKDIR_DEPTH = 100


def normalize_path(path: str) -> str:
    """Normalize a path to absolute POSIX form.

    - Ensures leading /
    - Collapses . and ..
    - Removes redundant slashes
    """
    if not path:
        return "/"
    if not path.startswith("/"):
        path = "/" + path
    resolved = posixpath.normpath(path)
    if resolved == "":
        return "/"
    return resolved


def split_path(normalized: str) -> list[str]:
    """Split a normalized path into segments (drop leading empty)."""
    if normalized == "/":
        return []
    return normalized[1:].split("/")


def parent_dir(path: str) -> str:
    """Return the parent directory of a path."""
    normalized = normalize_path(path)
    if normalized == "/":
        return "/"
    parent = posixpath.dirname(normalized)
    return parent if parent else "/"


def join_path(*parts: str) -> str:
    """Join path parts and normalize."""
    return normalize_path(posixpath.join(*parts))


def validate_path(path: str, operation: str = "operation") -> str:
    """Validate a path for common issues.

    Raises ValueError if path is too long or contains null bytes.
    """
    if "\x00" in path:
        raise ValueError(f"{operation}: path contains null byte")
    if len(path) > MAX_PATH_LENGTH:
        raise ValueError(
            f"{operation}: path too long ({len(path)} > {MAX_PATH_LENGTH})"
        )
    return path


def validate_symlink_target(target: str) -> str:
    """Validate a symlink target path.

    Raises ValueError if target is too long.
    """
    if "\x00" in target:
        raise ValueError("symlink target contains null byte")
    if len(target) > MAX_SYMLINK_TARGET_LENGTH:
        raise ValueError(
            f"symlink target too long ({len(target)} > {MAX_SYMLINK_TARGET_LENGTH})"
        )
    return target


def create_enoent(path: str, operation: str = "stat") -> FileNotFoundError:
    """Create a FileNotFoundError with a POSIX-style message."""
    return FileNotFoundError(
        f"ENOENT: no such file or directory, {operation} '{path}'"
    )


def create_eexist(path: str, operation: str = "mkdir") -> FileExistsError:
    """Create a FileExistsError with a POSIX-style message."""
    return FileExistsError(
        f"EEXIST: file or directory already exists, {operation} '{path}'"
    )


def create_enotdir(path: str, operation: str = "stat") -> NotADirectoryError:
    """Create a NotADirectoryError with a POSIX-style message."""
    return NotADirectoryError(
        f"ENOTDIR: not a directory, {operation} '{path}'"
    )


def create_eisdir(path: str, operation: str = "read") -> IsADirectoryError:
    """Create an IsADirectoryError with a POSIX-style message."""
    return IsADirectoryError(
        f"EISDIR: is a directory, {operation} '{path}'"
    )


def create_eloop(path: str) -> OSError:
    """Create an OSError for too many symlink levels."""
    err = OSError(f"ELOOP: too many levels of symbolic links: '{path}'")
    err.errno = 40  # ELOOP on POSIX
    return err


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Convert a simple glob pattern to a regex pattern.

    Supports:
      - *  -> match any chars except /
      - ** -> match any chars including /
      - ?  -> match single char except /
    """
    # Simple implementation - could be expanded
    parts = []
    i = 0
    while i < len(pattern):
        if pattern[i:i + 2] == "**":
            parts.append(".*")
            i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            parts.append("[^/]")
            i += 1
        elif pattern[i] == "[":
            # Character class
            end = pattern.find("]", i + 1)
            if end == -1:
                parts.append(re.escape("["))
                i += 1
            else:
                parts.append(pattern[i:end + 1])
                i = end + 1
        else:
            parts.append(re.escape(pattern[i]))
            i += 1

    regex = "^" + "".join(parts) + "$"
    return re.compile(regex)
