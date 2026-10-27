"""Git companion: repository status and a scan of all repos, as reports (issue #13).

READ only: every git call is a query. Aggregation happens in code; the model
gets a compact summary and the user a rendered report (AD-039).

Each report has a local part (git) and a GitHub part: sync with the remote
branch, pull requests, CI and issues, read through the GitHub MCP server's
skill-only tools (AD-042). Without that server the local part still renders,
with a note saying why the remote part is missing.
"""

import asyncio
import difflib
import html
import json
import os
import re
import subprocess
from pathlib import Path

from nova.skills import host
from nova.tools.report import Finding, Report, Stat, plural, short_path, with_artifact
from nova.tools.tool import RiskLevel, Tool

MAX_DIFF_CHARS = 6000
MAX_REPOS = 60
SCAN_DEPTH = 3
DEFAULT_ROOT = "~/Projects"
_SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".cache", "target", "dist", "build"}
_SEP = "\x1f"
MAX_REMOTE_REPOS = 12  # scan: repos (most active first) that get a GitHub column
_GITHUB_URL = re.compile(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")
_REMOTE_TOOLS = ("list_commits", "list_pull_requests", "list_issues", "actions_list")
_CI_TONE = {"success": "ok", "failure": "danger", "cancelled": "muted", "timed_out": "danger",
            "action_required": "warn", "startup_failure": "danger"}


def _git(repo: Path, *args: str, timeout: int = 20) -> str:
    # No hooks run for these queries; fsmonitor and external diff drivers are
    # off because a repo's own config must not execute code on a read.
    cmd = ["git", "-C", str(repo), "-c", "core.fsmonitor=false", "-c", "core.quotepath=false",
           "--no-pager", *args]
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def _resolve_repo(path: str) -> Path:
    p = Path(path or ".").expanduser().resolve()
    top = _git(p, "rev-parse", "--show-toplevel").strip() if p.is_dir() else ""
    if top:
        return Path(top)
    # A bare name ("nova") is a repository under ~/Projects: the model knows
    # the user's project names, not where they live.
    if path and "/" not in path and not path.startswith(("~", ".")):
        return _repo_by_name(path)
    raise ValueError(f"{path or '.'} is not inside a git repository")


def _repo_by_name(name: str) -> Path:
    root = Path(DEFAULT_ROOT).expanduser()
    repos = _find_repos(root) if root.is_dir() else []
    wanted = name.lower()
    exact = [r for r in repos if r.name.lower() == wanted]
    matches = exact or [r for r in repos if wanted in r.name.lower()]
    if len(matches) == 1:
        return matches[0]
    if matches:
        names = ", ".join(str(r.relative_to(root)) for r in matches[:10])
        raise ValueError(f"several repositories match {name!r} under {DEFAULT_ROOT}: {names}; pass one as path")
    close = difflib.get_close_matches(wanted, [r.name.lower() for r in repos], n=5, cutoff=0.5)
    hint = f" Closest: {', '.join(close)}." if close else ""
    raise ValueError(f"no git repository named {name!r} under {DEFAULT_ROOT}.{hint} "
                     "Use git_companion.scan to list them, or pass a full path.")


def _parse_status(text: str) -> dict:
    """Parse `git status --porcelain=v1 -b`."""
    info = {"branch": "", "upstream": "", "ahead": 0, "behind": 0,
            "staged": [], "modified": [], "untracked": [], "conflicted": []}
    for line in text.splitlines():
        if line.startswith("## "):
            head = line[3:]
            if head.startswith("No commits yet on "):
                info["branch"] = head.split(" on ", 1)[1]
                continue
            names, _, track = head.partition(" [")
            branch, _, upstream = names.partition("...")
            info["branch"], info["upstream"] = branch, upstream
            for part in track.rstrip("]").split(", "):
                if part.startswith("ahead "):
                    info["ahead"] = int(part[6:])
                elif part.startswith("behind "):
                    info["behind"] = int(part[7:])
            continue
        if len(line) < 4:
            continue
        x, y, name = line[0], line[1], line[3:]
        if x == "?" and y == "?":
            info["untracked"].append(name)
        elif x == "U" or y == "U" or (x == y and x in "AD"):
            info["conflicted"].append(name)
        else:
            if x not in " ?":
                info["staged"].append((x, name))
            if y not in " ?":
                info["modified"].append((y, name))
    return info


def _log(repo: Path, count: int, since: str = "") -> list[dict]:
    args = ["log", f"-n{count}", f"--format=%h{_SEP}%an{_SEP}%cs{_SEP}%s"]
    if since:
        args.append(f"--since={since}")
    out = []
    for line in _git(repo, *args).splitlines():
        parts = line.split(_SEP)
        if len(parts) == 4:
            out.append({"hash": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]})
    return out


