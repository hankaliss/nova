"""Tests for Linux system tools."""

from nova.tools import linux


def test_cpu_info():
    info = linux._cpu_info()
    assert "model" in info
    assert "cores" in info
    assert info["cores"] > 0


def test_memory_info():
    info = linux._memory_info()
    assert "total_bytes" in info
    assert info["total_bytes"] > 0
    assert "available_bytes" in info


def test_disk_usage():
    usage = linux._disk_usage()
    assert usage["total_bytes"] > 0
    assert usage["free_bytes"] > 0


def test_uptime():
    uptime = linux._uptime()
    assert "seconds" in uptime
    assert uptime["seconds"] > 0


def test_gpu_info_or_error():
    info = linux._gpu_info()
    if "error" in info:
        assert info["error"]
    else:
        assert "name" in info
        assert "vram_total_mib" in info


def test_tools_have_required_fields():
    for tool in linux.get_tools():
        assert tool.name.startswith("linux.")
        assert tool.description
        # All linux tools are READ-only; shell execution lives in
        # nova.tools.shell (persistent session) and is registered separately.
        assert tool.risk.value == "read"
        assert tool.permission == "system.info"

def test_processes(monkeypatch):
    from types import SimpleNamespace

    out = "  101 a  31.6  4.9 808804 vesktop\n  987 a   8.9  2.2 371084 kwin_wayland\n"
    seen = []
    monkeypatch.setattr(linux.subprocess, "run",
                        lambda cmd, **kw: seen.append(cmd) or SimpleNamespace(stdout=out))
    result = linux._processes("memory", 1)
    assert seen[0][-1] == "--sort=-rss"
    assert result["processes"] == [{"pid": 101, "user": "a", "cpu_pct": 31.6, "mem_pct": 4.9,
                                    "rss_mb": 789.8, "name": "vesktop"}]


def test_processes_rejects_bad_sort():
    import pytest

    with pytest.raises(ValueError):
        linux._processes("io")


def test_temperatures(monkeypatch, tmp_path):
    dev = tmp_path / "hwmon3"
    dev.mkdir()
    (dev / "name").write_text("coretemp\n")
    (dev / "temp1_input").write_text("45000\n")
    (dev / "temp1_label").write_text("Package id 0\n")
    (dev / "temp2_input").write_text("43500\n")
    monkeypatch.setattr(linux, "HWMON", tmp_path)
    assert linux._temperatures() == {"sensors": [
        {"chip": "coretemp", "label": "Package id 0", "celsius": 45.0},
        {"chip": "coretemp", "label": "temp2", "celsius": 43.5},
    ]}


def test_temperatures_none(monkeypatch, tmp_path):
    monkeypatch.setattr(linux, "HWMON", tmp_path)
    assert "error" in linux._temperatures()
