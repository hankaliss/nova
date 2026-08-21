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
        assert tool.name.startswith(("linux.", "shell."))
        assert tool.description
        if tool.name == "shell.execute":
            # The only WRITE-risk tool: must keep its own permission key.
            assert tool.risk.value == "write"
            assert tool.permission == "shell.execute"
        else:
            assert tool.risk.value == "read"
            assert tool.permission == "system.info"