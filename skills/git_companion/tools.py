"""Git companion: repository status and a scan of all repos, as reports (issue #13).

READ only: every git call is a query. Aggregation happens in code; the model
gets a compact summary and the user a rendered report (AD-039).
"""

import difflib
import os
import subprocess
from pathlib import Path

from nova.tools.report import Finding, Report, Stat, plural, short_path, with_artifact
from nova.tools.tool import RiskLevel, Tool

MAX_DIFF_CHARS = 6000
MAX_REPOS = 60
SCAN_DEPTH = 3
DEFAULT_ROOT = "~/Projects"
_SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".cache", "target", "dist", "build"}
_SEP = "\x1f"


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


def _status(path: str = ".", log_count: int = 10) -> dict:
    repo = _resolve_repo(path)
    log_count = max(1, min(int(log_count), 50))
    info = _parse_status(_git(repo, "status", "--porcelain=v1", "-b"))
    stat = _git(repo, "diff", "--cached", "--no-ext-diff", "--stat").strip()
    staged_diff = _git(repo, "diff", "--cached", "--no-ext-diff", "--unified=2").strip()
    commits = _log(repo, log_count)
    clean = not (info["staged"] or info["modified"] or info["untracked"] or info["conflicted"])

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
    }
    return with_artifact(summary, _status_report(repo, info, clean, stat, commits))


def _status_report(repo: Path, info: dict, clean: bool, stat: str, commits: list[dict]) -> Report:
    report = Report(repo.name, subtitle=short_path(str(repo), 60), kicker="Nova · git")
    sync = f"↑{info['ahead']} ↓{info['behind']}" if info["upstream"] else "no upstream"
    report.stats([
        Stat("Branch", info["branch"] or "?", "info", hint=sync),
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


def _scan(root: str = DEFAULT_ROOT, days: int = 7) -> dict:
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise ValueError(f"{root} is not a directory")
    days = max(1, min(int(days), 365))
    rows = []
    for repo in _find_repos(base):
        info = _parse_status(_git(repo, "status", "--porcelain=v1", "-b"))
        last = (_log(repo, 1) or [None])[0]
        recent = len(_log(repo, 500, since=f"{days} days ago"))
        dirty = len(info["staged"]) + len(info["modified"]) + len(info["untracked"]) + len(info["conflicted"])
        rows.append({"name": str(repo.relative_to(base)) if repo != base else repo.name, "branch": info["branch"],
                     "dirty": dirty, "ahead": info["ahead"], "behind": info["behind"],
                     "has_upstream": bool(info["upstream"]), "recent": recent,
                     "last": f"{last['date']} {last['subject']}" if last else "no commits"})
    rows.sort(key=lambda r: (-(r["dirty"] > 0), -r["recent"], r["name"].lower()))

    summary = {
        "root": str(base),
        "days": days,
        "repos": len(rows),
        "dirty": [f"{r['name']} ({r['branch']}): {r['dirty']} changes" for r in rows if r["dirty"]],
        "unpushed": [f"{r['name']}: ahead {r['ahead']}" for r in rows if r["ahead"]],
        "behind": [f"{r['name']}: behind {r['behind']}" for r in rows if r["behind"]],
        "active": [f"{r['name']}: {plural(r['recent'], 'commit')} in {days} days" for r in rows if r["recent"]],
        "last_commits": {r["name"]: r["last"] for r in rows},
    }
    return with_artifact(summary, _scan_report(base, rows, days))


def _scan_report(base: Path, rows: list[dict], days: int) -> Report:
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
        ["Repository", "Branch", "Changes", "Sync", "Last commit"],
        [[r["name"], r["branch"], r["dirty"] or "clean",
          f"↑{r['ahead']} ↓{r['behind']}" if r["has_upstream"] else "no upstream", r["last"]] for r in rows],
        tones=["warn" if r["dirty"] else "" for r in rows], mono={0, 1},
    )
    return report


def get_tools():
    return [
        Tool(
            name="git_companion.status",
            description=(
                "Read-only status of one local git repository: branch, ahead/behind, staged, "
                "modified and untracked files, the staged diff and recent commits. Use it for a "
                "git/GitHub status or report of a project, a commit message, or to explain "
                "changes. Prefer it over remote GitHub tools for the user's own projects. "
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
                "uncommitted changes, ahead/behind and last commit of each, plus commits in the "
                "last N days. Use it for \"what did I work on this week?\", a git/GitHub report "
                "of all projects, or to find a repository. Shows a report."
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
