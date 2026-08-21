"""Tests for KDE integration (tray)."""

from nova.kde.tray import toggle_window


class FakeWindow:
    def __init__(self):
        self.visible = True
        self.raised = False
        self.activated = False

    def isVisible(self):
        return self.visible

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def raise_(self):
        self.raised = True

    def requestActivate(self):
        self.activated = True


def test_toggle_window_hides_visible_window():
    window = FakeWindow()
    toggle_window(window)
    assert window.visible is False


def test_toggle_window_shows_and_raises_hidden_window():
    window = FakeWindow()
    window.hide()
    toggle_window(window)
    assert window.visible is True
    assert window.raised is True
    assert window.activated is True