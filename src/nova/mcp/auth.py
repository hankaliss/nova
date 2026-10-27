"""Detection of MCP server authentication challenges.

Some MCP servers (e.g. github-mcp-server without a PAT) are unauthenticated
until the user logs in through a browser. They report this by failing the
first tool call with a human-oriented message that contains a verification
URL and, for the device-code flow, a short user code.

Nova parses such messages out of failed tool results so the UI can present
a proper login dialog instead of letting the LLM paraphrase raw
instructions (and so the URL never needs to be copy-typed from chat).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Authorization-ish signals; "authoriz" covers authorize/authorization,
# authorized. Kept conservative to avoid false positives on ordinary errors.
_AUTH_HINTS = ("authoriz", "login/device", "oauth/authorize", "sign in")

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_CODE_RE = re.compile(r"\b([A-Z0-9]{4})-([A-Z0-9]{4})\b")


@dataclass(frozen=True)
class AuthChallenge:
    """A login request extracted from a failed MCP tool result."""

    url: str
    code: str = ""


def parse_auth_challenge(text: str | None) -> AuthChallenge | None:
    """Extract an auth challenge from an MCP error message, if present.

    Returns None unless the text both mentions authorization and contains
    at least one URL. Verification-style URLs (login/device,
    oauth/authorize) are preferred over incidental links.
    """
    if not text:
        return None
    lowered = text.lower()
    if not any(hint in lowered for hint in _AUTH_HINTS):
        return None

    urls = _URL_RE.findall(text)
    urls = [_clean_url(u) for u in urls]
    urls = [u for u in urls if u]
    if not urls:
        return None

    def rank(url: str) -> int:
        low = url.lower()
        if "login/device" in low:
            return 0
        if "oauth/authorize" in low or "/authorize" in low:
            return 1
        return 2

    code_match = _CODE_RE.search(text)
    url = min(urls, key=rank)
    code = "-".join(code_match.groups()) if code_match else ""
    return AuthChallenge(url=_complete_verification_url(url, code), code=code)


def _complete_verification_url(url: str, code: str) -> str:
    """Pre-fill the user code where the verification page supports it.

    GitHub's device-login page accepts ``?user_code=`` so the user only
    confirms instead of typing the code. Unknown servers are left alone —
    guessing parameter names would be unsafe.
    """
    if code and url.startswith("https://github.com/login/device"):
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}user_code={code}"
    return url


def _clean_url(url: str) -> str:
    """Strip markdown-link wrappers and trailing punctuation."""
    return url.rstrip(").,;:!?'\"").rstrip()


def is_verification_url(url: str) -> bool:
    """True for unmistakable login pages (device entry, OAuth authorize)."""
    low = (url or "").lower()
    return "login/device" in low or "/authorize" in low
