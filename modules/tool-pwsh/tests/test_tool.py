"""Tests for the PwshTool contract: mount(), execute(), safety gating,
availability degradation, and output truncation.

subprocess/ctypes are mocked; these tests focus on the Tool-level wiring
(does a denied command actually short-circuit before touching the runner,
does a missing executable produce a loud actionable error, does
ExecutionResult get translated into the same success-means-exit-code-zero
contract tool-bash uses).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from amplifier_module_tool_pwsh import PwshTool, _truncate_output
from amplifier_module_tool_pwsh.discovery import PowerShellExecutable
from amplifier_module_tool_pwsh.runner import ExecutionResult


def _mock_executable() -> PowerShellExecutable:
    return PowerShellExecutable(path="/usr/bin/pwsh", is_core=True)


@pytest.fixture
def tool_with_mocked_executable():
    with patch(
        "amplifier_module_tool_pwsh.find_powershell", return_value=_mock_executable()
    ):
        tool = PwshTool({"safety_profile": "strict"})
    return tool


class TestToolIdentity:
    def test_name_is_pwsh(self, tool_with_mocked_executable: PwshTool) -> None:
        assert tool_with_mocked_executable.name == "pwsh"

    def test_input_schema_requires_command(
        self, tool_with_mocked_executable: PwshTool
    ) -> None:
        schema = tool_with_mocked_executable.input_schema
        assert schema["required"] == ["command"]
        assert "command" in schema["properties"]
        assert "run_in_background" in schema["properties"]

    def test_description_names_resolved_shell_when_available(
        self, tool_with_mocked_executable: PwshTool
    ) -> None:
        assert "/usr/bin/pwsh" in tool_with_mocked_executable.description
        assert "NOT FOUND" not in tool_with_mocked_executable.description


class TestAvailabilityDegradation:
    """Missing PowerShell must be loud (named in the description AND every
    execute() call), never a silent no-op.
    """

    def test_description_names_missing_powershell_on_windows(self) -> None:
        with (
            patch("amplifier_module_tool_pwsh.find_powershell", return_value=None),
            patch("amplifier_module_tool_pwsh.sys.platform", "win32"),
        ):
            tool = PwshTool({})

        assert "POWERSHELL NOT FOUND" in tool.description
        assert "pwsh.exe" in tool.description or "powershell.exe" in tool.description

    def test_description_names_missing_powershell_on_non_windows(self) -> None:
        with (
            patch("amplifier_module_tool_pwsh.find_powershell", return_value=None),
            patch("amplifier_module_tool_pwsh.sys.platform", "linux"),
        ):
            tool = PwshTool({})

        assert "POWERSHELL NOT FOUND" in tool.description
        assert "not Windows" in tool.description

    @pytest.mark.asyncio
    async def test_execute_returns_actionable_error_when_powershell_missing(
        self,
    ) -> None:
        with patch("amplifier_module_tool_pwsh.find_powershell", return_value=None):
            tool = PwshTool({})

        result = await tool.execute({"command": "Get-Process"})

        assert result.success is False
        assert "POWERSHELL NOT FOUND" in str(result.output)
        assert result.error is not None


class TestSafetyGating:
    @pytest.mark.asyncio
    async def test_denied_command_never_reaches_the_runner(
        self, tool_with_mocked_executable: PwshTool
    ) -> None:
        tool_with_mocked_executable._runner = MagicMock()
        tool_with_mocked_executable._runner.run = MagicMock(
            side_effect=AssertionError(
                "runner must not be invoked for a denied command"
            )
        )

        result = await tool_with_mocked_executable.execute(
            {"command": "Format-Volume -DriveLetter D"}
        )

        assert result.success is False
        assert "denied for safety" in str(result.output).lower()

    @pytest.mark.asyncio
    async def test_missing_command_is_rejected_before_safety_check(
        self, tool_with_mocked_executable: PwshTool
    ) -> None:
        result = await tool_with_mocked_executable.execute({})
        assert result.success is False
        assert "required" in str(result.output).lower()


class TestExecuteToToolResultMapping:
    @pytest.mark.asyncio
    async def test_successful_execution_maps_to_success_true(
        self, tool_with_mocked_executable: PwshTool
    ) -> None:
        fake_result = ExecutionResult(
            success=True, exit_code=0, stdout="hello\n", stderr="", timed_out=False
        )
        with patch("asyncio.to_thread", new=AsyncMock(return_value=fake_result)):
            result = await tool_with_mocked_executable.execute(
                {"command": "Write-Output hello"}
            )

        assert result.success is True
        output = result.output
        assert isinstance(output, dict)
        assert output["exit_code"] == 0
        assert output["stdout"] == "hello\n"

    @pytest.mark.asyncio
    async def test_nonzero_exit_code_maps_to_success_false(
        self, tool_with_mocked_executable: PwshTool
    ) -> None:
        fake_result = ExecutionResult(
            success=False, exit_code=1, stdout="", stderr="oops", timed_out=False
        )
        with patch("asyncio.to_thread", new=AsyncMock(return_value=fake_result)):
            result = await tool_with_mocked_executable.execute(
                {"command": "bad-cmdlet"}
            )

        assert result.success is False
        output = result.output
        assert isinstance(output, dict)
        assert output["exit_code"] == 1
        assert output["stderr"] == "oops"

    @pytest.mark.asyncio
    async def test_timeout_maps_to_failure_with_timeout_message(
        self, tool_with_mocked_executable: PwshTool
    ) -> None:
        fake_result = ExecutionResult(
            success=False, exit_code=None, stdout="", stderr="", timed_out=True
        )
        with patch("asyncio.to_thread", new=AsyncMock(return_value=fake_result)):
            result = await tool_with_mocked_executable.execute(
                {"command": "Start-Sleep 999", "timeout": 1}
            )

        assert result.success is False
        error = result.error
        assert isinstance(error, dict)
        assert "timed out" in str(error.get("message", "")).lower()


class TestOutputTruncation:
    def test_output_under_limit_is_unchanged(self) -> None:
        text, truncated, total = _truncate_output("short", max_bytes=100)
        assert text == "short"
        assert truncated is False
        assert total == len("short".encode("utf-8"))

    def test_output_over_limit_is_truncated_with_notice(self) -> None:
        text, truncated, total = _truncate_output("a" * 1000, max_bytes=100)
        assert truncated is True
        assert total == 1000
        assert "truncated" in text
        assert len(text.encode("utf-8")) < 1000

    def test_truncation_does_not_split_multibyte_characters(self) -> None:
        # Each "é" is 2 bytes in UTF-8; force a cut that would otherwise
        # land mid-character.
        text, truncated, total = _truncate_output("é" * 100, max_bytes=51)
        assert truncated is True
        # Must decode cleanly -- would raise UnicodeDecodeError if a
        # multi-byte character had been split.
        text.encode("utf-8").decode("utf-8")
