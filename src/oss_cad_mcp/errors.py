"""Domain errors for the OSS CAD MCP server."""

from __future__ import annotations

from typing import Any

from oss_cad_mcp.models.common import ErrorInfo


class CadError(Exception):
    """Base class for errors that should be reported to the agent."""

    code = "cad_error"

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details
        self.retryable = retryable

    def to_info(self) -> ErrorInfo:
        return ErrorInfo(
            code=self.code,
            message=self.message,
            hint=self.hint,
            details=self.details,
            retryable=self.retryable,
        )


class SuiteNotFound(CadError):
    code = "suite_not_found"


class ToolNotFound(CadError):
    code = "tool_not_found"


class PathNotAllowed(CadError):
    code = "path_not_allowed"


class PreflightFailed(CadError):
    code = "preflight_failed"


class ToolFailed(CadError):
    code = "tool_failed"


class ToolTimeout(CadError):
    code = "tool_timeout"


class JobCancelled(CadError):
    code = "job_cancelled"


class ParseError(CadError):
    code = "parse_error"


class InvalidInput(CadError):
    code = "invalid_input"


class HardwareUnavailable(CadError):
    code = "hardware_unavailable"