def _local_status(path: str, log_count: int) -> tuple:
    repo = _resolve_repo(path)
    info = _parse_status(_git(repo, "status", "--porcelain=v1", "-b"))
    stat = _git(repo, "diff", "--cached", "--no-ext-diff", "--stat").strip()
    staged_diff = _git(repo, "diff", "--cached", "--no-ext-diff", "--unified=2").strip()
    commits = _log(repo, log_count)
    local_shas = _git(repo, "rev-list", "-n", "200", "HEAD").split()
    return repo, info, stat, staged_diff, commits, local_shas, _github_slug(repo), _default_branch(repo)


async def _status(path: str = ".", log_count: int = 10) -> dict:
    log_count = max(1, min(int(log_count), 50))
    # git is blocking; the MCP calls need the event loop free.
    repo, info, stat, staged_diff, commits, local_shas, slug, default = await asyncio.to_thread(
        _local_status, path, log_count)
    clean = not (info["staged"] or info["modified"] or info["untracked"] or info["conflicted"])
    remote = await _remote_status(slug, info["branch"], default, local_shas)

    summary = {
        "repo": str(repo),
        "branch": info["branch"],
        "upstream": info["upstream"],
        "ahead": info["ahead"],
        "behind": info["behind"],
        "clean": clean,
        "staged": [f"{c} {n}" for c, n in info["staged"]],
        "modified": [f"{c} {n}" for c, n in info["modified"]],
        "untracked": info["untracked"][:30],
        "conflicted": info["conflicted"],
        "staged_stat": stat,
        "staged_diff": staged_diff[:MAX_DIFF_CHARS] + ("\n… (cut)" if len(staged_diff) > MAX_DIFF_CHARS else ""),
        "recent_commits": [f"{c['hash']} {c['date']} {c['subject']}" for c in commits],
        "github": _remote_summary(remote),
    }
    report = _status_report(repo, info, clean, stat, commits)
    _remote_report(report, remote, info["branch"])
    return with_artifact(summary, report)


def _status_report(repo: Path, info: dict, clean: bool, stat: str, commits: list[dict]) -> Report:
    report = Report(repo.name, subtitle=short_path(str(repo), 60), kicker="Nova · git")
    sync = f"↑{info['ahead']} ↓{info['behind']}" if info["upstream"] else "no upstream"
    report.stats([
        Stat("Branch", _short_branch(info["branch"]) or "?", "info", hint=sync),
        Stat("Staged", len(info["staged"]), "ok" if info["staged"] else "muted"),
        Stat("Modified", len(info["modified"]), "warn" if info["modified"] else "ok"),
        Stat("Untracked", len(info["untracked"]), "warn" if info["untracked"] else "ok"),
        Stat("Conflicts", len(info["conflicted"]), "danger" if info["conflicted"] else "ok"),
    ])
    if clean:
        report.callout("Nothing to commit, working tree clean.", tone="ok", title="Clean.")
    if info["conflicted"]:
        report.section("Conflicts")
        report.findings([Finding(n, tone="danger", tag="conflict") for n in info["conflicted"][:20]])
    for title, items in (("Staged", info["staged"]), ("Modified", info["modified"])):
        if items:
            report.section(title, note=plural(len(items), "file"))
            report.table(["", "File"], [[c, n] for c, n in items[:30]], mono={0, 1})
    if info["untracked"]:
        report.section("Untracked", note=plural(len(info["untracked"]), "file"))
        report.chips([(n, "muted") for n in info["untracked"][:30]])
    if stat:
        report.section("Staged changes")
        report.callout(stat.splitlines()[-1].strip(), tone="info")
    if commits:
        report.section("Recent commits")
        report.table(["Commit", "Date", "Author", "Message"],
                     [[c["hash"], c["date"], c["author"], c["subject"]] for c in commits], mono={0})
    return report


