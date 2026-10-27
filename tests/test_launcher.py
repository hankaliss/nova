"""Tests for the `nova` launcher's Electron command line."""

from pathlib import Path

from nova.__main__ import _electron_command


def test_electron_runs_through_xwayland_by_default(monkeypatch):
    monkeypatch.delenv("NOVA_OZONE_PLATFORM", raising=False)
    cmd = _electron_command(Path("/e/electron"), Path("/e"))
    assert cmd == ["/e/electron", "/e", "--ozone-platform=x11"]


def test_ozone_platform_override(monkeypatch):
    monkeypatch.setenv("NOVA_OZONE_PLATFORM", "wayland")
    assert _electron_command(Path("/e/electron"), Path("/e"))[-1] == "--ozone-platform=wayland"


def test_installed_launch_uses_system_electron(monkeypatch, tmp_path):
    import nova.__main__ as launcher

    calls = []
    monkeypatch.setenv("NOVA_APP_DIR", str(tmp_path))
    monkeypatch.delenv("NOVA_OZONE_PLATFORM", raising=False)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(launcher, "_run", lambda cmd, cwd, env: calls.append((cmd, cwd)) or 0)
    assert launcher._launch_installed() == 0
    assert calls == [(["/usr/bin/electron44", str(tmp_path), "--ozone-platform=x11"], tmp_path)]


def test_installed_launch_without_electron(monkeypatch):
    import nova.__main__ as launcher

    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    assert launcher._launch_installed() == 1


def test_resources_dir(monkeypatch, tmp_path):
    from nova.rpc import paths

    monkeypatch.setenv("NOVA_RESOURCES_DIR", str(tmp_path))
    assert paths.resources_dir() == tmp_path
    monkeypatch.delenv("NOVA_RESOURCES_DIR")
    assert paths.resources_dir() == paths.REPO_ROOT  # running from the checkout
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path / "site-packages")
    assert paths.resources_dir() == paths.INSTALLED_RESOURCES


def test_toggle_is_passed_to_electron(monkeypatch):
    from nova.__main__ import _nova_args

    monkeypatch.delenv("NOVA_OZONE_PLATFORM", raising=False)
    extra = _nova_args(["--toggle", "--unknown"])
    assert extra == ["--toggle"]
    cmd = _electron_command(Path("/e/electron"), Path("/e"), extra)
    assert cmd == ["/e/electron", "/e", "--ozone-platform=x11", "--toggle"]


def test_installed_launch_passes_toggle(monkeypatch, tmp_path):
    import nova.__main__ as launcher

    calls = []
    monkeypatch.setenv("NOVA_APP_DIR", str(tmp_path))
    monkeypatch.setattr(launcher.sys, "argv", ["nova", "--toggle"])
    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(launcher, "_run", lambda cmd, cwd, env: calls.append(cmd) or 0)
    launcher._launch_installed()
    assert calls[0][-1] == "--toggle"
