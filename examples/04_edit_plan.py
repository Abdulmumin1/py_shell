"""Plan structured edits, inspect diffs, then apply them."""

from __future__ import annotations

import asyncio

from py_fs_shell import (
    StateReplaceEditInstruction,
    StateWriteEditInstruction,
    StateWriteJsonEditInstruction,
    create_memory_state_backend,
)


async def main() -> None:
    state = create_memory_state_backend(
        {
            "/app.py": "name = 'world'\nprint(f'hello {name}')\n",
            "/settings.json": {"debug": False},
        }
    )

    plan = await state.plan_edits(
        [
            StateReplaceEditInstruction(
                path="/app.py",
                search="hello",
                replacement="hi",
            ),
            StateWriteEditInstruction(
                path="/README.md",
                content="# Generated project\n",
            ),
            StateWriteJsonEditInstruction(
                path="/settings.json",
                value={"debug": True, "workers": 2},
            ),
        ]
    )

    for edit in plan.edits:
        print(f"--- {edit.path} changed={edit.changed}")
        print(edit.diff)

    result = await state.apply_edit_plan(plan)
    print("changed:", result.total_changed)
    print(await state.read_file("/app.py"))


if __name__ == "__main__":
    asyncio.run(main())
