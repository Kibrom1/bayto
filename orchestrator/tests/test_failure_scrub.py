"""A session's failure reason is visible through the (unauthenticated) API: it must not reveal host paths."""
from pathlib import Path

from orchestrator.api.runtime import scrub_failure


def test_scrub_replaces_the_repo_and_home_paths():
    from orchestrator.api import runtime
    msg = f"run.sh failed: Mounting {runtime._REPO_ROOT}/dev-team read-write, see {Path.home()}/.sbx/log"
    out = scrub_failure(msg)
    assert str(runtime._REPO_ROOT) not in out and "<repo>/dev-team" in out


def test_scrub_replaces_other_user_directories():
    out = scrub_failure("cannot open /Users/kb/Desktop/x and /home/alice/y: denied")
    assert "kb" not in out and "alice" not in out and "/<user>/Desktop/x" in out


def test_scrub_leaves_ordinary_messages_alone():
    assert scrub_failure("while creating the sandbox: SandboxProviderError: exit 1") == \
        "while creating the sandbox: SandboxProviderError: exit 1"
