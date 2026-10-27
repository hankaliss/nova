"""Tests for web tools (mocked httpx)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova.tools import web
from nova.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_search_success():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "results": [
            {
                "title": "CachyOS",
                "url": "https://cachyos.org",
                "content": "A fast Linux distro.",
            },
            {"title": "Second", "url": "https://example.org", "content": "Snippet"},
        ]
    }

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(web.httpx, "AsyncClient", return_value=mock_client):
        result = await web._search("cachyos")

    assert result["query"] == "cachyos"
    assert len(result["results"]) == 2
    assert result["results"][0]["url"] == "https://cachyos.org"


@pytest.mark.asyncio
async def test_search_limits_results():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "results": [
            {"title": f"R{i}", "url": f"https://x{i}.org", "content": ""}
            for i in range(10)
        ]
    }

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(web.httpx, "AsyncClient", return_value=mock_client):
        result = await web._search("query", num_results=3)

    assert len(result["results"]) == 3


@pytest.mark.asyncio
async def test_search_connection_error():
    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=web.httpx.ConnectError("refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(web.httpx, "AsyncClient", return_value=mock_client):
        with pytest.raises(web.WebToolError, match="SearXNG not reachable"):
            await web._search("query")


@pytest.mark.asyncio
async def test_fetch_success():
    mock_resp = MagicMock()
    mock_resp.text = "<html><body><h1>Hello</h1><p>World</p></body></html>"

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(web.httpx, "AsyncClient", return_value=mock_client):
        result = await web._fetch("https://example.org")

    assert result["url"] == "https://example.org"
    assert "Hello" in result["content"]
    assert "<h1>" not in result["content"]


@pytest.mark.asyncio
async def test_fetch_strips_scripts():
    mock_resp = MagicMock()
    mock_resp.text = (
        "<script>alert('x')</script><style>body{}</style><p>Content here</p>"
    )

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(web.httpx, "AsyncClient", return_value=mock_client):
        result = await web._fetch("https://example.org")

    assert "alert" not in result["content"]
    assert "Content here" in result["content"]


@pytest.mark.asyncio
async def test_fetch_truncates():
    mock_resp = MagicMock()
    mock_resp.text = "<p>" + "x" * 5000 + "</p>"

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(web.httpx, "AsyncClient", return_value=mock_client):
        result = await web._fetch("https://example.org", max_chars=100)

    assert len(result["content"]) <= 100
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_fetch_rejects_non_http():
    with pytest.raises(web.WebToolError, match="Refusing"):
        await web._fetch("file:///etc/passwd")


@pytest.mark.asyncio
async def test_web_tools_registered():
    registry = ToolRegistry()
    registry.register_web_tools()
    names = [t.name for t in registry.list_tools()]
    assert "web.search" in names
    assert "web.fetch" in names
    assert all(t.risk.value == "read" for t in registry.list_tools())


@pytest.mark.asyncio
async def test_web_search_tool_executes_via_registry():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"results": []}
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    registry = ToolRegistry()
    registry.register_web_tools()

    with patch.object(web.httpx, "AsyncClient", return_value=mock_client):
        result = await registry.execute("web.search", {"query": "test"})

    assert result.success is True
    assert result.data["results"] == []