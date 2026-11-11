"""What the bridge's tools share about where sessions may run and how they start.

The sessions themselves are ordinary background Claude Code sessions; this keeps
the project root, the folders opened to the bridge, the permission mode new
sessions get, and the SDK client the read-only side session runs on.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from .access import Access
from .store import Store

ClientFactory = Callable[[ClaudeAgentOptions], Any]


@dataclass(frozen=True)
class Grant:
    """The permission mode a conversation the bridge runs gets, and the folders added to it."""

    mode: str
    dirs: tuple[str, ...] = ()


class SessionManager:
    def __init__(
        self,
        store: Store,
        project_root: str | Path,
        client_factory: ClientFactory = ClaudeSDKClient,
        clock: Callable[[], float] = time.time,
        denied: Iterable[Path] = (),
        exempt: Iterable[Path] = (),
        new_mode: str = "default",
        grant_for: Callable[[str | None, str, str], Grant] | None = None,
    ) -> None:
        self.store = store
        self.root = Path(project_root).expanduser().resolve()
        # The bridge itself passes access.default_denied(); tests choose their own.
        self.access = Access(store, self.root, denied, exempt)
        self.client_factory = client_factory
        self.clock = clock
        self.new_mode = new_mode
        # The bridge itself passes session_modes.session_grant; left out, a resumed
        # conversation gets the mode for new sessions and no added folders.
        self.grant_for = grant_for or (lambda session_id, cwd, new_mode: Grant(new_mode))

    def resolve_project(self, project: str) -> Path:
        path = Path(project).expanduser()
        if not path.is_absolute():
            path = self.root / path
        path = path.resolve()
        # RULE: a project outside the root is refused
        if self.access.state(path) != "open":
            raise ValueError(f"project must be under {self.root} or a folder opened with request_access")
        if not path.is_dir():
            raise ValueError(f"not a directory: {path}")
        return path
