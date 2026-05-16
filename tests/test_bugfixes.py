from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from py_fs_shell import LocalFileSystem, workspace
from py_fs_shell.backend import (
    StateApplyEditsOptions,
    StateBatchOperationError,
    StateEdit,
    StateReplaceInFilesOptions,
)
from py_fs_shell.fs.interface import MkdirOptions, RmOptions
from py_fs_shell.memory_backend import create_memory_state_backend


@pytest.mark.asyncio
async def test_local_fs_denies_write_through_escape_symlink(tmp_path: Path) -> None:
    outside_dir = tmp_path.parent / f"{tmp_path.name}-outside"
    outside_dir.mkdir()
    secret = outside_dir / "secret.txt"
    secret.write_text("secret", encoding="utf-8")

    fs = LocalFileSystem(tmp_path)
    await fs.symlink(str(secret), "/escape.txt")

    with pytest.raises(PermissionError):
        await fs.write_file("/escape.txt", "owned")

    assert secret.read_text(encoding="utf-8") == "secret"


@pytest.mark.asyncio
async def test_workspace_resolves_intermediate_symlinks() -> None:
    ws = await workspace.memory()
    await ws.mkdir("/target/sub", MkdirOptions(recursive=True))
    await ws.write_file("/target/sub/file.txt", "ok")
    await ws.symlink("/target", "/dirlink")

    assert await ws.read_file("/dirlink/sub/file.txt") == "ok"
    assert await ws.realpath("/dirlink/sub/file.txt") == "/target/sub/file.txt"


@pytest.mark.asyncio
async def test_workspace_protects_root_from_recursive_rm_and_symlink() -> None:
    ws = await workspace.memory()
    await ws.write_file("/file.txt", "x")

    with pytest.raises(PermissionError):
        await ws.rm("/", RmOptions(recursive=True))

    with pytest.raises(IsADirectoryError):
        await ws.symlink("/target", "/")


@pytest.mark.asyncio
async def test_replace_in_files_respects_case_sensitive_option() -> None:
    backend = create_memory_state_backend({"/a.txt": "Hello hello"})

    result = await backend.replace_in_files(
        "*.txt",
        "hello",
        "hi",
        StateReplaceInFilesOptions(case_sensitive=True),
    )

    assert result.total_replacements == 1
    assert await backend.read_file("/a.txt") == "Hello hi"


@pytest.mark.asyncio
async def test_apply_edits_rollback_removes_new_files() -> None:
    backend = create_memory_state_backend()
    real_write = backend.fs.write_file
    calls = 0

    async def flaky_write(path: str, content: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("boom")
        await real_write(path, content)

    backend.fs.write_file = flaky_write  # type: ignore[method-assign]

    with pytest.raises(StateBatchOperationError):
        await backend.apply_edits(
            [StateEdit("/a.txt", "a"), StateEdit("/b.txt", "b")],
            StateApplyEditsOptions(rollback_on_error=True),
        )

    assert not await backend.exists("/a.txt")
    assert not await backend.exists("/b.txt")


@pytest.mark.asyncio
async def test_extract_archive_rejects_escaping_entries() -> None:
    backend = create_memory_state_backend()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        payload = b"oops"
        info = tarfile.TarInfo("../escape.txt")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    await backend.write_file_bytes("/archive.tar.gz", buf.getvalue())

    with pytest.raises(ValueError):
        await backend.extract_archive("/archive.tar.gz", "/safe")
