"""Tests for PwshRunner's process wiring: exit-code propagation, timeout
handling, and Job Object assignment sequencing.

subprocess.Popen is mocked throughout -- these tests exercise the *wiring*
(does ExecutionResult reflect what the process reported, is job
assignment attempted before the payload is sent, is timeout handled by
killing the job rather than just the one PID) rather than a live
PowerShell process. The actual PowerShell-side `$?`/`$LASTEXITCODE`
mapping logic is covered separately in test_runner_script_contract.py
(a truth-table replica of RUNNER_SCRIPT's branching) and, where a real
Windows host is available, by the skipped-elsewhere live execution test.

Platform-independent by construction: `sys.platform` is patched
explicitly per test rather than relying on the host this suite happens to
run on, so the Windows-only assignment path is exercised on Linux too.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from amplifier_module_tool_pwsh.discovery import PowerShellExecutable
from amplifier_module_tool_pwsh.runner import PwshRunner


def _executable() -> PowerShellExecutable:
    return PowerShellExecutable(path="/usr/bin/pwsh", is_core=True)


def _fake_proc(
    returncode: int, stdout: bytes = b"", stderr: bytes = b"", pid: int = 4242
):
    proc = MagicMock(name="Popen")
    proc.pid = pid
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, stderr)
    proc.stdin = MagicMock()
    return proc


class TestExitCodeWiring:
    @pytest.mark.parametrize(
        "returncode,expected_success",
        [
            (0, True),
            (1, False),
            (42, False),  # native exit code preserved, still non-zero -> failure
        ],
    )
    def test_exit_code_maps_to_success_as_exit_code_equals_zero(
        self, returncode: int, expected_success: bool
    ) -> None:
        proc = _fake_proc(returncode, stdout=b"hello\n")
        with (
            patch(
                "amplifier_module_tool_pwsh.runner.subprocess.Popen", return_value=proc
            ),
            patch("amplifier_module_tool_pwsh.runner.sys.platform", "linux"),
        ):
            runner = PwshRunner(_executable())
            result = runner.run("Write-Output hello", timeout=5)

        assert result.exit_code == returncode
        assert result.success is expected_success
        assert result.stdout == "hello\n"
        assert result.timed_out is False

    def test_stdout_and_stderr_are_both_captured_and_decoded(self) -> None:
        proc = _fake_proc(0, stdout=b"out-line\n", stderr=b"warn-line\n")
        with (
            patch(
                "amplifier_module_tool_pwsh.runner.subprocess.Popen", return_value=proc
            ),
            patch("amplifier_module_tool_pwsh.runner.sys.platform", "linux"),
        ):
            result = PwshRunner(_executable()).run("cmd", timeout=5)

        assert result.stdout == "out-line\n"
        assert result.stderr == "warn-line\n"

    def test_non_utf8_bytes_are_replaced_not_raised(self) -> None:
        proc = _fake_proc(0, stdout=b"\xff\xfe garbage")
        with (
            patch(
                "amplifier_module_tool_pwsh.runner.subprocess.Popen", return_value=proc
            ),
            patch("amplifier_module_tool_pwsh.runner.sys.platform", "linux"),
        ):
            result = PwshRunner(_executable()).run("cmd", timeout=5)

        # Must not raise; decode uses errors="replace".
        assert isinstance(result.stdout, str)


class TestJobObjectSequencing:
    def test_assign_to_job_called_before_payload_written_on_windows(self) -> None:
        """The core sequencing guarantee: job assignment happens, and the
        user's payload is only written to stdin afterward. We assert
        ordering via a shared call log rather than trusting independent
        mock call counts, since ordering -- not just occurrence -- is the
        entire point of this design.
        """
        call_order: list[str] = []

        proc = _fake_proc(0)

        def record_communicate(*args, **kwargs):
            call_order.append("communicate")
            return (b"", b"")

        proc.communicate.side_effect = record_communicate

        def record_assign(pid: int) -> bool:
            call_order.append("assign_to_job")
            return True

        with (
            patch(
                "amplifier_module_tool_pwsh.runner.subprocess.Popen", return_value=proc
            ),
            patch("amplifier_module_tool_pwsh.runner.sys.platform", "win32"),
            patch(
                "amplifier_module_tool_pwsh.runner.assign_to_job",
                side_effect=record_assign,
            ),
        ):
            result = PwshRunner(_executable()).run("Get-Process", timeout=5)

        assert call_order == ["assign_to_job", "communicate"], (
            "job assignment must happen strictly before the payload is "
            "communicated to stdin -- this is the entire race-elimination "
            "guarantee this design exists to provide"
        )
        assert result.job_protected is True

    def test_assign_to_job_not_called_on_non_windows(self) -> None:
        proc = _fake_proc(0)
        with (
            patch(
                "amplifier_module_tool_pwsh.runner.subprocess.Popen", return_value=proc
            ),
            patch("amplifier_module_tool_pwsh.runner.sys.platform", "linux"),
            patch("amplifier_module_tool_pwsh.runner.assign_to_job") as mock_assign,
        ):
            result = PwshRunner(_executable()).run("cmd", timeout=5)

        mock_assign.assert_not_called()
        assert result.job_protected is False

    def test_job_assignment_failure_does_not_raise_or_block_execution(self) -> None:
        """assign_to_job returning False (e.g. environmental restriction)
        must not prevent the command from running -- defense-in-depth,
        not a correctness requirement.
        """
        proc = _fake_proc(0, stdout=b"still ran\n")
        with (
            patch(
                "amplifier_module_tool_pwsh.runner.subprocess.Popen", return_value=proc
            ),
            patch("amplifier_module_tool_pwsh.runner.sys.platform", "win32"),
            patch(
                "amplifier_module_tool_pwsh.runner.assign_to_job", return_value=False
            ),
        ):
            result = PwshRunner(_executable()).run("cmd", timeout=5)

        assert result.job_protected is False
        assert result.stdout == "still ran\n"
        assert result.success is True


class TestTimeoutHandling:
    def test_timeout_reports_timed_out_with_no_exit_code(self) -> None:
        proc = _fake_proc(0)
        proc.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="pwsh", timeout=5),
            (b"partial output\n", b""),
        ]

        with (
            patch(
                "amplifier_module_tool_pwsh.runner.subprocess.Popen", return_value=proc
            ),
            patch("amplifier_module_tool_pwsh.runner.sys.platform", "linux"),
        ):
            result = PwshRunner(_executable()).run("Start-Sleep 999", timeout=1)

        assert result.timed_out is True
        assert result.success is False
        assert result.exit_code is None
        assert "partial output" in result.stdout

    def test_timeout_on_windows_kills_via_job_not_just_the_pid(self) -> None:
        """The entire reason to use a Job Object over a bare kill(): a
        timeout must be able to reach descendants the command spawned,
        not only the one PID this module started.
        """
        proc = _fake_proc(0)
        proc.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="pwsh", timeout=5),
            (b"", b""),
        ]

        with (
            patch(
                "amplifier_module_tool_pwsh.runner.subprocess.Popen", return_value=proc
            ),
            patch("amplifier_module_tool_pwsh.runner.sys.platform", "win32"),
            patch("amplifier_module_tool_pwsh.runner.assign_to_job", return_value=True),
            patch(
                "amplifier_module_tool_pwsh.runner.terminate_job", return_value=True
            ) as mock_term,
        ):
            PwshRunner(_executable()).run("cmd", timeout=1)

        mock_term.assert_called_once()
        proc.kill.assert_not_called()

    def test_timeout_falls_back_to_process_kill_when_job_terminate_fails(self) -> None:
        proc = _fake_proc(0)
        proc.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="pwsh", timeout=5),
            (b"", b""),
        ]

        with (
            patch(
                "amplifier_module_tool_pwsh.runner.subprocess.Popen", return_value=proc
            ),
            patch("amplifier_module_tool_pwsh.runner.sys.platform", "win32"),
            patch("amplifier_module_tool_pwsh.runner.assign_to_job", return_value=True),
            patch(
                "amplifier_module_tool_pwsh.runner.terminate_job", return_value=False
            ),
        ):
            PwshRunner(_executable()).run("cmd", timeout=1)

        proc.kill.assert_called_once()
