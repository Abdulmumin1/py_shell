"""Workspace tests."""

from __future__ import annotations

import asyncio
import io
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from py_shell import SQLiteMetadataStore, workspace
from py_shell.fs.interface import CpOptions, MkdirOptions, RmOptions


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, Bucket: str, Key: str, Body: bytes, **kwargs):
        self.objects[(Bucket, Key)] = Body if isinstance(Body, bytes) else Body.read()
        return {}

    def get_object(self, Bucket: str, Key: str):
        try:
            data = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise FileNotFoundError(Key) from exc
        return {"Body": io.BytesIO(data)}

    def delete_object(self, Bucket: str, Key: str):
        self.objects.pop((Bucket, Key), None)
        return {}


async def run_tests():
    passed = 0
    failed = 0

    async def check(name, test):
        nonlocal passed, failed
        try:
            await test()
            passed += 1
            print(f"  PASS: {name}")
        except Exception as e:
            failed += 1
            print(f"  FAIL: {name} - {e}")
            import traceback
            traceback.print_exc()

    print("=== Workspace ===")

    async def t_memory_workspace_state():
        ws = await workspace.memory()
        state = ws.state()
        await state.write_file("/src/app.py", "print('hi')\n")
        await state.write_file_bytes("/video.mp4", b"\x00\x00video")
        assert await state.read_file("/src/app.py") == "print('hi')\n"
        assert await state.read_file_bytes("/video.mp4") == b"\x00\x00video"
        assert "/src/app.py" in await state.glob("**/*.py")
    await check("memory_workspace_state", t_memory_workspace_state)

    async def t_local_workspace_persists():
        with tempfile.TemporaryDirectory() as tmp:
            ws1 = await workspace.local(tmp)
            await ws1.write_file("/hello.txt", "hello")
            ws2 = await workspace.local(tmp)
            assert await ws2.read_file("/hello.txt") == "hello"
            assert (Path(tmp) / "metadata.sqlite3").exists()
            assert (Path(tmp) / "blobs").exists()
    await check("local_workspace_persists", t_local_workspace_persists)

    async def t_s3_workspace_is_fully_remote_by_default():
        client = FakeS3Client()
        ws1 = await workspace.s3(bucket="bucket", prefix="runs/1", client=client)
        await ws1.write_file_bytes("/media/video.mp4", b"video-bytes")

        ws2 = await workspace.s3(bucket="bucket", prefix="runs/1", client=client)
        assert await ws2.read_file_bytes("/media/video.mp4") == b"video-bytes"
        assert ("bucket", "runs/1/.py_shell/metadata.json") in client.objects
        assert not Path(".py_shell_workspace/metadata.sqlite3").exists()
    await check("s3_workspace_is_fully_remote_by_default", t_s3_workspace_is_fully_remote_by_default)

    async def t_s3_workspace_allows_custom_metadata_store():
        client = FakeS3Client()
        with tempfile.TemporaryDirectory() as tmp:
            metadata = SQLiteMetadataStore(Path(tmp) / "metadata.sqlite3")
            ws = await workspace.s3(bucket="bucket", prefix="runs/2", client=client, metadata=metadata)
            await ws.write_file("/hello.txt", "hello")
            assert await ws.read_file("/hello.txt") == "hello"
            assert (Path(tmp) / "metadata.sqlite3").exists()
    await check("s3_workspace_allows_custom_metadata_store", t_s3_workspace_allows_custom_metadata_store)

    async def t_fs_ops():
        ws = await workspace.memory()
        fs = ws.fs()
        await fs.mkdir("/a/b", MkdirOptions(recursive=True))
        await fs.write_file("/a/b/c.txt", "c")
        await fs.cp("/a", "/copy", CpOptions(recursive=True))
        assert await fs.read_file("/copy/b/c.txt") == "c"
        await fs.mv("/copy/b/c.txt", "/copy/b/d.txt")
        assert await fs.read_file("/copy/b/d.txt") == "c"
        await fs.rm("/copy", RmOptions(recursive=True))
        assert not await fs.exists("/copy")
    await check("fs_ops", t_fs_ops)

    async def t_symlink():
        ws = await workspace.memory()
        await ws.write_file("/dir/target.txt", "ok")
        await ws.symlink("target.txt", "/dir/link.txt")
        assert await ws.read_file("/dir/link.txt") == "ok"
        assert await ws.readlink("/dir/link.txt") == "target.txt"
    await check("symlink", t_symlink)

    print("\n" + "=" * 50)
    print(f"Results: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    ok = asyncio.run(run_tests())
    if not ok:
        sys.exit(1)
