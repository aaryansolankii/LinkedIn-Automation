"""Error type for failures Claude should see and explain to the user."""

from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError


class LinkedInError(ToolError):
    """An expected failure whose message is safe and useful to show to Claude.

    The MCP server hides the text of any other exception from the model, so every
    failure the user can act on (not connected, token expired, post not found...)
    is raised as this type.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
