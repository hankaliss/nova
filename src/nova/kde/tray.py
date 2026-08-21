"""System tray integration for Nova."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


def make_icon(color: str = "#89b4fa") -> QIcon:
    """Draw a simple rounded-square 'N' icon (no theme icon needed)."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
    painter.setPen(QColor("#11111b"))
    font = QFont()
    font.setPixelSize(34)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "N")
    painter.end()
    return QIcon(pixmap)


def toggle_window(window) -> None:
    """Show/hide a QQuickWindow, bringing it forward when shown."""
    if window.isVisible():
        window.hide()
    else:
        window.show()
        window.raise_()
        window.requestActivate()


class TrayIcon(QSystemTrayIcon):
    """Tray icon that toggles the main window."""

    def __init__(self, window, parent=None, quit_cb=None) -> None:
        super().__init__(make_icon(), parent)
        self._window = window
        self.setToolTip("Nova")

        menu = QMenu()
        menu.addAction("Show / Hide", self._toggle)
        quit_action = menu.addAction("Quit")
        if quit_cb is not None:
            quit_action.triggered.connect(quit_cb)
        self.setContextMenu(menu)

        self.activated.connect(self._on_activated)

    def _toggle(self) -> None:
        toggle_window(self._window)

    def _on_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._toggle()