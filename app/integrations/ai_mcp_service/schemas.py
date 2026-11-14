"""Wire-level shapes received from the AI/MCP service."""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict

DEFAULT_EVENT_TYPE = "message"
# Upstream termination sentinel: a `data: [DONE]` event ends the stream and is not yielded.
DONE_SENTINEL = "[DONE]"


class ServerSentEvent(BaseModel):
    """One raw upstream SSE event. Its `data` is untrusted until the calling module validates it."""

    model_config = ConfigDict(frozen=True)

    event: str = DEFAULT_EVENT_TYPE
    data: str = ""
    id: str | None = None
    retry: int | None = None

    def json_data(self) -> Any:
        """Decode `data` as JSON. Raises ValueError if it is not valid JSON."""
        return json.loads(self.data)
