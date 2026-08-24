"""Server-executed tool for the insider SmolPaws shared memory store."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Literal

from filelock import Timeout
from pydantic import Field

from openhands.sdk import Action, Observation, ToolDefinition
from openhands.sdk.tool import ToolAnnotations, ToolExecutor, register_tool

from smolpaws_memory_store import MemoryConflictError, SmolPawsMemoryStore


MemoryOperation = Literal[
    "read_durable",
    "replace_durable",
    "append_today",
    "read_daily",
    "list_dailies",
]


class SmolPawsMemoryAction(Action):
    operation: MemoryOperation = Field(description="Bounded memory operation to run.")
    content: str | None = Field(
        default=None,
        description="Memory text for replace_durable or append_today.",
    )
    expected_revision: str | None = Field(
        default=None,
        description="Revision returned by read_durable; required for replacement.",
    )
    date: str | None = Field(
        default=None,
        description="Strict YYYY-MM-DD date for read_daily.",
    )


class SmolPawsMemoryObservation(Observation):
    """JSON result from a shared-memory operation."""


class SmolPawsMemoryExecutor(
    ToolExecutor[SmolPawsMemoryAction, SmolPawsMemoryObservation]
):
    def __init__(self, store: SmolPawsMemoryStore | None = None):
        self.store = store if store is not None else SmolPawsMemoryStore()

    def __call__(
        self,
        action: SmolPawsMemoryAction,
        conversation=None,
    ) -> SmolPawsMemoryObservation:
        state = getattr(conversation, "state", None)
        tags = getattr(state, "tags", {})
        if not isinstance(tags, Mapping) or tags.get("smolpaws") != "insider":
            return self._error(
                "forbidden",
                "Shared memory is available only to insider SmolPaws conversations.",
            )
        try:
            payload = self._execute(action)
        except MemoryConflictError as error:
            return self._error(
                "revision_conflict",
                "Durable memory changed; read it again before replacing it.",
                current_revision=error.current_revision,
            )
        except ValueError as error:
            return self._error("invalid_request", str(error))
        except Timeout:
            return self._error("store_busy", "The memory store is busy; retry shortly.")
        except OSError:
            return self._error("storage_error", "The memory store is unavailable.")
        return SmolPawsMemoryObservation.from_text(json.dumps(payload, sort_keys=True))

    def _execute(self, action: SmolPawsMemoryAction) -> dict[str, object]:
        operation = action.operation
        if operation == "read_durable":
            self._require_only(action)
            return {"status": "ok", **asdict(self.store.read_durable())}
        if operation == "replace_durable":
            self._require_only(action, "content", "expected_revision")
            if action.content is None or action.expected_revision is None:
                raise ValueError(
                    "replace_durable requires content and expected_revision"
                )
            updated = self.store.replace_durable(
                action.content,
                expected_revision=action.expected_revision,
            )
            return {"status": "updated", "revision": updated.revision}
        if operation == "append_today":
            self._require_only(action, "content")
            if action.content is None:
                raise ValueError("append_today requires content")
            appended = self.store.append_today(action.content)
            return {
                "status": "appended",
                "date": appended.date,
                "revision": appended.revision,
            }
        if operation == "read_daily":
            self._require_only(action, "date")
            if action.date is None:
                raise ValueError("read_daily requires date")
            document = self.store.read_daily(action.date)
            return (
                {"status": "not_found", "date": action.date}
                if document is None
                else {"status": "ok", **asdict(document)}
            )
        if operation == "list_dailies":
            self._require_only(action)
            return {
                "status": "ok",
                "dailies": [asdict(item) for item in self.store.list_dailies()],
            }
        raise ValueError("unsupported memory operation")

    @staticmethod
    def _require_only(action: SmolPawsMemoryAction, *allowed: str) -> None:
        values = {
            "content": action.content,
            "expected_revision": action.expected_revision,
            "date": action.date,
        }
        unexpected = [
            name
            for name, value in values.items()
            if value is not None and name not in allowed
        ]
        if unexpected:
            raise ValueError(
                f"{action.operation} does not accept: {', '.join(sorted(unexpected))}"
            )

    @staticmethod
    def _error(code: str, message: str, **details: object) -> SmolPawsMemoryObservation:
        return SmolPawsMemoryObservation.from_text(
            json.dumps({"error": code, "message": message, **details}, sort_keys=True),
            is_error=True,
        )


_DESCRIPTION = """Read and maintain the memory shared by all insider SmolPaws conversations.

The store is private to the local OpenHands installation at
`~/.openhands/smolpaws/memory/` (or the configured OpenHands persistence root).
It is separate from generic OpenHands memory and from other SmolPaws faces.

Read at use:
* At the start of substantive work, call operation="read_durable". Do not rely
  on a copy from an earlier turn; another insider conversation may have updated it.
* Daily notes are not loaded automatically. Use operation="list_dailies" and
  operation="read_daily" only when durable memory points to relevant detail.

Write safely:
* Use operation="append_today" for fresh observations and task-local detail.
* Promote only stable, broadly useful facts into MEMORY.md. First read_durable,
  then replace_durable with the returned expected_revision. On a
  revision_conflict, read again, merge deliberately, and retry.
* Never store secrets, credentials, raw logs, or facts that are cheap to rediscover.

The tool accepts no filesystem paths. Dates are strict YYYY-MM-DD values, and
all writes are size-bounded and serialized across conversations."""


class SmolPawsMemoryTool(
    ToolDefinition[SmolPawsMemoryAction, SmolPawsMemoryObservation]
):
    @classmethod
    def create(
        cls,
        conv_state=None,  # noqa: ARG003
        **params,  # noqa: ARG003
    ) -> Sequence["SmolPawsMemoryTool"]:
        return [
            cls(
                description=_DESCRIPTION,
                action_type=SmolPawsMemoryAction,
                observation_type=SmolPawsMemoryObservation,
                executor=SmolPawsMemoryExecutor(),
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]


register_tool("smolpaws_memory", SmolPawsMemoryTool)
