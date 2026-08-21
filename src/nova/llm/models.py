"""Data models for LLM communication."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FunctionCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    function: FunctionCall


class Message(BaseModel):
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ModelInfo(BaseModel):
    name: str
    size: int = 0
    parameter_size: str = ""
    quantization: str = ""
    context_length: int = 0
    capabilities: list[str] = Field(default_factory=list)


class StreamChunk(BaseModel):
    token: str = ""
    done: bool = False
    tool_calls: list[ToolCall] = Field(default_factory=list)
    total_duration_ns: int = 0
    eval_count: int = 0
    eval_duration_ns: int = 0
    prompt_eval_count: int = 0
    prompt_eval_duration_ns: int = 0