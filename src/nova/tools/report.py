"""Rendered artifacts for tools and skills (AD-039).

A tool can return a finished report next to its data:

    return with_artifact({"failed_units": 2, ...}, report)

The Agent shows the report in the artifact panel as soon as the tool
finishes and gives the model only the data, so the model writes a short
interpretation instead of typing HTML. The report is saved with the reply
(marked with ``ARTIFACT_MARKER``) and stripped from the model's context when
history is reloaded.

``Report`` renders one consistent design (Nova's night-sky look, with a light
variant): static HTML + CSS, no scripts, no external resources.
"""

import html as _html
import re
import time
from dataclasses import dataclass, field
from typing import Any

ARTIFACT_KEY = "artifact"
ARTIFACT_MARKER = "<!-- nova:artifact -->"
MAX_ARTIFACT_CHARS = 400_000

# A saved reply's tool artifact: the fenced block the Agent emitted.
_ARTIFACT_BLOCK = re.compile(r"\n*```html\n" + re.escape(ARTIFACT_MARKER) + r"[\s\S]*?\n```\n*")

TONES = ("ok", "warn", "danger", "info", "accent", "muted")


def esc(value: Any) -> str:
    """HTML-escape, and keep backticks out (the report travels in a ``` fence)."""
    return _html.escape(str(value), quote=True).replace("`", "&#96;")


def short_path(path: str, limit: int = 42) -> str:
    """~ for home, then a middle ellipsis: keeps both ends readable."""
    import os

    home = os.path.expanduser("~")
    if path == home or path.startswith(home + "/"):
        path = "~" + path[len(home):]
    if len(path) <= limit:
        return path
    keep = (limit - 1) // 2
    return path[:keep] + "…" + path[-(limit - 1 - keep):]


def plural(n: int, word: str, words: str = "") -> str:
    return f"{n:,} {word if n == 1 else (words or word + 's')}"


def with_artifact(data: dict[str, Any], report: "Report") -> dict[str, Any]:
    """Tool result carrying ``report`` as its artifact."""
    return {**data, ARTIFACT_KEY: {"title": report.title, "html": report.html()}}


def artifact_block(title: str, html: str) -> str:
    """The fenced block the Agent puts into the reply for one artifact."""
    body = html.replace("```", "&#96;&#96;&#96;")[:MAX_ARTIFACT_CHARS]
    return f"\n\n```html\n{ARTIFACT_MARKER}\n{body}\n```\n\n"


def strip_artifacts(text: str) -> str:
    """A saved reply without its tool artifacts (for the model's context)."""
    return _ARTIFACT_BLOCK.sub("\n\n", text).strip()


@dataclass
class Stat:
    label: str
    value: Any
    tone: str = "muted"
    hint: str = ""


@dataclass
class Bar:
    label: str
    value: float
    max: float
    note: str = ""
    tone: str = "accent"


@dataclass
class Finding:
    title: str
    detail: str = ""
    tone: str = "warn"
    tag: str = ""
    meta: str = ""


