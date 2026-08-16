"""Amplifier tool module: native PowerShell execution (the ``pwsh`` tool).

Amplifier's ``bash`` tool is the dominant tool call across real sessions,
but on Windows it resolves to WSL bash, Git Bash, or nothing at all --
none of which are the shell actually native to the machine. This module
registers a ``pwsh`` tool that runs PowerShell directly, so agents on
Windows have a shell that matches the host instead of an emulation layer
around it.

Design notes worth reading before touching this file:

    - ``encoding.py``  -- why the command payload is pinned to UTF-16LE
      base64, not guessed, and what happens when that guess is wrong.
    - ``runner.py``    -- the stdin-gated Job Object execution sequence,
      and the ``$?``/``$LASTEXITCODE`` -> single exit code mapping.
    - ``jobobject.py`` -- the raw Win32 ctypes calls, all with explicit
      restype/argtypes.
    - ``safety.py``    -- the four-profile safety layer, matching
      ``tool-bash``'s config surface exactly while covering the
      remote-script-execution gap measured absent there.
    - ``discovery.py`` -- pwsh vs Windows PowerShell resolution.

None of the Windows-only code (ctypes, Job Objects) executes at import
time -- only inside functions, and only when actually running a command on
``sys.platform == "win32"``. Importing this module is always safe on any
platform.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from amplifier_core import ModuleCoordinator, ToolResult

from .discovery import PowerShellExecutable, find_powershell
from .runner import ExecutionResult, PwshRunner
from .safety import SafetyConfig, SafetyValidator

logger = logging.getLogger(__name__)

# Default output limit: ~100KB, matching tool-bash's default so truncation
# behavior feels consistent between the two shells.
DEFAULT_MAX_OUTPUT_BYTES = 100_000
DEFAULT_TIMEOUT_SECONDS = 30


def _truncate_output(output: str, max_bytes: int) -> tuple[str, bool, int]:
    """Truncate `output` to at most `max_bytes` UTF-8 bytes.

    Returns (possibly-truncated text, was_truncated, total_original_bytes).
    Truncates at a valid UTF-8 boundary so multi-byte characters are never
    split into invalid fragments.
    """
    encoded = output.encode("utf-8")
    if len(encoded) <= max_bytes:
        return output, False, len(encoded)

    truncated = encoded[:max_bytes]
    for cut in range(len(truncated), max(0, len(truncated) - 4), -1):
        try:
            text = truncated[:cut].decode("utf-8")
            return (
                text + f"\n[...truncated: {len(encoded) - cut} more bytes...]",
                True,
                len(encoded),
            )
        except UnicodeDecodeError:
            continue
    return "", True, len(encoded)


async def mount(coordinator: ModuleCoordinator, config: dict[str, Any] | None = None):
    """Mount the pwsh tool.

    Args:
        coordinator: Module coordinator.
        config: Tool configuration.
            - working_dir: Working directory for command execution.
              Falls back to the session.working_dir capability, then ".".
            - timeout: Command timeout in seconds (default: 30).
            - safety_profile: One of "strict" (default), "standard",
              "permissive", "unrestricted" -- same names as tool-bash.
            - allowed_commands: Whitelist of allowed command patterns
              (supports * wildcards) -- same key as tool-bash.
            - denied_commands: Additional custom blocklist patterns --
              same key as tool-bash.
            - safety_overrides: Fine-grained {"allow": [...], "block": [...]}
              overrides -- same shape as tool-bash.
            - max_output_bytes: Output truncation limit (default: 100_000).

    Returns:
        None (no cleanup resources held beyond the process-wide Job Object,
        which is cleaned up by Windows itself on process/job-handle close).
    """
    config = config or {}

    if "working_dir" not in config:
        working_dir = coordinator.get_capability("session.working_dir")
        if working_dir:
            config = {**config, "working_dir": working_dir}

    tool = PwshTool(config)
    await coordinator.mount("tools", tool, name=tool.name)
    logger.info("Mounted PwshTool (executable=%s)", tool.executable)
    return None


class PwshTool:
    """Execute PowerShell commands natively (Windows-first, cross-platform-capable)."""

    name = "pwsh"

    BASE_DESCRIPTION = """
Execute PowerShell commands. This is the native shell on Windows -- prefer
it over `bash` there, since `bash` on Windows resolves to WSL, Git Bash, or
nothing, none of which is the shell actually installed on the machine.

WHEN TO USE PWSH:
- Any Windows-native operation: filesystem, registry, services, processes
- .NET-backed scripting and object-pipeline queries (Get-Process, Get-Item)
- Anything the equivalent bash command would only work via an emulation layer

EXIT CODE SEMANTICS:
- A native command's own exit code takes precedence when set ($LASTEXITCODE)
- Otherwise, cmdlet-level success/failure ($?) maps to 0/1
- An explicit `exit N` inside your command always wins outright

OUTPUT LIMITS:
- Long outputs are automatically truncated to prevent context overflow
- When truncated, you will see a trailing note naming how many bytes were cut

SAFETY:
- Destructive commands (registry hive deletion, disk formatting, recursive
  deletion of drive roots or the home directory, execution-policy bypass,
  `-Verb RunAs` elevation, and remote-script-execution idioms such as
  `Invoke-WebRequest ... | Invoke-Expression`) are blocked by default
