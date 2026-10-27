"""Unit tests for MCP auth-challenge parsing (no SDK or server needed)."""

from nova.mcp.auth import AuthChallenge, parse_auth_challenge

DEVICE_FLOW_TEXT = (
    "The error indicates that the GitHub MCP Server requires manual "
    "authorization. Follow these steps:\n\n"
    "Visit https://github.com/login/device in your browser\n"
    "Enter the code A0CF-0E52 to authorize the MCP Server\n"
    "After authorization, retry your request\n"
    "⚠️ Note: Your MCP client appears to lack secure URL handling "
    "capabilities. For better security, consider having your development "
    "environment (IDE, CLI, etc.) handle this authorization process instead."
)

OAUTH_FLOW_TEXT = (
    "Authorization required. Please open this URL to authorize the GitHub "
    "MCP Server: https://github.com/login/oauth/authorize?client_id=abc123"
    "&redirect_uri=http%3A%2F%2Flocalhost%3A8085%2Fcallback — after you "
    "approve, retry your request."
)


def test_device_flow_extracts_url_and_code():
    challenge = parse_auth_challenge(DEVICE_FLOW_TEXT)
    assert challenge == AuthChallenge(
        url="https://github.com/login/device?user_code=A0CF-0E52",
        code="A0CF-0E52",
    )


def test_oauth_flow_extracts_url_without_code():
    challenge = parse_auth_challenge(OAUTH_FLOW_TEXT)
    assert challenge is not None
    assert challenge.url.startswith("https://github.com/login/oauth/authorize")
    assert "client_id=abc123" in challenge.url
    assert challenge.code == ""


def test_verification_url_preferred_over_incidental_links():
    text = (
        "Docs live at https://example.com/docs but you must authorize: "
        "visit https://github.com/login/device and enter the code AAAA-BBBB."
    )
    challenge = parse_auth_challenge(text)
    assert challenge is not None
    assert challenge.url == "https://github.com/login/device?user_code=AAAA-BBBB"


def test_ordinary_error_returns_none():
    assert parse_auth_challenge("boom failed") is None


def test_url_without_auth_context_returns_none():
    text = "Request failed: 404 at https://api.github.com/repos/x/y — not found."
    assert parse_auth_challenge(text) is None


def test_auth_words_without_url_returns_none():
    text = "You must authorize the server before continuing (no link provided)."
    assert parse_auth_challenge(text) is None


def test_empty_text_returns_none():
    assert parse_auth_challenge("") is None
    assert parse_auth_challenge(None) is None


def test_markdown_link_and_trailing_punctuation_cleaned():
    text = (
        "Please [authorize here](https://github.com/login/device). Enter "
        "the code CDEF-2345 to continue."
    )
    challenge = parse_auth_challenge(text)
    assert challenge == AuthChallenge(
        url="https://github.com/login/device?user_code=CDEF-2345",
        code="CDEF-2345",
    )


def test_non_github_url_left_untouched():
    text = (
        "Authorization required: visit https://git.example.com/device and "
        "enter the code AAAA-BBBB."
    )
    challenge = parse_auth_challenge(text)
    assert challenge == AuthChallenge(url="https://git.example.com/device", code="AAAA-BBBB")
