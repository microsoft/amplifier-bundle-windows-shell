"""Tests for PowerShell subprocess execution.

Validates that pwsh execution works correctly across platforms and handles
background processes, timeouts, pipelines, and variable expansion properly.

These are integration tests that require real pwsh to be installed.
"""

import asyncio
import shutil

import pytest

from amplifier_module_tool_pwsh import PwshTool

# Check if pwsh (PowerShell Core) is available at module load time
PWSH_AVAILABLE = shutil.which("pwsh") is not None


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh not available")
class TestPwshExecution:
    """Test PowerShell execution with real pwsh binary."""

    @pytest.fixture
    def tool(self):
        """Create a PwshTool instance with permissive settings for testing."""
        return PwshTool(
            {
                "require_approval": False,
                "timeout": 30,
                # Empty denied list so integration tests are not blocked
                "denied_commands": [],
            }
        )

    @pytest.mark.asyncio
    async def test_simple_write_output(self, tool):
        """Test simple Write-Output command produces expected output."""
        result = await tool.execute({"command": 'Write-Output "Hello from PowerShell"'})

        assert result.success
        assert "Hello from PowerShell" in result.output["stdout"]
        assert result.output["returncode"] == 0

    @pytest.mark.asyncio
    async def test_environment_variable_access(self, tool):
        """Test that environment variables are accessible via $env: provider."""
        # [System.Environment]::UserName is cross-platform (Linux + Windows)
        result = await tool.execute(
            {"command": "Write-Output ([System.Environment]::UserName)"}
        )

        assert result.success
        # Username must be non-empty — empty means the variable was not resolved
        assert len(result.output["stdout"].strip()) > 0

    @pytest.mark.asyncio
    async def test_pipeline(self, tool):
        """Test PowerShell object pipeline with Select-Object."""
        result = await tool.execute(
            {
                "command": (
                    "Get-Process | "
                    "Select-Object -First 3 -Property Name, Id | "
                    "Format-Table -AutoSize"
                )
            }
        )

        assert result.success
        assert result.output["returncode"] == 0
        # Format-Table output should have some content
        assert len(result.output["stdout"].strip()) > 0

    @pytest.mark.asyncio
    async def test_variable_expansion_and_math(self, tool):
        """Test variable assignment and sub-expression math expansion."""
        result = await tool.execute(
            {"command": '$a = 10; $b = 20; Write-Output "Sum: $($a + $b)"'}
        )

        assert result.success
        assert "Sum: 30" in result.output["stdout"]

    @pytest.mark.asyncio
    async def test_background_execution_returns_pid(self, tool):
        """Test background command execution returns a valid PID immediately."""
        result = await tool.execute(
            {
                "command": "Start-Sleep -Milliseconds 200",
                "run_in_background": True,
            }
        )

        assert result.success
        assert "pid" in result.output
        assert isinstance(result.output["pid"], int)
        assert result.output["pid"] > 0

        # Give the background process time to finish cleanly
        await asyncio.sleep(0.3)

    @pytest.mark.asyncio
    async def test_timeout_behavior(self, tool):
        """Test that commands are killed and a timeout error is returned."""
        short_timeout_tool = PwshTool(
            {
                "require_approval": False,
                "timeout": 2,
                "denied_commands": [],
            }
        )

        result = await short_timeout_tool.execute(
            {"command": "Start-Sleep -Seconds 60"}
        )

        assert not result.success
        assert "timed out" in result.error["message"].lower()

    @pytest.mark.asyncio
    async def test_per_call_timeout_override(self, tool):
        """Test that timeout parameter in input overrides the class-level default."""
        # The fixture tool has a 30 s class-level timeout, but we pass 2 s
        # per-call via the input dict — the command must be killed early.
        result = await tool.execute(
            {
                "command": "Start-Sleep -Seconds 60",
                "timeout": 2,
            }
        )
        assert not result.success
        assert "timed out" in result.error["message"].lower()


class TestPwshDiscovery:
    """Test PowerShell executable discovery logic."""

    def test_find_powershell_returns_path_or_none(self):
        """_find_powershell() should return a string path or None."""
        tool = PwshTool({"require_approval": False})
        result = tool._find_powershell()

        assert result is None or isinstance(result, str)

    def test_found_path_is_executable(self):
        """If _find_powershell() returns a path it must be an executable file."""
        tool = PwshTool({"require_approval": False})
        pwsh_path = tool._find_powershell()

        if pwsh_path is not None:
            # shutil.which only returns paths to executables, so the path
            # returned by _find_powershell (which also uses shutil.which)
            # must be resolvable as an executable on the current PATH.
            resolved = shutil.which(pwsh_path) or shutil.which("pwsh")
            assert resolved is not None, f"Path {pwsh_path!r} is not an executable"