def _short_branch(branch: str, limit: int = 16) -> str:
    """Branch for a stat card: long names would fill the card; the full one
    stays in the GitHub part and the summary."""
    return branch if len(branch) <= limit else branch[: limit - 1] + "…"


def _find_repos(root: Path) -> list[Path]:
    repos: list[Path] = []
    for dirpath, dirnames, _ in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        if ".git" in dirnames or (Path(dirpath) / ".git").is_file():
            repos.append(Path(dirpath))
            dirnames[:] = []  # nested repos/submodules are not listed on their own
            continue
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        if depth >= SCAN_DEPTH:
            dirnames[:] = []
        if len(repos) >= MAX_REPOS:
            break
    if (root / ".git").exists() and root not in repos:
        repos.insert(0, root)
    return repos[:MAX_REPOS]


def _local_scan(base: Path, days: int) -> list[dict]:
    rows = []
    for repo in _find_repos(base):
        info = _parse_status(_git(repo, "status", "--porcelain=v1", "-b"))
        last = (_log(repo, 1) or [None])[0]
        recent = len(_log(repo, 500, since=f"{days} days ago"))
        dirty = len(info["staged"]) + len(info["modified"]) + len(info["untracked"]) + len(info["conflicted"])
        rows.append({"name": str(repo.relative_to(base)) if repo != base else repo.name, "branch": info["branch"],
                     "dirty": dirty, "ahead": info["ahead"], "behind": info["behind"],
                     "has_upstream": bool(info["upstream"]), "recent": recent,
                     "last": f"{last['date']} {last['subject']}" if last else "no commits",
                     "slug": _github_slug(repo)})
    rows.sort(key=lambda r: (-(r["dirty"] > 0), -r["recent"], r["name"].lower()))
    return rows


async def _scan(root: str = DEFAULT_ROOT, days: int = 7) -> dict:
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise ValueError(f"{root} is not a directory")
    days = max(1, min(int(days), 365))
    rows = await asyncio.to_thread(_local_scan, base, days)
    remote_note = await _remote_scan(rows)

    summary = {
        "root": str(base),
        "days": days,
        "repos": len(rows),
        "dirty": [f"{r['name']} ({r['branch']}): {r['dirty']} changes" for r in rows if r["dirty"]],
        "unpushed": [f"{r['name']}: ahead {r['ahead']}" for r in rows if r["ahead"]],
        "behind": [f"{r['name']}: behind {r['behind']}" for r in rows if r["behind"]],
        "active": [f"{r['name']}: {plural(r['recent'], 'commit')} in {days} days" for r in rows if r["recent"]],
        "last_commits": {r["name"]: r["last"] for r in rows},
        "github": remote_note or {
            r["name"]: {"repo": r["slug_text"], "open_prs": r["prs"], "open_issues": r["issues"], "ci": r["ci"]}
            for r in rows if "prs" in r
        },
    }
    return with_artifact(summary, _scan_report(base, rows, days, remote_note))


def _scan_report(base: Path, rows: list[dict], days: int, remote_note: str = "") -> Report:
    report = Report("Repositories", subtitle=short_path(str(base), 60), kicker="Nova · git")
    dirty = sum(1 for r in rows if r["dirty"])
    ahead = sum(1 for r in rows if r["ahead"])
    behind = sum(1 for r in rows if r["behind"])
    report.stats([
        Stat("Repositories", len(rows), "info"),
        Stat("Uncommitted", dirty, "warn" if dirty else "ok"),
        Stat("Unpushed", ahead, "warn" if ahead else "ok"),
        Stat("Behind", behind, "warn" if behind else "ok"),
        Stat(f"Commits ({days}d)", sum(r["recent"] for r in rows), "info"),
    ])
    if not rows:
        report.callout("No git repositories found here.", tone="info", title="Nothing found.")
        return report
    report.section("All repositories")
    report.table(
        ["Repository", "Branch", "Changes", "Sync", "PRs", "Issues", "CI", "Last commit"],
        [[r["name"], r["branch"], r["dirty"] or "clean",
          f"↑{r['ahead']} ↓{r['behind']}" if r["has_upstream"] else "no upstream",
          r.get("prs", "–"), r.get("issues", "–"), r.get("ci", "–"), r["last"]] for r in rows],
        tones=["warn" if r["dirty"] else "" for r in rows], mono={0, 1},
    )
    if remote_note:
        report.callout(remote_note, tone="info", title="GitHub columns empty.")
    return report


