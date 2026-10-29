"""The side session: a read-only fork of a running session's conversation, asked one question.

The Agent SDK can resume a conversation as a fork: a copy holding the whole conversation up to
that moment, which then goes its own way while the original carries on untouched. A new fork
per question answers from the freshest state, and can read the project's files as the session
itself would. Nothing it does may change anything: a hook refuses every tool that could,
whatever the owner's own permission rules allow. See decisions/ADR-CV-008.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import Counter
from collections.abc import Callable
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolPermissionContext,
    ToolUseBlock,
)

# Tools that only read. Everything else is refused, including commands, which could read
# as well as write and can't be told apart.
READ_TOOLS = frozenset({"Read", "Grep", "Glob", "LS", "NotebookRead", "ToolSearch"})

SIDE_SESSION_NOTE = (
    "[From the claude-voice bridge: you are the side session, a fork of this conversation made "
    "just now to answer one question from the relay, the voice assistant the owner is talking to, "
    "while the original session (the desk session) keeps working. You are read-only: read files "
    "and look back through this conversation as you need, but change nothing; any tool that could "
    "change something is refused. You know this conversation up to the fork, plus what you read "
    "now. The owner sees neither the question nor your answer unless the relay passes them on: "
    "answer the relay in your reply text, self-contained, naming things rather than pointing "
    "back to earlier text.]"
)


async def refuse_changes(hook_input: Any, tool_use_id: str | None, context: Any) -> Any:
    """Before every tool the fork would run: let a reading tool through, refuse the rest."""
    tool = str((hook_input or {}).get("tool_name", ""))
    # RULE: the side session never runs a tool that can change anything
    if tool in READ_TOOLS:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"The side session is read-only, so {tool} is not run.",
        }
    }


async def _only_reading(
    tool: str, tool_input: dict[str, Any], context: ToolPermissionContext
) -> PermissionResultAllow | PermissionResultDeny:
    """Anything that would otherwise ask for permission: reading yes, the rest no, nobody asked."""
    if tool in READ_TOOLS:
        return PermissionResultAllow()
    return PermissionResultDeny(message="The side session is read-only.")


def fork_options(session_id: str, cwd: str) -> ClaudeAgentOptions:
    """Options for a fork: the owner's own settings, as the session itself has them."""
    return ClaudeAgentOptions(
        cwd=cwd,
        resume=session_id,
        fork_session=True,
        setting_sources=["user", "project", "local"],
        permission_mode="default",
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[refuse_changes])]},
        can_use_tool=_only_reading,
        settings=json.dumps({"disableClaudeAiConnectors": True}),
    )


async def ask(
    client_factory: Callable[[ClaudeAgentOptions], Any], session_id: str, cwd: str, question: str, wait_seconds: float
) -> dict[str, Any]:
    """Fork the session, ask it `question`, and return its answer and the tools it used."""
    texts: list[str] = []
    tools: Counter[str] = Counter()
    final: str | None = None

    async def read(client: Any) -> None:
        nonlocal final
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        texts.append(block.text.strip())
                    elif isinstance(block, ToolUseBlock):
                        tools[block.name] += 1
            elif isinstance(msg, ResultMessage) and msg.result:
                final = msg.result.strip()

    status = "answered"
    async with client_factory(fork_options(session_id, cwd)) as client:
        await client.query(f"{SIDE_SESSION_NOTE}\n\nThe relay asks: {question}")
        try:
            async with asyncio.timeout(wait_seconds):
                await read(client)
        except TimeoutError:
            status = "timed_out"
            with contextlib.suppress(Exception):
                await client.interrupt()
    answer = final or (texts[-1] if texts else "")
    return {"status": status, "answer": answer, "tools_used": dict(tools)}
