"""Git companion skill (issue #13): real git in throwaway repositories."""

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from nova.skills.loader import SkillLoader
from nova.tools.registry import ToolRegistry
from nova.tools.report import ARTIFACT_KEY

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def load(name):
    registry = ToolRegistry()
    SkillLoader(SKILLS_DIR, registry=registry).register_all(registry)
    return registry.get(f"git_companion.{name}")


def call(name):
    """The tool as a plain function (its implementation is async)."""
    impl = load(name).implementation
    return lambda *args, **kwargs: asyncio.run(impl(*args, **kwargs))


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


def make_repo(path: Path, files=None):
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    for name, text in (files or {"a.txt": "one\n"}).items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "first commit")
    return path


@pytest.fixture(autouse=True)
def _no_user_git_config(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def test_discovered_as_read_only():
    data = json.loads(SkillLoader(SKILLS_DIR).to_json())
    skill = next(s for s in data if s["name"] == "git_companion")
    assert skill["tools"] == [{"name": "git_companion.status", "risk": "read"},
                              {"name": "git_companion.scan", "risk": "read"}]


def test_status_reports_changes(tmp_path):
    repo = make_repo(tmp_path / "proj")
    (repo / "a.txt").write_text("one\ntwo\n")
    (repo / "new.txt").write_text("x")
    git(repo, "add", "new.txt")
    (repo / "loose.txt").write_text("y")

    result = call("status")(str(repo))
    report = result.pop(ARTIFACT_KEY)

    assert result["branch"] == "main" and not result["clean"]
    assert result["staged"] == ["A new.txt"]
    assert result["modified"] == ["M a.txt"]
    assert result["untracked"] == ["loose.txt"]
    assert "new.txt" in result["staged_diff"]
    assert result["recent_commits"][0].endswith("first commit")
    assert report["title"] == "proj" and "loose.txt" in report["html"]


def test_status_works_from_subfolder_and_clean(tmp_path):
    repo = make_repo(tmp_path / "proj", {"sub/f.txt": "1"})
    result = call("status")(str(repo / "sub"))
    assert result["repo"] == str(repo.resolve()) and result["clean"]


def test_status_outside_repo_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="not inside a git repository"):
        call("status")(str(tmp_path))


def test_status_by_repo_name_under_projects(tmp_path):
    projects = tmp_path / "home" / "Projects"
    repo = make_repo(projects / "AI" / "nova")
    make_repo(projects / "dotfiles")
    status = call("status")
    assert status("nova")["repo"] == str(repo)
    assert status("NOVA")["repo"] == str(repo)
    assert status("dot")["repo"] == str(projects / "dotfiles")  # unique partial match


def test_status_by_name_ambiguous_or_unknown(tmp_path):
    projects = tmp_path / "home" / "Projects"
    make_repo(projects / "nova-web")
    make_repo(projects / "nova-cli")
    status = call("status")
    with pytest.raises(ValueError, match="several repositories match 'nova'.*nova-cli.*nova-web"):
        status("nova")
    with pytest.raises(ValueError, match="no git repository named 'nova-clu'.*Closest: nova-cli"):
        status("nova-clu")


def test_status_by_name_exact_beats_partial(tmp_path):
    projects = tmp_path / "home" / "Projects"
    repo = make_repo(projects / "nova")
    make_repo(projects / "nova-web")
    assert call("status")("nova")["repo"] == str(repo)


def test_status_cuts_huge_staged_diff(tmp_path):
    repo = make_repo(tmp_path / "proj")
    (repo / "big.txt").write_text("line\n" * 5000)
    git(repo, "add", "big.txt")
    result = call("status")(str(repo))
    assert len(result["staged_diff"]) < 6100 and result["staged_diff"].endswith("(cut)")


def test_status_ahead_of_upstream(tmp_path):
    origin = make_repo(tmp_path / "origin")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
    (clone / "b.txt").write_text("b")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "local")
    result = call("status")(str(clone))
    assert result["ahead"] == 1 and result["behind"] == 0 and result["upstream"] == "origin/main"


def test_scan_lists_repos_sorted_dirty_first(tmp_path):
    root = tmp_path / "Projects"
    make_repo(root / "clean")
    dirty = make_repo(root / "group" / "dirty")
    (dirty / "wip.txt").write_text("w")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    make_repo(root / "node_modules" / "pkg" / "skipped")
    (root / "plain").mkdir()

    result = call("scan")(str(root))
    report = result.pop(ARTIFACT_KEY)

    assert result["repos"] == 2
    assert result["dirty"] == ["group/dirty (main): 1 changes"]
    assert list(result["last_commits"]) == ["group/dirty", "clean"]
    assert result["active"] == ["group/dirty: 1 commit in 7 days", "clean: 1 commit in 7 days"]
    assert "skipped" not in report["html"]


def test_scan_missing_folder(tmp_path):
    with pytest.raises(ValueError, match="not a directory"):
        call("scan")(str(tmp_path / "nope"))


def test_repo_config_cannot_run_code(tmp_path):
    repo = make_repo(tmp_path / "proj")
    marker = tmp_path / "ran"
    git(repo, "config", "core.fsmonitor", f"touch {marker}; echo")
    call("status")(str(repo))
    assert not marker.exists()


