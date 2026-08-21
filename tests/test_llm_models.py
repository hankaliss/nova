"""Tests for LLM data models."""

from nova.llm.models import Message, ModelInfo, Role, StreamChunk


def test_message_creation():
    msg = Message(role=Role.USER, content="Hello")
    assert msg.role == Role.USER
    assert msg.content == "Hello"


def test_message_roles():
    assert Role.SYSTEM.value == "system"
    assert Role.USER.value == "user"
    assert Role.ASSISTANT.value == "assistant"


def test_model_info_defaults():
    info = ModelInfo(name="test-model")
    assert info.name == "test-model"
    assert info.size == 0
    assert info.parameter_size == ""
    assert info.quantization == ""
    assert info.context_length == 0
    assert info.capabilities == []


def test_model_info_full():
    info = ModelInfo(
        name="qwen3:8b",
        size=5225388164,
        parameter_size="8.2B",
        quantization="Q4_K_M",
        context_length=40960,
        capabilities=["completion", "tools", "thinking"],
    )
    assert info.parameter_size == "8.2B"
    assert len(info.capabilities) == 3


def test_stream_chunk_defaults():
    chunk = StreamChunk()
    assert chunk.token == ""
    assert chunk.done is False
    assert chunk.eval_count == 0


def test_stream_chunk_with_data():
    chunk = StreamChunk(
        token="hello",
        done=False,
        eval_count=10,
        eval_duration_ns=100000000,
    )
    assert chunk.token == "hello"
    assert chunk.eval_count == 10
