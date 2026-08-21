"""Tool definitions for Nova."""

from enum import Enum
from typing import Any, Callable, Awaitable

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class ToolResult(BaseModel):
    success: bool
    data: Any = None
    error: str | None = None


ToolImplementation = Callable[..., Any] | Callable[..., Awaitable[Any]]


class Tool(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel = RiskLevel.READ
    permission: str = ""
    implementation: ToolImplementation