- Commands requiring interactive input are not supported
"""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.timeout = config.get("timeout", DEFAULT_TIMEOUT_SECONDS)
        self.working_dir = config.get("working_dir")
        self.max_output_bytes = config.get("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES)

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

        self.executable: PowerShellExecutable | None = find_powershell()
        self._runner: PwshRunner | None = (
            PwshRunner(self.executable, working_dir=self.working_dir)
            if self.executable is not None
            else None
        )
        self._background_processes: dict[int, Any] = {}

        # Plain instance attribute (not a property) -- same pattern
        # tool-bash uses for its Windows-shell startup note: assembled
        # once here, at mount time.
        self.description: str = self.BASE_DESCRIPTION + self._availability_note()

    def _availability_note(self) -> str:
        """Loud, actionable note on PowerShell availability.

        Appended to the tool description (an instance property, so this
        is assembled once at mount time -- same mechanism tool-bash uses
        for its Windows-shell startup note) so a model learns the tool is
        unavailable, and exactly why, BEFORE its first call -- rather than
        discovering it only after a confusing failure.
        """
        if self.executable is not None:
            return f"\n\nRESOLVED SHELL: {self.executable.edition} ({self.executable.path})"

        if sys.platform == "win32":
            return (
                "\n\nPOWERSHELL NOT FOUND: neither pwsh.exe nor powershell.exe was "
                "found on PATH. Every call to this tool will fail with this exact "
                "message. Install PowerShell 7 (https://aka.ms/powershell) or use "
                "the `bash` tool instead."
            )
        return (
            "\n\nPOWERSHELL NOT FOUND: this host is not Windows and no `pwsh` "
            "executable is on PATH. Every call to this tool will fail with this "
            "exact message. Use the `bash` tool on this platform instead."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "PowerShell command or script to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Command timeout in seconds (default: 30). Increase for "
                        "builds, tests, or monitoring. Use run_in_background for "
                        "truly indefinite processes."
                    ),
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": (
                        "Run command in background, returning immediately with a PID."
                    ),
                    "default": False,
                },
            },
            "required": ["command"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        command = input.get("command")
        if not command:
            error_msg = "Command is required"
            return ToolResult(
                success=False, output=error_msg, error={"message": error_msg}
            )

        if self.executable is None or self._runner is None:
            error_msg = self._availability_note().strip()
            return ToolResult(
                success=False, output=error_msg, error={"message": error_msg}
            )

        safety_result = self._safety_validator.validate(command)
        if not safety_result.allowed:
            error_msg = f"Command denied for safety: {safety_result.reason}"
            if safety_result.hint:
                error_msg += f"\n  Hint: {safety_result.hint}"
            return ToolResult(
                success=False, output=error_msg, error={"message": error_msg}
            )

        timeout = input.get("timeout", self.timeout)
        run_in_background = input.get("run_in_background", False)

        try:
            if run_in_background:
                pid = await self._run_background(command)
                return ToolResult(
                    success=True,
                    output={
                        "pid": pid,
                        "message": f"Command started in background with PID {pid}",
                    },
                )

            result = await asyncio.to_thread(self._runner.run, command, timeout)
            return self._to_tool_result(result, timeout)
        except Exception as exc:
            logger.error("tool-pwsh: command execution error: %s", exc)
            return ToolResult(
                success=False, output=str(exc), error={"message": str(exc)}
            )

    def _to_tool_result(self, result: ExecutionResult, timeout: float) -> ToolResult:
        if result.timed_out:
            error_msg = f"Command timed out after {timeout} seconds"
            return ToolResult(
                success=False, output=error_msg, error={"message": error_msg}
            )

        stdout, stdout_truncated, stdout_bytes = _truncate_output(
            result.stdout, self.max_output_bytes
        )
        stderr, stderr_truncated, stderr_bytes = _truncate_output(
            result.stderr, self.max_output_bytes
        )

        output: dict[str, Any] = {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": result.exit_code,
        }
        if stdout_truncated or stderr_truncated:
            output["truncated"] = True
            if stdout_truncated:
                output["stdout_total_bytes"] = stdout_bytes
            if stderr_truncated:
                output["stderr_total_bytes"] = stderr_bytes

        # ToolResult.success mirrors tool-bash's meaning exactly: exit code
        # zero is success, anything else is failure. The runner has
        # already resolved PowerShell's two distinct failure notions
        # ($? vs $LASTEXITCODE) down to this single exit_code -- the model
        # never has to reason about the platform-specific subtlety.
        return ToolResult(success=result.success, output=output)

    async def _run_background(self, command: str) -> int:
        """Launch `command` without waiting for completion; return its PID.

        Deliberately minimal: this module's scope is the foreground
        execution/safety/encoding contract. Background execution here does
        not offer bash tool's log-polling ergonomics -- it exists so
        long-running processes (dev servers, watchers) are not forced
        through the foreground timeout path.
        """
        import subprocess

        from .encoding import encode_command
        from .runner import RUNNER_SCRIPT

        assert self.executable is not None
        runner_encoded = encode_command(RUNNER_SCRIPT)
        args = [
            self.executable.path,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            runner_encoded,
        ]
        proc: subprocess.Popen[bytes] = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=self.working_dir,
            text=False,
        )

        if sys.platform == "win32":
            from .jobobject import assign_to_job

            assign_to_job(proc.pid)

        payload = (encode_command(command) + "\n").encode("ascii")
        assert proc.stdin is not None
        proc.stdin.write(payload)
        proc.stdin.close()

        self._background_processes[proc.pid] = proc
        return proc.pid
