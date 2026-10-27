"""Git companion skill (issue #13): real git in throwaway repositories."""

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

    result = load("status").implementation(str(repo))
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
    result = load("status").implementation(str(repo / "sub"))
    assert result["repo"] == str(repo.resolve()) and result["clean"]


def test_status_outside_repo_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="not inside a git repository"):
        load("status").implementation(str(tmp_path))


def test_status_cuts_huge_staged_diff(tmp_path):
    repo = make_repo(tmp_path / "proj")
    (repo / "big.txt").write_text("line\n" * 5000)
    git(repo, "add", "big.txt")
    result = load("status").implementation(str(repo))
    assert len(result["staged_diff"]) < 6100 and result["staged_diff"].endswith("(cut)")


def test_status_ahead_of_upstream(tmp_path):
    origin = make_repo(tmp_path / "origin")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
    (clone / "b.txt").write_text("b")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "local")
    result = load("status").implementation(str(clone))
    assert result["ahead"] == 1 and result["behind"] == 0 and result["upstream"] == "origin/main"


def test_scan_lists_repos_sorted_dirty_first(tmp_path):
    root = tmp_path / "Projects"
    make_repo(root / "clean")
    dirty = make_repo(root / "group" / "dirty")
    (dirty / "wip.txt").write_text("w")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    make_repo(root / "node_modules" / "pkg" / "skipped")
    (root / "plain").mkdir()

    result = load("scan").implementation(str(root))
    report = result.pop(ARTIFACT_KEY)

    assert result["repos"] == 2
    assert result["dirty"] == ["group/dirty (main): 1 changes"]
    assert list(result["last_commits"]) == ["group/dirty", "clean"]
    assert result["active"] == ["group/dirty: 1 commit in 7 days", "clean: 1 commit in 7 days"]
    assert "skipped" not in report["html"]


def test_scan_missing_folder(tmp_path):
    with pytest.raises(ValueError, match="not a directory"):
        load("scan").implementation(str(tmp_path / "nope"))


def test_repo_config_cannot_run_code(tmp_path):
    repo = make_repo(tmp_path / "proj")
    marker = tmp_path / "ran"
    git(repo, "config", "core.fsmonitor", f"touch {marker}; echo")
    load("status").implementation(str(repo))
    assert not marker.exists()