# ---- GitHub part (fake MCP tools through nova.skills.host, AD-042) -------


@pytest.fixture
def github(tmp_path):
    """Fake GitHub MCP tools; returns the dict of canned answers to tweak."""
    from nova.skills import host
    from nova.tools.tool import Tool

    answers = {
        "list_commits": [],
        "list_pull_requests": [
            {"number": 7, "title": "Add &amp; fix", "draft": False, "user": {"login": "me"},
             "head": {"ref": "feature"}, "updated_at": "2026-10-27T10:00:00Z"},
            {"number": 8, "title": "Other", "draft": True, "user": {"login": "bot"},
             "head": {"ref": "x"}, "updated_at": "2026-10-26T10:00:00Z"},
        ],
        "pull_request_read": {"check_runs": [{"name": "tests", "status": "completed", "conclusion": "failure"}]},
        "list_issues": {"issues": [{"number": 3, "title": "Bug", "updated_at": "2026-10-20T00:00:00Z"}],
                        "totalCount": 4},
        "actions_list": {"workflow_runs": [
            {"head_branch": "feature", "status": "in_progress"},
            {"head_branch": "main", "status": "completed", "conclusion": "success"},
        ]},
    }
    calls = []

    def tool(short):
        async def impl(**kwargs):
            calls.append((short, kwargs))
            answer = answers[short]
            if isinstance(answer, Exception):
                raise answer
            return json.dumps(answer(kwargs) if callable(answer) else answer)
        return Tool(name=f"mcp.github.{short}", description="", implementation=impl, model_visible=False)

    registry = ToolRegistry()
    for short in answers:
        registry.register(tool(short))
    host.attach(registry)
    answers["_calls"] = calls
    yield answers
    host.attach(None)


def github_repo(tmp_path, name="proj"):
    repo = make_repo(tmp_path / name)
    git(repo, "remote", "add", "origin", f"git@github.com:me/{name}.git")
    git(repo, "switch", "-q", "-c", "feature")
    return repo


def test_status_github_part(tmp_path, github):
    repo = github_repo(tmp_path)
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (repo / "b.txt").write_text("b")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-q", "-m", "local only")
    # GitHub has two commits we don't, on top of our base.
    github["list_commits"] = [{"sha": "r2"}, {"sha": "r1"}, {"sha": base}]

    result = call("status")(str(repo))
    report = result.pop(ARTIFACT_KEY)
    gh = result["github"]
    assert gh["repo"] == "me/proj"
    assert (gh["unpulled_commits"], gh["unpushed_commits"]) == (2, 1)
    assert gh["open_prs"] == ["#7 Add & fix", "#8 Other (draft)"]
    assert gh["this_branch_pr"].startswith("#7 Add & fix, checks failing: tests")
    assert gh["ci"] == {"feature": "in_progress"}  # no origin/HEAD here, so only this branch
    assert gh["open_issues"] == 4 and gh["recent_issues"] == ["#3 Bug"]
    html_text = report["html"]
    for text in ("GitHub", "me/proj", "To pull", "Pull requests", "this branch", "Issues", "Bug"):
        assert text in html_text
    asked = dict(github["_calls"])
    assert asked["list_commits"]["sha"] == "feature"
    assert asked["pull_request_read"] == {"owner": "me", "repo": "proj", "method": "get_check_runs", "pullNumber": 7}


def test_status_branch_not_on_github(tmp_path, github):
    repo = github_repo(tmp_path)
    github["list_commits"] = RuntimeError("failed to list commits: 404 Not Found")
    result = call("status")(str(repo))
    assert result["github"]["branch"] == "not on GitHub yet"
    assert "not pushed" in result[ARTIFACT_KEY]["html"]


def test_status_one_failing_part_keeps_the_rest(tmp_path, github):
    repo = github_repo(tmp_path)
    github["list_issues"] = RuntimeError("rate limited")
    gh = call("status")(str(repo))["github"]
    assert gh["errors"] == {"issues": "rate limited"}
    assert "open_prs" in gh and "ci" in gh


def test_status_without_github_server_or_remote(tmp_path):
    repo = github_repo(tmp_path)  # no tools attached
    result = call("status")(str(repo))
    assert "not connected" in result["github"]["unavailable"]
    assert "Remote part unavailable" in result[ARTIFACT_KEY]["html"]
    plain = make_repo(tmp_path / "plain")
    assert "No GitHub remote" in call("status")(str(plain))["github"]["unavailable"]


def test_status_names_missing_toolsets(tmp_path, github):
    from nova.skills import host

    host._registry.unregister("mcp.github.actions_list")
    repo = github_repo(tmp_path)
    assert "lacks actions_list" in call("status")(str(repo))["github"]["unavailable"]


def test_scan_adds_github_columns(tmp_path, github):
    root = tmp_path / "Projects"
    github_repo(root, "one")
    make_repo(root / "local-only")
    github["list_pull_requests"] = [{"number": 1}, {"number": 2}]
    result = call("scan")(str(root))
    assert result["github"] == {"one": {"repo": "me/one", "open_prs": 2, "open_issues": 4, "ci": "in_progress"}}
    assert "PRs" in result[ARTIFACT_KEY]["html"]
