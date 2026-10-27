"""Global show/hide shortcut through KDE's kglobalaccel (AD-035).

kglobalaccel runs inside kwin_wayland, so a malformed call can take down the
compositor and the whole session (AD-009 did, with dbus-next). Every call
here uses the exact signature from the service's introspection data, and
key sequences are built only from validated text.

Protocol (org.kde.KGlobalAccel at /kglobalaccel):
  doRegister(as actionId)
  setShortcutKeys(as actionId, a(ai) keys, u flags) -> a(ai)
  setInactive(as actionId)
  getComponent(s componentUnique) -> o
  getGlobalShortcutsByKey(i key) -> a(ssssssaiai)   (owners of a key)
Each key sequence is a struct holding up to four key combinations; Nova
sends one combination: Qt key code | modifier bits.
"""

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

SERVICE = "org.kde.kglobalaccel"
PATH = "/kglobalaccel"
INTERFACE = "org.kde.KGlobalAccel"
COMPONENT_INTERFACE = "org.kde.kglobalaccel.Component"

COMPONENT = "nova"
ACTION = "toggle"
# [componentUnique, actionUnique, componentFriendly, actionFriendly]
ACTION_ID = [COMPONENT, ACTION, "Nova", "Show or hide Nova"]

# KGlobalAccel::SetShortcutFlag: SetPresent | NoAutoloading. Nova's setting is
# the source of truth, so kglobalaccel must not swap in a saved key.
SET_FLAGS = 0x2 | 0x4

# Qt::KeyboardModifier bits, in Qt's display order.
MODIFIERS = {
    "Meta": 0x10000000,
    "Ctrl": 0x04000000,
    "Alt": 0x08000000,
    "Shift": 0x02000000,
}
_SPECIAL_KEYS = {
    "Space": 0x20,
    "Escape": 0x01000000,
    "Tab": 0x01000001,
    "Backspace": 0x01000003,
    "Return": 0x01000004,
    "Insert": 0x01000006,
    "Delete": 0x01000007,
    "Pause": 0x01000008,
    "Print": 0x01000009,
    "Home": 0x01000010,
    "End": 0x01000011,
    "Left": 0x01000012,
    "Up": 0x01000013,
    "Right": 0x01000014,
    "Down": 0x01000015,
    "PgUp": 0x01000016,
    "PgDown": 0x01000017,
}
_PUNCTUATION = "`-=[];',./\\"
# Keys that may be bound without a modifier (they type nothing).
_STANDALONE = {"Pause", "Print"} | {f"F{n}" for n in range(1, 25)}


def _key_code(key: str) -> int:
    if len(key) == 1 and key.isascii() and (key.isalnum() or key in _PUNCTUATION):
        return ord(key.upper())
    if key in _SPECIAL_KEYS:
        return _SPECIAL_KEYS[key]
    if key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return 0x01000030 + int(key[1:]) - 1
    raise ValueError(f"unsupported key: {key!r}")


def parse_shortcut(text: str) -> int:
    """Turn "Meta+Space" / "Ctrl+Alt+N" into a Qt key code.

    Raises ValueError for anything that isn't one key plus modifiers, or a
    typing key without Meta/Ctrl/Alt (a global grab of plain "N" would
    swallow that letter everywhere).
    """
    parts = [p.strip() for p in text.split("+")]
    if not parts or any(p == "" for p in parts):
        raise ValueError(f"invalid shortcut: {text!r}")
    *mods, key = parts
    if len(set(mods)) != len(mods) or any(m not in MODIFIERS for m in mods):
        raise ValueError(f"invalid modifiers in {text!r}")
    code = _key_code(key)
    if key not in _STANDALONE and not set(mods) & {"Meta", "Ctrl", "Alt"}:
        raise ValueError(f"{text!r} needs Meta, Ctrl or Alt")
    for mod in mods:
        code |= MODIFIERS[mod]
    return code


class ShortcutTaken(Exception):
    """The key already belongs to another global shortcut."""


