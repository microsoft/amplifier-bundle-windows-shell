"""
PowerShell command execution tool for Amplifier.
Includes safety features and approval mechanisms.
"""

# Amplifier module metadata
__amplifier_module_type__ = "tool"

import asyncio
import logging
import os
import shutil
import signal
import subprocess
import sys
from typing import Any

from amplifier_core import ModuleCoordinator
from amplifier_core import ToolResult

from .safety import SafetyConfig, SafetyValidator

logger = logging.getLogger(__name__)


async def mount(coordinator: ModuleCoordinator, config: dict[str, Any] | None = None):
    """
    Mount the PowerShell tool.

    Args:
        coordinator: Module coordinator
        config: Tool configuration
            - working_dir: Working directory for command execution (default: ".")
              If not set, falls back to session.working_dir capability.
            - timeout: Command timeout in seconds (default: 30)
            - require_approval: Require approval for commands (default: True)
            - safety_profile: Safety profile to use (default: "strict")
              Options: "strict", "standard", "permissive", "unrestricted"
            - allowed_commands: Whitelist of allowed commands (default: [])
            - denied_commands: Additional custom blocklist patterns (default: [])
            - safety_overrides: Fine-grained safety overrides dict with 'allow' and 'block' lists

    Returns:
        Optional cleanup function
    """
    config = config or {}

    # If working_dir not explicitly set in config, use session.working_dir capability
    # This enables server deployments where Path.cwd() returns the wrong directory
    if "working_dir" not in config:
        try:
            wd = await coordinator.get_capability("session.working_dir")
            if wd:
                config = {**config, "working_dir": wd}
        except Exception:
            pass

    tool = PwshTool(config)
    await coordinator.mount("tools", tool, name=tool.name)
    logger.info("Mounted PwshTool")
    return


