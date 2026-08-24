"""Thread-safe application activity state shared by workstation adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class ApplicationState:
    """Represent application state state and behavior."""
    stage: str = "IMPORT"
    busy: bool = False
    message: str = "No case imported"
    observers: list[Callable[[], None]] = field(default_factory=list, repr=False)

    def update(self, *, stage: str | None = None, busy: bool | None = None, message: str | None = None) -> None:
        """Handle update for the enclosing ASCEND workflow."""
        if stage is not None:
            self.stage = stage
        if busy is not None:
            self.busy = busy
        if message is not None:
            self.message = message
        for callback in list(self.observers):
            callback()