def format_shortcut(code: int) -> str:
    """Inverse of parse_shortcut (for what kglobalaccel reports back)."""
    mods = [name for name, bit in MODIFIERS.items() if code & bit]
    key_code = code & ~sum(MODIFIERS.values())
    names = {v: k for k, v in _SPECIAL_KEYS.items()}
    if key_code in names:
        key = names[key_code]
    elif 0x01000030 <= key_code <= 0x01000047:
        key = f"F{key_code - 0x01000030 + 1}"
    elif 0x20 < key_code < 0x7F:
        key = chr(key_code)
    else:
        key = hex(key_code)
    return "+".join([*mods, key])


class GlobalShortcut:
    """Nova's show/hide action in kglobalaccel, on the session bus."""

    def __init__(self, on_pressed: Callable[[], Any]) -> None:
        self._on_pressed = on_pressed
        self._bus: Any = None
        self._component_path: str | None = None

    async def _call(self, member: str, signature: str, body: list, *,
                    path: str = PATH, interface: str = INTERFACE) -> list:
        from dbus_fast import Message, MessageType

        reply = await self._bus.call(Message(
            destination=SERVICE, path=path, interface=interface,
            member=member, signature=signature, body=body,
        ))
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"{member}: {reply.error_name}: {reply.body}")
        return reply.body

    async def start(self) -> bool:
        """Connect and register the action. False when kglobalaccel is absent."""
        try:
            from dbus_fast import Message
            from dbus_fast.aio import MessageBus

            self._bus = await MessageBus().connect()
            await self._call("doRegister", "as", [ACTION_ID])
            [path] = await self._call("getComponent", "s", [COMPONENT])
            self._component_path = path
            rule = (
                f"type='signal',sender='{SERVICE}',path='{path}',"
                f"interface='{COMPONENT_INTERFACE}',member='globalShortcutPressed'"
            )
            await self._bus.call(Message(
                destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus", member="AddMatch",
                signature="s", body=[rule],
            ))
            self._bus.add_message_handler(self._on_message)
            return True
        except Exception as e:  # no session bus, not KDE, ...
            logger.info("global shortcut unavailable: %s", e)
            await self.stop()
            return False

    def _on_message(self, message: Any) -> None:
        if (
            message.member == "globalShortcutPressed"
            and message.path == self._component_path
            and list(message.body[:2]) == [COMPONENT, ACTION]
        ):
            self._on_pressed()

    async def owners(self, code: int) -> list[str]:
        """Other shortcuts on this key, as "Component: Action"."""
        [infos] = await self._call("getGlobalShortcutsByKey", "i", [code])
        return [
            f"{info[3]}: {info[1]}"
            for info in infos
            if (info[2], info[0]) != (COMPONENT, ACTION)
        ]

    async def set_keys(self, text: str) -> str:
        """Bind ``text`` ("" clears it). Returns the key KDE actually set.

        Raises ShortcutTaken when another shortcut owns the key (kglobalaccel
        itself doesn't refuse it).
        """
        keys = []
        if text:
            code = parse_shortcut(text)
            taken = await self.owners(code)
            if taken:
                raise ShortcutTaken(f"{text} is already used by {', '.join(taken)}")
            keys = [[[code]]]
        [result] = await self._call("setShortcutKeys", "asa(ai)u", [ACTION_ID, keys, SET_FLAGS])
        codes = [c for (seq,) in result for c in seq[:1] if c]
        return format_shortcut(codes[0]) if codes else ""

    async def invoke(self) -> None:
        """Trigger the action as if its key were pressed (tests)."""
        await self._call("invokeShortcut", "s", [ACTION],
                         path=self._component_path, interface=COMPONENT_INTERFACE)

    async def stop(self) -> None:
        """Mark the action inactive (Nova is quitting) and disconnect."""
        if self._bus is None:
            return
        try:
            if self._component_path is not None:
                await self._call("setInactive", "as", [ACTION_ID])
        except Exception as e:
            logger.info("global shortcut: setInactive failed: %s", e)
        self._bus.disconnect()
        self._bus = None
        self._component_path = None
