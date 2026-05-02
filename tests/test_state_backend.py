"""Tests for FileSystemStateBackend - high-level state operations."""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from py_shell.memory_backend import FileSystemStateBackend, create_memory_state_backend
from py_shell.backend import (
    StateFindOptions,
    StateSearchOptions,
    StateJsonWriteOptions,
    StateJsonUpdateOperation,
    StateHashOptions,
    StateReplaceInFilesOptions,
    StateWriteEditInstruction,
    StateReplaceEditInstruction,
    StateWriteJsonEditInstruction,
    StateApplyEditsOptions,
    StateEdit,
)


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

    # === Factory ===
    print("=== Factory ===")

    async def t_factory():
        backend = create_memory_state_backend({"/test.txt": "hello"})
        assert await backend.read_file("/test.txt") == "hello"
    await check("create_from_string", t_factory)

    async def t_factory_json():
        backend = create_memory_state_backend({"/config.json": {"key": "value"}})
        data = await backend.read_json("/config.json")
        assert data == {"key": "value"}
    await check("create_from_json", t_factory_json)

    # === JSON helpers ===
    print("\n=== JSON ===")

    async def t_json_rw():
        backend = create_memory_state_backend()
        await backend.write_json("/data.json", {"name": "test", "count": 42})
        data = await backend.read_json("/data.json")
        assert data["name"] == "test"
        assert data["count"] == 42
    await check("json_rw", t_json_rw)

    async def t_json_indent():
        backend = create_memory_state_backend()
        await backend.write_json("/pretty.json", {"a": 1}, StateJsonWriteOptions(spaces=4))
        content = await backend.read_file("/pretty.json")
        assert '    "a":' in content  # 4-space indent
    await check("json_indent", t_json_indent)

    async def t_json_query():
        backend = create_memory_state_backend()
        await backend.write_json("/data.json", {"users": [{"name": "Alice"}, {"name": "Bob"}]})
        result = await backend.query_json("/data.json", "/users/0/name")
        assert result == "Alice"
    await check("json_query", t_json_query)

    async def t_json_update():
        backend = create_memory_state_backend()
        await backend.write_json("/data.json", {"a": 1, "b": 2})
        result = await backend.update_json("/data.json", [
            StateJsonUpdateOperation(op="set", path="/c", value=3),
            StateJsonUpdateOperation(op="delete", path="/b"),
        ])
        assert result.value == {"a": 1, "c": 3}
        assert result.operations_applied == 2
    await check("json_update", t_json_update)

    # === Search ===
    print("\n=== Search ===")

    async def t_search_text():
        backend = create_memory_state_backend({"/file.txt": "hello world\nhello again"})
        matches = await backend.search_text("/file.txt", "hello")
        assert len(matches) == 2
        assert matches[0].line == 1
        assert matches[1].line == 2
    await check("search_text", t_search_text)

    async def t_search_regex():
        backend = create_memory_state_backend({"/file.txt": "hello world\ngoodbye world"})
        matches = await backend.search_text("/file.txt", r"^hello", StateSearchOptions(regex=True))
        assert len(matches) == 1
        assert matches[0].match == "hello"
    await check("search_regex", t_search_regex)

    async def t_search_files():
        backend = create_memory_state_backend({
            "/a.txt": "hello world",
            "/b.txt": "goodbye world",
            "/c.py": "hello python",
        })
        results = await backend.search_files("*.txt", "hello")
        assert len(results) == 1
        assert results[0].path == "/a.txt"
    await check("search_files", t_search_files)

    # === Replace ===
    print("\n=== Replace ===")

    async def t_replace_in_file():
        backend = create_memory_state_backend({"/file.txt": "hello world"})
        result = await backend.replace_in_file("/file.txt", "world", "universe")
        assert result.replaced == 1
        assert await backend.read_file("/file.txt") == "hello universe"
    await check("replace_in_file", t_replace_in_file)

    async def t_replace_in_files():
        backend = create_memory_state_backend({
            "/a.txt": "hello world",
            "/b.txt": "hello moon",
        })
        result = await backend.replace_in_files("*.txt", "hello", "hi")
        assert result.total_replacements == 2
        assert result.total_files == 2
        assert await backend.read_file("/a.txt") == "hi world"
    await check("replace_in_files", t_replace_in_files)

    # === Diff ===
    print("\n=== Diff ===")

    async def t_diff():
        backend = create_memory_state_backend({
            "/a.txt": "line1\nline2\n",
            "/b.txt": "line1\nmodified\n",
        })
        diff = await backend.diff("/a.txt", "/b.txt")
        assert "-line2" in diff
        assert "+modified" in diff
    await check("diff", t_diff)

    async def t_diff_content():
        backend = create_memory_state_backend({"/file.txt": "original"})
        diff = await backend.diff_content("/file.txt", "modified")
        assert "-original" in diff
        assert "+modified" in diff
    await check("diff_content", t_diff_content)

    # === Find ===
    print("\n=== Find ===")

    async def t_find():
        backend = create_memory_state_backend({
            "/a/1.txt": "x",
            "/a/b/2.txt": "y",
            "/c/3.txt": "z",
        })
        results = await backend.find("/")
        paths = [r.path for r in results]
        assert "/a/1.txt" in paths
        assert "/a/b/2.txt" in paths
    await check("find", t_find)

    async def t_find_by_name():
        backend = create_memory_state_backend({
            "/a.md": "a",
            "/b.md": "b",
            "/c.txt": "c",
        })
        results = await backend.find("/", StateFindOptions(name="*.md"))
        assert len(results) == 2
        assert all(r.name.endswith(".md") for r in results)
    await check("find_by_name", t_find_by_name)

    # === Tree ===
    print("\n=== Tree ===")

    async def t_walk_tree():
        backend = create_memory_state_backend({
            "/a/b/c.txt": "deep",
            "/a/d.txt": "shallow",
        })
        tree = await backend.walk_tree("/")
        assert tree.name == "/"
        assert tree.children is not None
        assert len(tree.children) == 1  # 'a'
        assert len(tree.children[0].children) == 2  # 'b', 'd'
    await check("walk_tree", t_walk_tree)

    async def t_summarize_tree():
        backend = create_memory_state_backend({
            "/a/b/c.txt": "deep",
            "/a/d.txt": "shallow",
        })
        summary = await backend.summarize_tree("/")
        assert summary.files == 2
        assert summary.directories == 3  # /, /a, /a/b
        assert summary.max_depth >= 2
    await check("summarize_tree", t_summarize_tree)

    # === Hash & Detect ===
    print("\n=== Hash & Detect ===")

    async def t_hash():
        backend = create_memory_state_backend({"/file.txt": "hello"})
        h = await backend.hash_file("/file.txt")
        import hashlib
        expected = hashlib.sha256(b"hello").hexdigest()
        assert h == expected
    await check("hash_sha256", t_hash)

    async def t_hash_md5():
        backend = create_memory_state_backend({"/file.txt": "hello"})
        h = await backend.hash_file("/file.txt", StateHashOptions(algorithm="md5"))
        import hashlib
        expected = hashlib.md5(b"hello").hexdigest()
        assert h == expected
    await check("hash_md5", t_hash_md5)

    async def t_detect():
        backend = create_memory_state_backend({"/main.py": "print('hi')"})
        d = await backend.detect_file("/main.py")
        assert "python" in d.mime or "text" in d.mime
        assert not d.binary
    await check("detect_python", t_detect)

    # === Archives ===
    print("\n=== Archives ===")

    async def t_archive():
        backend = create_memory_state_backend({
            "/src/main.py": "print('hi')",
            "/src/lib.py": "def f(): pass",
        })
        result = await backend.create_archive("/archive.tar.gz", ["/src"])
        assert result.bytes_written > 0
        assert len(result.entries) > 0

        # List
        listed = await backend.list_archive("/archive.tar.gz")
        assert len(listed) > 0

        # Extract
        extracted = await backend.extract_archive("/archive.tar.gz", "/extracted")
        assert len(extracted.entries) > 0
    await check("archive", t_archive)

    # === Compression ===
    print("\n=== Compression ===")

    async def t_compress():
        backend = create_memory_state_backend({"/file.txt": "hello world" * 100})
        result = await backend.compress_file("/file.txt")
        assert result.destination == "/file.txt.gz"
        compressed = await backend.read_file_bytes("/file.txt.gz")
        assert len(compressed) < len(b"hello world" * 100)  # Should be compressed
    await check("compress", t_compress)

    # === Edit planning ===
    print("\n=== Edit Planning ===")

    async def t_plan_write():
        backend = create_memory_state_backend()
        plan = await backend.plan_edits([
            StateWriteEditInstruction(path="/new.py", content="x = 1"),
        ])
        assert plan.total_changed == 1
        assert plan.edits[0].changed
    await check("plan_write", t_plan_write)

    async def t_plan_replace():
        backend = create_memory_state_backend({"/file.txt": "hello world"})
        plan = await backend.plan_edits([
            StateReplaceEditInstruction(path="/file.txt", search="hello", replacement="hi"),
        ])
        assert plan.total_changed == 1
    await check("plan_replace", t_plan_replace)

    async def t_apply_edits():
        backend = create_memory_state_backend()
        result = await backend.apply_edits([
            StateEdit(path="/a.py", content="x = 1"),
        ])
        assert result.total_changed == 1
        assert await backend.read_file("/a.py") == "x = 1"
    await check("apply_edits", t_apply_edits)

    print("\n" + "=" * 50)
    print(f"Results: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    ok = asyncio.run(run_tests())
    if not ok:
        import sys
        sys.exit(1)