# ---- GitHub (through the MCP server's skill-only tools) -------------------


class _Unavailable(Exception):
    """The remote part can't be read; the message says why, for the report."""


def _github_slug(repo: Path) -> tuple[str, str] | None:
    url = _git(repo, "remote", "get-url", "origin").strip()
    m = _GITHUB_URL.search(url)
    return (m.group(1), m.group(2)) if m else None


def _default_branch(repo: Path) -> str:
    ref = _git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").strip()
    return ref.split("/", 1)[1] if "/" in ref else ""


def _missing_tools() -> str:
    """Why the GitHub part can't be read, or "" when every tool is there."""
    missing = [t for t in _REMOTE_TOOLS if host.find_mcp_tool(t) is None]
    if not missing:
        return ""
    if len(missing) == len(_REMOTE_TOOLS):
        return "The GitHub MCP server is not connected (MCP dialog → Add GitHub server)."
    return (f"The GitHub MCP server lacks {', '.join(missing)}: add the toolsets "
            "issues, pull_requests and actions to its --toolsets argument.")


async def _mcp(tool: str, args: dict):
    name = host.find_mcp_tool(tool)
    if name is None:
        raise _Unavailable(f"{tool} is not available")
    data = await host.call_tool(name, args)
    if isinstance(data, str):
        try:
            return json.loads(data)
        except ValueError:
            raise RuntimeError(data[:200]) from None
    return data


def _ci_label(run: dict | None) -> tuple[str, str]:
    """(label, tone) of a workflow run."""
    if not run:
        return "no runs", "muted"
    if run.get("status") != "completed":
        return run.get("status") or "running", "info"
    conclusion = run.get("conclusion") or "unknown"
    return conclusion, _CI_TONE.get(conclusion, "muted")


def _checks_label(data: dict) -> tuple[str, str]:
    runs = data.get("check_runs") or []
    if not runs:
        return "no checks", "muted"
    if any(r.get("status") != "completed" for r in runs):
        return f"running ({len(runs)} checks)", "info"
    failed = [r.get("name", "?") for r in runs if r.get("conclusion") not in ("success", "skipped", "neutral")]
    if failed:
        return f"failing: {', '.join(failed[:3])}", "danger"
    return f"passing ({len(runs)} checks)", "ok"


