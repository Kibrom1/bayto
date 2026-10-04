"""M1.10 spike: SubprocessSbxLifecycle's argv shapes, same four-verb family as M2.4's
LocalSbxSandboxProvider/SubprocessSbxRunner -- UNVERIFIED against a real `sbx` binary
(none available in this dev-team sandbox; see sbx.py's module docstring)."""
import subprocess
from unittest.mock import patch

import pytest

from m1_driver.sbx import SbxCommandError, SubprocessSbxLifecycle


def _completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode=returncode, stdout=stdout, stderr=stderr)


def test_create_builds_the_expected_argv():
    lifecycle = SubprocessSbxLifecycle()
    with patch("subprocess.run", return_value=_completed(["sbx"])) as run:
        lifecycle.create("sbx-wad-102")
    run.assert_called_once_with(
        ["sbx", "env", "create", "team.sbxenv.yaml", "--env-arg", "name=sbx-wad-102", "--auto-approve"],
        capture_output=True, text=True, check=False,
    )


def test_start_team_builds_the_expected_argv():
    lifecycle = SubprocessSbxLifecycle()
    with patch("subprocess.run", return_value=_completed(["sbx"])) as run:
        lifecycle.start_team("sbx-wad-102")
    run.assert_called_once_with(
        ["sbx", "exec", "sbx-wad-102", "start-team"], capture_output=True, text=True, check=False,
    )


def test_crew_notify_builds_the_expected_argv():
    lifecycle = SubprocessSbxLifecycle()
    with patch("subprocess.run", return_value=_completed(["sbx"])) as run:
        lifecycle.crew_notify("sbx-wad-102", "advocate")
    run.assert_called_once_with(
        ["sbx", "exec", "sbx-wad-102", "crew-notify", "advocate"], capture_output=True, text=True, check=False,
    )


def test_remove_builds_the_expected_argv():
    lifecycle = SubprocessSbxLifecycle()
    with patch("subprocess.run", return_value=_completed(["sbx"])) as run:
        lifecycle.remove("sbx-wad-102")
    run.assert_called_once_with(
        ["sbx", "env", "rm", "sbx-wad-102", "--auto-approve"], capture_output=True, text=True, check=False,
    )


def test_nonzero_returncode_raises_sbx_command_error():
    lifecycle = SubprocessSbxLifecycle()
    with patch("subprocess.run", return_value=_completed(["sbx"], returncode=1, stderr="boom")):
        with pytest.raises(SbxCommandError):
            lifecycle.create("sbx-wad-102")