class PwshTool:
    """Execute PowerShell commands with safety features."""

    name = "pwsh"
    description = (
        "Execute PowerShell commands. Preferred over bash on Windows platforms.\n\n"
        "WHEN TO USE THIS TOOL:\n"
        "- On Windows: use pwsh for ALL system operations (preferred over bash)\n"
        "- On Linux/macOS: use pwsh when PowerShell-specific cmdlets are needed\n"
        "- For cross-platform scripts: pwsh works on Windows, Linux, and macOS\n\n"
        "WINDOWS NOTE: Unix tools (grep, find, cat, ls) are NOT available natively on Windows.\n"
        "Use PowerShell equivalents: Select-String, Get-ChildItem, Get-Content.\n\n"
        "Use bash only for explicitly cross-platform bash scripts on Windows."
    )

    # Default output limit: ~100KB (roughly 25k tokens)
    DEFAULT_MAX_OUTPUT_BYTES = 100_000

    def __init__(self, config: dict[str, Any]):
        """
        Initialize PowerShell tool.

        Args:
            config: Tool configuration
        """
        self.config = config
        self.require_approval = config.get("require_approval", True)
        self.timeout = config.get("timeout", 30)
        self.working_dir = config.get("working_dir", ".")
        # Output limiting to prevent context overflow
        self.max_output_bytes = config.get(
            "max_output_bytes", self.DEFAULT_MAX_OUTPUT_BYTES
        )

        # Initialize safety validator with profile-based system
        safety_profile = config.get("safety_profile", "strict")
        safety_config = SafetyConfig(
            profile=safety_profile,
            allowed_commands=config.get("allowed_commands", []),
            denied_commands=config.get("denied_commands", []),
            safety_overrides=config.get("safety_overrides"),
        )
        self._safety_validator = SafetyValidator(
            profile=safety_profile, config=safety_config
        )

        # Keep for backward compatibility with get_metadata
        self.allowed_commands = config.get("allowed_commands", [])
        self.denied_commands = config.get("denied_commands", [])

    @property
    def input_schema(self) -> dict:
        """Return JSON schema for tool parameters."""
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "PowerShell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Command timeout in seconds (default: 30). Increase for builds, tests, or monitoring. Use run_in_background for truly indefinite processes.",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": "Run command in background, returning immediately with PID. Use for long-running processes like dev servers.",
                    "default": False,
                },
            },
            "required": ["command"],
        }

    def get_metadata(self) -> dict[str, Any]:
        """Return tool metadata for approval system."""
        return {
            "requires_approval": self.require_approval,
            "approval_hints": {
                "risk_level": "high",
                "dangerous_patterns": self.denied_commands,
                "safe_patterns": self.allowed_commands,
            },
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        """
        Execute a PowerShell command.

        Args:
            input: Dictionary with 'command' and optional 'timeout' /
                   'run_in_background' keys

        Returns:
            Tool result with command output
        """
        command = input.get("command")
        if not command:
            error_msg = "Command is required"
            return ToolResult(
                success=False, output=error_msg, error={"message": error_msg}
            )

        timeout = input.get("timeout", self.timeout)
        run_in_background = input.get("run_in_background", False)

        # Safety checks using profile-based validator
        safety_result = self._safety_validator.validate(command)
        if not safety_result.allowed:
            error_msg = f"Command denied for safety: {safety_result.reason}"
            if safety_result.hint:
                error_msg += f"\n  Hint: {safety_result.hint}"
            return ToolResult(
                success=False,
                output=error_msg,
                error={"message": error_msg},
            )

        # Find PowerShell executable
        pwsh_exe = self._find_powershell()
        if not pwsh_exe:
            error_msg = (
                "PowerShell not found.\n\n"
                "Install PowerShell Core (recommended):\n"
                "  https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell\n\n"
                "Windows: winget install Microsoft.PowerShell\n"
                "macOS: brew install powershell/tap/powershell\n"
                "Linux: See documentation for your distribution"
            )
            return ToolResult(
                success=False,
                output=error_msg,
                error={"message": error_msg},
            )

        # Approval is now handled by approval hook via tool:pre event

        try:
            if run_in_background:
                # Execute command in background and return immediately
                result = await self._run_command_background(command, pwsh_exe)
                return ToolResult(
                    success=True,
                    output={
                        "pid": result["pid"],
                        "message": f"Command started in background with PID {result['pid']}",
                        "note": "Use Get-Process or Stop-Process to manage the background process.",
                    },
                )
            else:
                # Execute command and wait for completion
                result = await self._run_command(command, pwsh_exe, timeout=timeout)

                # Apply output truncation to prevent context overflow
                stdout, stdout_truncated, stdout_bytes = self._truncate_output(
                    result["stdout"]
                )
                stderr, stderr_truncated, stderr_bytes = self._truncate_output(
                    result["stderr"]
                )

                output = {
                    "stdout": stdout,
                    "stderr": stderr,
                    "returncode": result["returncode"],
                }

                # Include truncation metadata if either was truncated
                if stdout_truncated or stderr_truncated:
                    output["truncated"] = True
                    if stdout_truncated:
                        output["stdout_total_bytes"] = stdout_bytes
                    if stderr_truncated:
                        output["stderr_total_bytes"] = stderr_bytes

                return ToolResult(
                    success=result["returncode"] == 0,
                    output=output,
                )

        except TimeoutError:
            error_msg = f"Command timed out after {timeout} seconds"
            return ToolResult(
                success=False,
                output=error_msg,
                error={"message": error_msg},
            )
        except Exception as e:
            logger.error(f"Command execution error: {e}")
            error_msg = str(e)
            return ToolResult(
                success=False, output=error_msg, error={"message": error_msg}
            )

    # NOTE: _is_safe_command has been replaced by SafetyValidator which provides
    # profile-based safety with smart pattern matching. See safety.py for the
    # implementation.

    def _find_powershell(self) -> str | None:
        """Find PowerShell executable.

        Prefers pwsh (PowerShell Core) over powershell (Windows PowerShell).
        """
        # Try PowerShell Core first (cross-platform)
        pwsh = shutil.which("pwsh")
        if pwsh:
            return pwsh

        # Fall back to Windows PowerShell on Windows
        if sys.platform == "win32":
            powershell = shutil.which("powershell")
            if powershell:
                return powershell

        return None

    def _extract_head_bytes(self, output: str, budget: int) -> str:
        """Extract first N bytes from output, respecting UTF-8 boundaries.

        Args:
            output: The string to extract from
            budget: Maximum bytes to extract

        Returns:
            String containing at most `budget` bytes, not splitting multi-byte chars
        """
        encoded = output.encode("utf-8")
        if len(encoded) <= budget:
            return output

        # Truncate at byte level, then decode safely
        truncated_bytes = encoded[:budget]

        # Find valid UTF-8 boundary by trying to decode
        # Work backwards until we get valid UTF-8
        for i in range(len(truncated_bytes), max(0, len(truncated_bytes) - 4), -1):
            try:
                return truncated_bytes[:i].decode("utf-8")
            except UnicodeDecodeError:
                continue

        # Fallback: decode with error replacement (shouldn't normally happen)
        return truncated_bytes.decode("utf-8", errors="ignore")

    def _extract_tail_bytes(self, output: str, budget: int) -> str:
        """Extract last N bytes from output, respecting UTF-8 boundaries.

        Args:
            output: The string to extract from
            budget: Maximum bytes to extract

        Returns:
            String containing at most `budget` bytes, not splitting multi-byte chars
        """
        encoded = output.encode("utf-8")
        if len(encoded) <= budget:
            return output

        # Truncate at byte level from the end
        truncated_bytes = encoded[-budget:]

        # Find valid UTF-8 boundary by trying to decode.
        # Work forwards until we get valid UTF-8 (skip partial char at start).
        # We scan at most 4 positions because a UTF-8 continuation sequence is
        # at most 3 bytes wide (leading byte + up to 3 continuation bytes).
        for i in range(min(4, len(truncated_bytes))):
            try:
                return truncated_bytes[i:].decode("utf-8")
            except UnicodeDecodeError:
                continue

        # Edge-case fallback: all 4 scanned bytes are continuation bytes
        # (0x80–0xBF), which would only happen with a corrupted or non-UTF-8
        # byte stream. errors="ignore" silently drops the undecodable bytes
        # rather than raising, keeping the tool output usable.
        return truncated_bytes.decode("utf-8", errors="ignore")

    def _truncate_output(self, output: str) -> tuple[str, bool, int]:
        """Truncate output if it exceeds max_output_bytes.

        Uses line-based truncation for cleaner output, with byte-level fallback
        for edge cases like single giant lines (minified JSON, base64).

        Returns:
            Tuple of (possibly truncated output, was_truncated, original_bytes)
        """
        original_bytes = len(output.encode("utf-8"))

        if original_bytes <= self.max_output_bytes:
            return output, False, original_bytes

        # Preserve head and tail with truncation indicator
        # Use roughly 40% head, 40% tail, leaving room for indicator
        head_budget = int(self.max_output_bytes * 0.4)
        tail_budget = int(self.max_output_bytes * 0.4)

        # Split into lines for cleaner truncation
        lines = output.split("\n")

        # Build head (first N lines up to head_budget)
        head_lines = []
        head_size = 0
        for line in lines:
            line_bytes = len((line + "\n").encode("utf-8"))
            if head_size + line_bytes > head_budget:
                break
            head_lines.append(line)
            head_size += line_bytes

        # Build tail (last N lines up to tail_budget)
        tail_lines = []
        tail_size = 0
        for line in reversed(lines):
            line_bytes = len((line + "\n").encode("utf-8"))
            if tail_size + line_bytes > tail_budget:
                break
            tail_lines.insert(0, line)
            tail_size += line_bytes

        head_content = "\n".join(head_lines)
        tail_content = "\n".join(tail_lines)

        # Check if line-based truncation captured enough content
        captured_bytes = len(head_content.encode("utf-8")) + len(
            tail_content.encode("utf-8")
        )
        min_useful = self.max_output_bytes * 0.2  # At least 20% of limit

        if captured_bytes < min_useful:
            # Byte-level fallback for very long lines (minified JSON, base64, etc.)
            head_content = self._extract_head_bytes(output, head_budget)
            tail_content = self._extract_tail_bytes(output, tail_budget)

            head_actual_bytes = len(head_content.encode("utf-8"))
            tail_actual_bytes = len(tail_content.encode("utf-8"))

            truncation_indicator = (
                f"\n\n[...OUTPUT TRUNCATED (byte-level)...]\n"
                f"[Showing first ~{head_actual_bytes:,} bytes and last ~{tail_actual_bytes:,} bytes]\n"
                f"[Total output: {original_bytes:,} bytes, limit: {self.max_output_bytes:,} bytes]\n"
                f"[Note: Line-based truncation failed (very long lines), using byte-level fallback]\n"
                f"[TIP: For large structured output, redirect to file and read portions]\n\n"
            )
        else:
            # Standard line-based truncation indicator
            truncation_indicator = (
                f"\n\n[...OUTPUT TRUNCATED...]\n"
                f"[Showing first {len(head_lines)} lines and last {len(tail_lines)} lines]\n"
                f"[Total output: {original_bytes:,} bytes, limit: {self.max_output_bytes:,} bytes]\n"
                f"[TIP: For large structured output (JSON/XML), redirect to file: command | Out-File output.txt]\n\n"
            )

        truncated = head_content + truncation_indicator + tail_content
        return truncated, True, original_bytes

    async def _run_command_background(
        self, command: str, pwsh_exe: str
    ) -> dict[str, Any]:
        """Run command in background, returning immediately with PID.

        The process is fully detached with:
        - New session so it's not killed when parent exits
        - Pipes redirected to /dev/null to prevent blocking
        - Returns immediately with PID for management

        Uses subprocess.Popen instead of asyncio.create_subprocess_* to avoid
        creating asyncio transports that would need cleanup. Since we're fully
        detaching the process anyway, we don't need asyncio's process management.
        This prevents "Event loop is closed" errors during session cleanup.
        """
        is_windows = sys.platform == "win32"
        devnull = subprocess.DEVNULL

        if is_windows:
            process = subprocess.Popen(
                [pwsh_exe, "-NoProfile", "-NonInteractive", "-Command", command],
                stdout=devnull,
                stderr=devnull,
                stdin=devnull,
                cwd=self.working_dir,
                creationflags=subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            # Unix-like: Use start_new_session to create new session, fully detached
            process = subprocess.Popen(
                [pwsh_exe, "-NoProfile", "-NonInteractive", "-Command", command],
                stdout=devnull,
                stderr=devnull,
                stdin=devnull,
                cwd=self.working_dir,
                start_new_session=True,  # Creates new session, detaches from terminal
            )

        return {"pid": process.pid}

    async def _run_command(
        self, command: str, pwsh_exe: str, timeout: int | None = None
    ) -> dict[str, Any]:
        """Run PowerShell command asynchronously and wait for completion.

        On Unix, uses process groups (start_new_session=True) for proper
        cleanup on timeout — kills the entire process tree, not just the
        top-level pwsh process.

        On Windows there is no equivalent of UNIX process groups accessible
        from asyncio.create_subprocess_exec, so on timeout only the top-level
        pwsh process is killed via process.kill(). Child processes spawned by
        the script may linger; use the 'unrestricted' profile with caution in
        long-running Windows scenarios.
        """
        is_windows = sys.platform == "win32"
        pgid = None

        # Build subprocess kwargs
        kwargs: dict[str, Any] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": self.working_dir,
        }

        if not is_windows:
            # start_new_session=True creates a new process group, enabling
            # us to kill the entire process tree on timeout (not just pwsh).
            # No Windows equivalent is available via asyncio subprocess.
            kwargs["start_new_session"] = True

        process = await asyncio.create_subprocess_exec(
            pwsh_exe,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
            **kwargs,
        )

        if not is_windows:
            # Get the process group ID (same as PID when start_new_session=True)
            pgid = process.pid

        # Wait for completion with timeout
        effective_timeout = timeout if timeout is not None else self.timeout
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=effective_timeout
            )

            return {
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
                "returncode": process.returncode,
            }

        except TimeoutError:
            # Kill the entire process group (all children) on Unix
            if pgid is not None and sys.platform != "win32":
                try:
                    # Send SIGTERM to process group first (graceful shutdown)
                    os.killpg(pgid, signal.SIGTERM)
                    # Give processes a moment to clean up
                    await asyncio.sleep(0.5)
                    # Force kill if still running
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass  # Already terminated
                except ProcessLookupError:
                    pass  # Process group already gone
                except PermissionError:
                    # Fall back to killing just the main process
                    process.kill()
            else:
                # Windows: use taskkill /T to kill the entire process tree
                if sys.platform == "win32" and process.pid is not None:
                    try:
                        # /T = kill child processes, /F = force, /PID = target
                        await asyncio.create_subprocess_exec(
                            "taskkill",
                            "/F",
                            "/T",
                            "/PID",
                            str(process.pid),
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                    except OSError:
                        # taskkill not available, fall back to process.kill()
                        process.kill()
                else:
                    process.kill()

            # Clean up
            try:
                await asyncio.wait_for(process.communicate(), timeout=5)
            except TimeoutError:
                pass  # Best effort cleanup
            raise
