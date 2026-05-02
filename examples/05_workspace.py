"""Workspace presets: memory, local, and S3-style composition."""

from __future__ import annotations

import asyncio
import tempfile

from py_fs_shell import workspace


async def main() -> None:
    mem = await workspace.memory()
    state = mem.state()

    await state.write_file("/notes/todo.txt", "ship workspace\n")
    await state.write_file_bytes("/media/clip.mp4", b"fake video bytes")
    print(await state.readdir("/"))
    print(await state.read_file("/notes/todo.txt"))

    with tempfile.TemporaryDirectory() as tmp:
        local = await workspace.local(tmp)
        await local.write_file("/persisted.txt", "stored through SQLite metadata + local blobs")

        reopened = await workspace.local(tmp)
        print(await reopened.read_file("/persisted.txt"))

    # S3 usage, if boto3 and credentials are configured.
    # Both metadata and blobs are remote by default:
    # metadata -> s3://my-bucket/runs/123/.py_fs_shell/metadata.json
    # blobs    -> s3://my-bucket/runs/123/<sha256>
    # s3_ws = await workspace.s3(bucket="my-bucket", prefix="runs/123")
    # await s3_ws.write_file_bytes("/video.mp4", video_bytes)


if __name__ == "__main__":
    asyncio.run(main())
