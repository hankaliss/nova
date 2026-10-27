"""Text-mode tool calls stay out of the visible reply (AD-037)."""

import pytest

from nova.agent.agent import _CallHider

TOOLS = {"linux.cpu_info", "web.search"}


def run(text, step=1):
    """Feed text in small pieces; return (visible text, pieces seen early)."""
    hider = _CallHider(TOOLS)
    out = []
    for i in range(0, len(text), step):
        out.append(hider.feed(text[i:i + step]))
    out.append(hider.finish())
    return "".join(out), out


@pytest.mark.parametrize("step", [1, 3, 1000])
def test_fenced_call_is_hidden(step):
    text = '```json\n{"tool": "linux.cpu_info", "args": {}}\n```'
    assert run(text, step)[0] == ""


@pytest.mark.parametrize("step", [1, 1000])
def test_bare_call_is_hidden(step):
    assert run('{"name": "web.search", "arguments": {"query": "x"}}', step)[0] == ""


def test_prose_around_a_call_stays():
    text = 'Let me check.\n```json\n{"tool": "linux.cpu_info", "args": {}}\n```\nDone.'
    assert run(text)[0] == "Let me check.\n\nDone."


def test_indented_call_on_its_own_line_is_hidden():
    text = 'Checking:\n  {"tool": "linux.cpu_info", "args": {}}\n'
    assert run(text)[0] == "Checking:\n  \n"


def test_unknown_tool_name_is_shown():
    text = '```json\n{"name": "Bob", "age": 3}\n```'
    assert run(text)[0] == text


def test_code_fence_streams_before_it_closes():
    text = "Here:\n```python\nprint('hi')\nprint('there')\n```\n"
    visible, pieces = run(text)
    assert visible == text
    # Everything up to the first print line was out before the closing fence.
    through_hi = text.index("print('there')")
    assert "".join(pieces[:through_hi]) == text[:through_hi]


def test_inline_braces_stream_immediately():
    text = "Use {braces} inline and a dict like {a: 1} mid-sentence."
    visible, pieces = run(text)
    assert visible == text
    assert "".join(pieces[: text.index("{a") + 2]) == text[: text.index("{a") + 2]


def test_plain_text_is_not_delayed():
    text = "Just an answer with no code at all."
    _, pieces = run(text)
    assert all(p == c for p, c in zip(pieces, text))


def test_unclosed_fence_is_released_at_the_end():
    text = "```json\n{\"tool\": \"linux.cpu_info\""
    assert run(text)[0] == text


def test_two_calls_and_braces_in_strings():
    text = (
        '{"tool": "web.search", "args": {"query": "a } b { c"}}\n'
        '{"tool": "linux.cpu_info", "args": {}}\n'
        "Result follows."
    )
    assert run(text)[0] == "\n\nResult follows."