@dataclass
class Report:
    """A report artifact, built section by section."""

    title: str
    subtitle: str = ""
    kicker: str = "Nova"
    _parts: list[str] = field(default_factory=list)

    def stats(self, stats: list[Stat]) -> "Report":
        cards = "".join(
            f'<div class="stat {_tone(s.tone)}"><div class="stat-label">{esc(s.label)}</div>'
            f'<div class="stat-value">{esc(s.value)}</div>'
            + (f'<div class="stat-hint">{esc(s.hint)}</div>' if s.hint else "")
            + "</div>"
            for s in stats
        )
        self._parts.append(f'<div class="stats">{cards}</div>')
        return self

    def section(self, title: str, note: str = "") -> "Report":
        extra = f'<span class="section-note">{esc(note)}</span>' if note else ""
        self._parts.append(f'<h2 class="section">{esc(title)}{extra}</h2>')
        return self

    def bars(self, bars: list[Bar]) -> "Report":
        rows = []
        for b in bars:
            pct = 0 if b.max <= 0 else max(2.0, min(100.0, 100.0 * b.value / b.max))
            rows.append(
                f'<div class="bar-row"><div class="bar-label" title="{esc(b.label)}">{esc(b.label)}</div>'
                f'<div class="bar-track"><div class="bar-fill {_tone(b.tone)}" style="width:{pct:.1f}%"></div></div>'
                f'<div class="bar-note">{esc(b.note)}</div></div>'
            )
        self._parts.append(f'<div class="bars">{"".join(rows)}</div>')
        return self

    def table(self, columns: list[str], rows: list[list[Any]], tones: list[str] | None = None,
              mono: set[int] | None = None) -> "Report":
        mono = mono or set()
        head = "".join(f"<th>{esc(c)}</th>" for c in columns)
        body = []
        for i, row in enumerate(rows):
            tone = (tones or [])[i] if tones and i < len(tones) else ""
            cells = []
            for j, cell in enumerate(row):
                cls = ' class="mono"' if j in mono else ""
                if j == 0 and tone:
                    cells.append(f'<td{cls}><span class="dot {_tone(tone)}"></span>{esc(cell)}</td>')
                else:
                    cells.append(f"<td{cls}>{esc(cell)}</td>")
            body.append(f"<tr>{''.join(cells)}</tr>")
        self._parts.append(
            f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>'
        )
        return self

    def findings(self, findings: list[Finding]) -> "Report":
        items = []
        for f in findings:
            tag = f'<span class="chip {_tone(f.tone)}">{esc(f.tag)}</span>' if f.tag else ""
            meta = f'<span class="finding-meta">{esc(f.meta)}</span>' if f.meta else ""
            detail = f'<pre class="finding-detail">{esc(f.detail)}</pre>' if f.detail else ""
            items.append(
                f'<div class="finding {_tone(f.tone)}"><div class="finding-head">{tag}'
                f'<span class="finding-title">{esc(f.title)}</span>{meta}</div>{detail}</div>'
            )
        self._parts.append(f'<div class="findings">{"".join(items)}</div>')
        return self

    def callout(self, text: str, tone: str = "ok", title: str = "") -> "Report":
        head = f"<strong>{esc(title)}</strong> " if title else ""
        self._parts.append(f'<div class="callout {_tone(tone)}">{head}{esc(text)}</div>')
        return self

    def chips(self, chips: list[tuple[str, str]]) -> "Report":
        self._parts.append(
            '<div class="chips">'
            + "".join(f'<span class="chip {_tone(tone)}">{esc(text)}</span>' for text, tone in chips)
            + "</div>"
        )
        return self

    def html(self) -> str:
        stamp = time.strftime("%Y-%m-%d %H:%M")
        subtitle = f'<p class="subtitle">{esc(self.subtitle)}</p>' if self.subtitle else ""
        return (
            "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<title>{esc(self.title)}</title><style>{_CSS}</style></head><body><main>"
            f'<header><div class="kicker">{esc(self.kicker)}</div>'
            f"<h1>{esc(self.title)}</h1>{subtitle}</header>"
            + "".join(self._parts)
            + f'<footer>Generated by Nova · {esc(stamp)}</footer></main></body></html>'
        )


def _tone(tone: str) -> str:
    return f"t-{tone if tone in TONES else 'muted'}"


