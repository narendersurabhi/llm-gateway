"""Constitution IX: one feature, one branch. Process tooling, so no spec IDs to cite."""

from pathlib import Path

import pytest
from branch_policy import violations


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    spec = tmp_path / "specs" / "004-search-api"
    spec.mkdir(parents=True)
    for name in ("spec.md", "plan.md"):
        (spec / name).write_text("x")
    (spec / "tasks.md").write_text("- [x] T001 done\n")
    return tmp_path


@pytest.mark.parametrize(
    "branch", ["spec/004-search-api", "fix/chunk-ids", "chore/bump-deps", "docs/readme"]
)
def test_valid_branches(repo, branch):
    assert violations(branch, ["README.md"], repo) == []


@pytest.mark.parametrize("branch", ["feature/x", "spec/search-api", "Spec/004-x", "fix/"])
def test_invalid_branch_names(repo, branch):
    assert violations(branch, [], repo)


@pytest.mark.parametrize("branch", ["main", "master"])
def test_commits_to_protected_branches_rejected(repo, branch):
    [problem] = violations(branch, [], repo)
    assert "don't commit to" in problem


def test_detached_head_is_allowed(repo):
    assert violations("HEAD", ["src/x.py"], repo) == []


def test_spec_branch_needs_its_spec_folder(repo):
    [problem] = violations("spec/005-other", [], repo)
    assert "specs/005-other/" in problem


def test_spec_branch_needs_all_spec_files(repo):
    (repo / "specs" / "004-search-api" / "plan.md").unlink()
    assert violations("spec/004-search-api", [], repo) == [
        "specs/004-search-api/plan.md is missing"
    ]


def test_unfinished_tasks_allowed_while_developing_but_not_when_ready(repo):
    (repo / "specs" / "004-search-api" / "tasks.md").write_text("- [x] T001\n- [ ] T002 wire CLI\n")
    assert violations("spec/004-search-api", [], repo) == []
    [problem] = violations("spec/004-search-api", [], repo, ready=True)
    assert "T002 wire CLI" in problem


def test_docs_branch_cannot_change_code(repo):
    assert violations("docs/readme", ["README.md", "src/service/app.py"], repo) == [
        "docs branch changes code: src/service/app.py"
    ]


def test_existing_specs_satisfy_policy():
    """Every finished spec folder would pass as its own feature branch. Drafts (tests written,
    not implemented) are exempt here; `--ready` still blocks a PR for them."""
    root = Path(__file__).parent.parent
    for spec_dir in sorted((root / "specs").glob("[0-9][0-9][0-9]-*")):
        if "| **Status** | Draft" in (spec_dir / "spec.md").read_text():
            continue
        assert violations(f"spec/{spec_dir.name}", [], ready=True) == [], spec_dir.name


@pytest.mark.parametrize("path", ["deploy/aws/cloudformation.yaml", "Dockerfile", "scripts/x.py"])
def test_docs_branch_cannot_change_deployment_or_tooling(repo, path):
    assert violations("docs/readme", [path], repo) == [f"docs branch changes code: {path}"]


def test_current_branch_works_before_the_first_commit(tmp_path, monkeypatch):
    import subprocess

    import branch_policy

    subprocess.run(["git", "init", "-q", "-b", "spec/001-x", str(tmp_path)], check=True)
    monkeypatch.setattr(branch_policy, "ROOT", tmp_path)
    assert branch_policy.current_branch() == "spec/001-x"