async def _remote_status(slug, branch: str, default: str, local_shas: list[str]) -> dict:
    if slug is None:
        return {"error": "No GitHub remote: origin is not a github.com URL."}
    missing = _missing_tools()
    owner, name = slug
    if missing:
        return {"slug": f"{owner}/{name}", "error": missing}
    o = {"owner": owner, "repo": name}
    parts = await asyncio.gather(
        _mcp("list_commits", {**o, "sha": branch, "perPage": 50, "fields": ["sha"]}) if branch else asyncio.sleep(0),
        _mcp("list_pull_requests", {**o, "state": "open", "perPage": 30,
                                    "fields": ["number", "title", "draft", "user", "head", "updated_at"]}),
        _mcp("list_issues", {**o, "state": "OPEN", "perPage": 5, "fields": ["number", "title", "updated_at"]}),
        _mcp("actions_list", {**o, "method": "list_workflow_runs", "per_page": 15}),
        return_exceptions=True,
    )
    commits, prs, issues, runs = parts
    remote: dict = {"slug": f"{owner}/{name}", "errors": {}}

    if isinstance(commits, BaseException):
        if "404" in str(commits) or "Not Found" in str(commits):
            remote["branch_missing"] = True
        else:
            remote["errors"]["sync"] = str(commits)
    elif isinstance(commits, list):
        remote_shas = [c.get("sha", "") for c in commits]
        local_set, remote_set = set(local_shas), set(remote_shas)
        behind = next((i for i, sha in enumerate(remote_shas) if sha in local_set), None)
        ahead = next((i for i, sha in enumerate(local_shas) if sha in remote_set), None)
        remote["behind"] = behind if behind is not None else f"{len(remote_shas)}+"
        remote["ahead"] = ahead if ahead is not None else f"{len(local_shas)}+"

    if isinstance(prs, BaseException):
        remote["errors"]["pull requests"] = str(prs)
    else:
        remote["prs"] = [{
            "number": p.get("number"), "title": html.unescape(p.get("title") or ""),
            "draft": bool(p.get("draft")), "author": (p.get("user") or {}).get("login", ""),
            "branch": (p.get("head") or {}).get("ref", ""), "updated": (p.get("updated_at") or "")[:10],
        } for p in prs or []]
        mine = next((p for p in remote["prs"] if branch and p["branch"] == branch), None)
        if mine:
            try:
                checks = await _mcp("pull_request_read", {**o, "method": "get_check_runs",
                                                          "pullNumber": mine["number"]})
                mine["checks"] = _checks_label(checks if isinstance(checks, dict) else {})
            except Exception as e:  # noqa: BLE001 - shown in the report
                mine["checks"] = (f"unknown ({e})"[:80], "muted")
            remote["branch_pr"] = mine

    if isinstance(issues, BaseException):
        remote["errors"]["issues"] = str(issues)
    else:
        remote["open_issues"] = issues.get("totalCount", len(issues.get("issues") or []))
        remote["issues"] = [{"number": i.get("number"), "title": html.unescape(i.get("title") or ""),
                             "updated": (i.get("updated_at") or "")[:10]} for i in issues.get("issues") or []]

    if isinstance(runs, BaseException):
        remote["errors"]["CI"] = str(runs)
    else:
        all_runs = runs.get("workflow_runs") or []
        remote["ci"] = {
            b: _ci_label(next((r for r in all_runs if r.get("head_branch") == b), None))
            for b in dict.fromkeys(x for x in (branch, default) if x)
        }
    return remote


def _remote_summary(remote: dict) -> dict:
    """The GitHub part for the model: short, no URLs."""
    if "error" in remote:
        return {"unavailable": remote["error"]}
    out: dict = {"repo": remote["slug"]}
    if remote.get("branch_missing"):
        out["branch"] = "not on GitHub yet"
    elif "behind" in remote:
        out["unpulled_commits"], out["unpushed_commits"] = remote["behind"], remote["ahead"]
    if "prs" in remote:
        out["open_prs"] = [f"#{p['number']} {p['title']}" + (" (draft)" if p["draft"] else "")
                           for p in remote["prs"][:10]]
    if remote.get("branch_pr"):
        p = remote["branch_pr"]
        out["this_branch_pr"] = f"#{p['number']} {p['title']}, checks {p['checks'][0]}"
    if "ci" in remote:
        out["ci"] = {b: label for b, (label, _) in remote["ci"].items()}
    if "open_issues" in remote:
        out["open_issues"] = remote["open_issues"]
        out["recent_issues"] = [f"#{i['number']} {i['title']}" for i in remote["issues"]]
    if remote["errors"]:
        out["errors"] = remote["errors"]
    return out