_CSS = """
:root{color-scheme:dark;--bg:#14131b;--surface:#1c1a25;--surface2:#24212f;--border:#34303f;
--text:#eeeaf6;--sub:#b7b1c8;--meta:#9a93ae;--accent:#f7b267;--ok:#7fdbb0;--warn:#e8d06b;
--danger:#ff8a85;--info:#c3b5ff;--muted:#7d7790;
--glow:radial-gradient(120% 80% at 100% 0%,rgba(195,181,255,.10),transparent 60%),
radial-gradient(90% 60% at 0% 0%,rgba(247,178,103,.08),transparent 55%)}
@media (prefers-color-scheme:light){:root{color-scheme:light;--bg:#f6f4fb;--surface:#ffffff;
--surface2:#f0edf7;--border:#ddd8ea;--text:#1d1a26;--sub:#4d4760;--meta:#6b6580;--accent:#b8661a;
--ok:#1f8a5b;--warn:#8a6d00;--danger:#c0392b;--info:#5b47c4;--muted:#8a8499;
--glow:radial-gradient(120% 80% at 100% 0%,rgba(91,71,196,.07),transparent 60%)}}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--text)}
body{font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans",sans-serif;
background-image:var(--glow);background-attachment:fixed;min-height:100vh}
main{max-width:860px;margin:0 auto;padding:28px 22px 22px}
header{margin-bottom:22px}
.kicker{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:600}
h1{margin:4px 0 2px;font-size:26px;line-height:1.2;letter-spacing:-.01em}
.subtitle{margin:0;color:var(--sub)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:8px}
.stat{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:12px 14px;
position:relative;overflow:hidden}
.stat::before{content:"";position:absolute;inset:0 auto 0 0;width:3px;background:var(--c,var(--muted))}
.stat-label{font-size:12px;color:var(--meta)}
.stat-value{font-size:24px;font-weight:650;margin-top:2px;font-variant-numeric:tabular-nums;color:var(--c,var(--text))}
.stat-hint{font-size:12px;color:var(--sub);margin-top:2px}
.t-ok{--c:var(--ok)}.t-warn{--c:var(--warn)}.t-danger{--c:var(--danger)}.t-info{--c:var(--info)}
.t-accent{--c:var(--accent)}.t-muted{--c:var(--muted)}
.stat.t-muted .stat-value{color:var(--text)}
h2.section{font-size:13px;font-weight:650;text-transform:uppercase;letter-spacing:.08em;color:var(--sub);
margin:26px 0 10px;display:flex;align-items:baseline;gap:10px}
.section-note{font-size:12px;font-weight:400;text-transform:none;letter-spacing:0;color:var(--meta)}
.bars{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:10px 14px}
.bar-row{display:grid;grid-template-columns:minmax(90px,30%) 1fr auto;gap:12px;align-items:center;padding:5px 0}
.bar-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:ui-monospace,"JetBrains Mono",monospace;font-size:12.5px}
.bar-track{height:8px;border-radius:99px;background:var(--surface2);overflow:hidden}
.bar-fill{height:100%;border-radius:99px;background:var(--c)}
.bar-note{font-size:12px;color:var(--meta);font-variant-numeric:tabular-nums;white-space:nowrap}
.table-wrap{background:var(--surface);border:1px solid var(--border);border-radius:14px;overflow:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-weight:600;font-size:12px;color:var(--meta);padding:9px 12px;border-bottom:1px solid var(--border);
background:var(--surface2)}
td{padding:8px 12px;border-bottom:1px solid var(--border);vertical-align:top}
tr:last-child td{border-bottom:0}
td.mono{font-family:ui-monospace,"JetBrains Mono",monospace;font-size:12.5px}
td:first-child{white-space:nowrap}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--c);margin-right:8px;vertical-align:1px}
.findings{display:flex;flex-direction:column;gap:8px}
.finding{background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--c);border-radius:12px;padding:10px 12px}
.finding-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.finding-title{font-weight:600}
.finding-meta{margin-left:auto;font-size:12px;color:var(--meta)}
.finding-detail{margin:8px 0 0;padding:8px 10px;background:var(--surface2);border-radius:8px;
font:12px/1.45 ui-monospace,"JetBrains Mono",monospace;color:var(--sub);white-space:pre-wrap;word-break:break-word;max-height:9.5em;overflow:auto}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{display:inline-block;font-size:11.5px;font-weight:600;padding:2px 8px;border-radius:99px;
color:var(--c);background:color-mix(in srgb,var(--c) 14%,transparent);border:1px solid color-mix(in srgb,var(--c) 35%,transparent)}
.callout{border-radius:14px;padding:12px 14px;border:1px solid color-mix(in srgb,var(--c) 40%,transparent);
background:color-mix(in srgb,var(--c) 10%,var(--surface))}
.callout strong{color:var(--c)}
footer{margin-top:28px;font-size:11.5px;color:var(--meta);text-align:right}
"""
