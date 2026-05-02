"""pytest fixtures for py_fs_shell tests."""

from __future__ import annotations

import asyncio

import pytest

from py_fs_shell.fs.in_memory import InMemoryFs
from py_fs_shell.memory_backend import FileSystemStateBackend, create_memory_state_backend


@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def empty_fs() -> InMemoryFs:
    return InMemoryFs()


@pytest.fixture
def sample_fs() -> InMemoryFs:
    return InMemoryFs({
        "/hello.txt": "Hello, World!",
        "/dir/nested.txt": "nested content",
    })


@pytest.fixture
def empty_backend() -> FileSystemStateBackend:
    return create_memory_state_backend()


@pytest.fixture
def sample_backend() -> FileSystemStateBackend:
    return create_memory_state_backend({
        "/hello.txt": "Hello, World!",
        "/dir/nested.txt": "nested content",
        "/dir/sub/subfile.txt": "deep nested",
    })


# Custom pytest marker for async tests
pytest_plugins = ["pytest_asyncio"]