def _remote_report(report: Report, remote: dict, branch: str) -> None:
    if "error" in remote:
        report.section("GitHub")
        report.callout(remote["error"], tone="info", title="Remote part unavailable.")
        return
    report.section("GitHub", note=remote["slug"])
    stats = []
    if remote.get("branch_missing"):
        stats.append(Stat("Branch on GitHub", "not pushed", "warn", hint=branch))
    elif "behind" in remote:
        stats.append(Stat("To pull", remote["behind"], "warn" if remote["behind"] else "ok", hint="commits on GitHub"))
        stats.append(Stat("To push", remote["ahead"], "warn" if remote["ahead"] else "ok", hint="local commits"))
    if "prs" in remote:
        stats.append(Stat("Open PRs", len(remote["prs"]), "info" if remote["prs"] else "ok"))
    if "open_issues" in remote:
        stats.append(Stat("Open issues", remote["open_issues"], "info"))
    if "ci" in remote and branch in remote["ci"]:
        label, tone = remote["ci"][branch]
        stats.append(Stat("CI", label, tone, hint=branch))
    if stats:
        report.stats(stats)

    if remote.get("branch_pr"):
        p = remote["branch_pr"]
        label, tone = p["checks"]
        report.findings([Finding(f"#{p['number']} {p['title']}", tone=tone, tag="this branch",
                                 meta=f"checks {label}" + (" · draft" if p["draft"] else ""))])
    if remote.get("prs"):
        report.section("Pull requests", note=plural(len(remote["prs"]), "open PR"))
        report.table(["#", "Title", "Branch", "Author", "Updated"],
                     [[p["number"], p["title"] + (" (draft)" if p["draft"] else ""), p["branch"],
                       p["author"], p["updated"]] for p in remote["prs"][:15]],
                     tones=["info" if p["branch"] == branch else "" for p in remote["prs"][:15]], mono={0, 2})
    if remote.get("ci"):
        report.section("CI", note="latest workflow run")
        report.chips([(f"{b}: {label}", tone) for b, (label, tone) in remote["ci"].items()])
    if remote.get("issues"):
        report.section("Issues", note=f"{remote['open_issues']} open")
        report.table(["#", "Title", "Updated"],
                     [[i["number"], i["title"], i["updated"]] for i in remote["issues"]], mono={0})
    if remote["errors"]:
        report.findings([Finding(f"GitHub {part} unavailable", detail=err[:300], tone="muted")
                         for part, err in remote["errors"].items()])


async def _remote_scan(rows: list[dict]) -> str:
    """Fill prs/issues/ci on the most active GitHub repos; returns a note when
    the GitHub columns stay empty."""
    targets = [r for r in rows if r["slug"]][:MAX_REMOTE_REPOS]
    if not targets:
        return ""
    missing = _missing_tools()
    if missing:
        return missing

    async def one(row: dict) -> None:
        owner, name = row["slug"]
        o = {"owner": owner, "repo": name}
        prs, issues, runs = await asyncio.gather(
            _mcp("list_pull_requests", {**o, "state": "open", "perPage": 100, "fields": ["number"]}),
            _mcp("list_issues", {**o, "state": "OPEN", "perPage": 1, "fields": ["number"]}),
            _mcp("actions_list", {**o, "method": "list_workflow_runs", "per_page": 1}),
            return_exceptions=True,
        )
        row["slug_text"] = f"{owner}/{name}"
        row["prs"] = "?" if isinstance(prs, BaseException) else len(prs or [])
        row["issues"] = "?" if isinstance(issues, BaseException) else issues.get("totalCount", "?")
        row["ci"] = "?" if isinstance(runs, BaseException) else _ci_label(
            (runs.get("workflow_runs") or [None])[0])[0]

    await asyncio.gather(*(one(r) for r in targets))
    return ""


def get_tools():
    return [
        Tool(
            name="git_companion.status",
            description=(
                "Read-only status of one of the user's repositories, local and on GitHub: branch, "
                "staged, modified and untracked files, the staged diff and recent commits, plus "
                "commits to pull or push, open pull requests, CI and open issues. Use it for any "
                "git or GitHub status, report or changes of a project, a commit message, or to "
                "explain changes. Prefer it over other GitHub tools for the user's own projects. "
                "Shows a report to the user by itself."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": (
                        "Repository name (e.g. nova, found under ~/Projects) or any path inside it. "
                        "A project the user names is a repository name, even if it matches your own name.")},
                    "log_count": {"type": "integer", "description": "How many recent commits (default 10, max 50)"},
                },
            },
            risk=RiskLevel.READ,
            implementation=_status,
        ),
        Tool(
            name="git_companion.scan",
            description=(
                "Read-only scan of all git repositories under a folder (default ~/Projects): "
                "uncommitted changes, ahead/behind and last commit of each, commits in the last "
                "N days, and open PRs, open issues and CI on GitHub. Use it for \"what did I work "
                "on this week?\", a git/GitHub report of all projects, or to find a repository. "
                "Shows a report."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "root": {"type": "string", "description": "Folder to scan (default ~/Projects)"},
                    "days": {"type": "integer", "description": "Window for recent commits (default 7)"},
                },
            },
            risk=RiskLevel.READ,
            implementation=_scan,
        ),
    ]
