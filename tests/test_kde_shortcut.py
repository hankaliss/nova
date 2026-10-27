"""Tests for the kglobalaccel shortcut client (no D-Bus needed)."""

from types import SimpleNamespace

import pytest

from nova.kde.shortcut import (
    ACTION_ID,
    SET_FLAGS,
    GlobalShortcut,
    ShortcutTaken,
    format_shortcut,
    parse_shortcut,
)


# Codes as KDE itself stores them (read from a live kglobalaccel).
@pytest.mark.parametrize(
    "text, code",
    [
        ("Meta+W", 268435543),
        ("Alt+F4", 150994995),
        ("Meta+T", 268435540),
        ("Meta+PgUp", 285212694),
        ("Meta+Space", 0x10000020),
        ("Ctrl+Alt+N", 0x0C00004E),
        ("F12", 0x0100003B),
        ("Ctrl+/", 0x0400002F),
    ],
)
def test_parse_and_format_round_trip(text, code):
    assert parse_shortcut(text) == code
    assert format_shortcut(code) == text


@pytest.mark.parametrize(
    "text", ["", "N", "Shift+N", "Ctrl+", "Foo+N", "Ctrl+Ctrl+N", "Ctrl+Comma", "Ctrl+F25", "Ctrl+é"]
)
def test_parse_rejects(text):
    with pytest.raises(ValueError):
        parse_shortcut(text)


class FakeBus:
    """Records _call()s and answers them like kglobalaccel."""

    def __init__(self, owners=None):
        self.calls = []
        self.owners = owners or []

    async def __call__(self, member, signature, body, **kw):
        self.calls.append((member, signature, body))
        if member == "getGlobalShortcutsByKey":
            return [self.owners]
        if member == "setShortcutKeys":
            return [body[1]]  # kglobalaccel echoes the keys it set
        return []


@pytest.fixture
def shortcut(monkeypatch):
    gs = GlobalShortcut(lambda: None)
    bus = FakeBus()
    monkeypatch.setattr(gs, "_call", bus)
    return gs, bus


@pytest.mark.asyncio
async def test_set_keys_sends_exact_signature(shortcut):
    gs, bus = shortcut
    assert await gs.set_keys("Meta+Space") == "Meta+Space"
    member, signature, body = bus.calls[-1]
    assert (member, signature) == ("setShortcutKeys", "asa(ai)u")
    # a(ai): one key sequence (a struct) holding one key combination.
    assert body == [ACTION_ID, [[[0x10000020]]], SET_FLAGS]


@pytest.mark.asyncio
async def test_clear_sends_no_keys(shortcut):
    gs, bus = shortcut
    assert await gs.set_keys("") == ""
    assert bus.calls == [("setShortcutKeys", "asa(ai)u", [ACTION_ID, [], SET_FLAGS])]


@pytest.mark.asyncio
async def test_taken_key_is_refused(monkeypatch):
    gs = GlobalShortcut(lambda: None)
    overview = ("Overview", "Toggle Overview", "kwin", "KWin", "default", "Default Context", [1], [1])
    bus = FakeBus(owners=[overview])
    monkeypatch.setattr(gs, "_call", bus)
    with pytest.raises(ShortcutTaken, match="KWin: Toggle Overview"):
        await gs.set_keys("Meta+W")
    assert [c[0] for c in bus.calls] == ["getGlobalShortcutsByKey"]


@pytest.mark.asyncio
async def test_own_binding_is_not_a_conflict(monkeypatch):
    gs = GlobalShortcut(lambda: None)
    ours = ("toggle", "Show or hide Nova", "nova", "Nova", "default", "Default Context", [1], [1])
    monkeypatch.setattr(gs, "_call", FakeBus(owners=[ours]))
    assert await gs.set_keys("Meta+Space") == "Meta+Space"


@pytest.mark.asyncio
async def test_invalid_text_never_reaches_dbus(shortcut):
    gs, bus = shortcut
    with pytest.raises(ValueError):
        await gs.set_keys("N")
    assert bus.calls == []


def test_only_our_pressed_signal_toggles():
    pressed = []
    gs = GlobalShortcut(lambda: pressed.append(1))
    gs._component_path = "/component/nova"

    def msg(member="globalShortcutPressed", path="/component/nova", body=("nova", "toggle", 0)):
        return SimpleNamespace(member=member, path=path, body=list(body))

    gs._on_message(msg())
    gs._on_message(msg(path="/component/kwin", body=("kwin", "Overview", 0)))
    gs._on_message(msg(body=("nova", "other", 0)))
    gs._on_message(msg(member="globalShortcutReleased"))
    assert pressed == [1]
