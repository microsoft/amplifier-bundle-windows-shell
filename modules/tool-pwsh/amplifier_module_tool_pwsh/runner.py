"""Execution engine for the pwsh tool.

Two concerns live here that don't belong in the Tool class itself:

1. **Exit-code semantics.** PowerShell has two distinct notions of
   failure -- ``$?`` (did the last cmdlet report success) and
   ``$LASTEXITCODE`` (the last *native* process's exit code). Collapsing
   them misreports outcomes. Measured on a real Windows 11 host (7/7 cases
   passed, see docs/EVIDENCE.md in the parent bundle):

       native exit code preserved   -- ``cmd /c exit 42`` -> rc=42
       cmdlet failure                -- non-terminating error -> rc=1
       handled failure genuinely ok  -- ``try{...}catch{}; exit 0`` -> rc=0

   ``RUNNER_SCRIPT`` below encodes exactly this priority: if
   ``$LASTEXITCODE`` was set by a native command, that value wins (native
   exit takes precedence over cmdlet-level $?); otherwise ``$?`` decides
   0 vs 1. An explicit ``exit N`` inside the user's own command always
   short-circuits this -- ``exit`` terminates the whole PowerShell process
   immediately, which is exactly the "handled failure; exit 0" case.

2. **Job Object assignment sequencing (Windows only).** The runner script
   blocks on ``[Console]::In.ReadLine()`` as its very first executable
   statement -- before it has decoded or executed a single byte of the
   user's command. We assign the spawned process to the Job Object BEFORE
   writing anything to its stdin. This means no user-controlled code can
   possibly run before job assignment completes: there is no window to
   race, because the thing that would need to escape the job (a child
   process spawned by the user's command) cannot exist yet. This is
   stronger than "assign quickly, then sweep for descendants" -- it
   removes the race rather than narrowing it.

The runner script's own source is fixed, authored by us, and contains zero
user input -- only the user's command travels through the stdin pipe (as
UTF-16LE-base64, matching PowerShell's own ``-EncodedCommand`` convention;
see ``encoding.py``). The runner script itself is passed to pwsh via
``-EncodedCommand`` too, for the same reason every other command payload
in this module is: guessing an ambient default encoding is exactly the bug
class this module exists to avoid.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass

from .discovery import PowerShellExecutable
from .encoding import encode_command
from .jobobject import assign_to_job, terminate_job

logger = logging.getLogger(__name__)

# Fixed, trusted PowerShell source for the stdin-gated runner. Contains NO
# user input -- the user's command arrives later, over stdin, as a
# base64/UTF-16LE payload this script decodes itself. See module docstring
# for the exit-code mapping and the Job Object sequencing this exists for.
RUNNER_SCRIPT = r"""
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$ErrorActionPreference = 'Continue'
$line = [Console]::In.ReadLine()
if ($null -eq $line) {
    exit 1
}
$bytes = [Convert]::FromBase64String($line)
$decoded = [System.Text.Encoding]::Unicode.GetString($bytes)
$sb = [scriptblock]::Create($decoded)
$global:LASTEXITCODE = $null
$global:__ok = $true
$global:__lec = $null
& $sb
$__ok = $global:__ok
$__lec = $global:__lec
if (-not $__ok) {
    if ($null -ne $__lec -and $__lec -ne 0) {
        exit $__lec
    }
    exit 1
}
if ($null -ne $__lec) {
    exit $__lec
}
exit 0
""".strip()

# Appended to the user's command before encoding, so `$?` is read INSIDE the
# scriptblock scope on the statement immediately after the user's last one.
#
# Reading `$?` in the runner after `& $sb` does not work: it reports whether
# the *scriptblock invocation* succeeded, and a non-terminating error inside
# (the default under `$ErrorActionPreference = 'Continue'`) still counts as a
# successful invocation. Measured on Windows 11 -- a cmdlet failure reported
# exit 0 on BOTH pwsh 7 and powershell 5.1 until the capture moved in here.
_CAPTURE_SUFFIX = "\n$global:__ok = $?\n$global:__lec = $LASTEXITCODE"


def instrument_command(command: str) -> str:
    """Append the `$?` / `$LASTEXITCODE` capture to a user command.

    A command containing an explicit `exit` terminates the process before the
    capture runs -- that is correct, the explicit exit code IS the answer.
    """
    return command + _CAPTURE_SUFFIX


@dataclass
class ExecutionResult:
    """Result of running a command through the pwsh runner."""

    success: bool
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    job_protected: bool = False


class PwshRunner:
    """Runs PowerShell commands via the stdin-gated Job Object runner.

    One instance per tool configuration (working directory, timeout). Each
    call to :meth:`run` spawns a brand-new pwsh process -- there is no
    persistent session state across calls, so ``$LASTEXITCODE``/``$?``
    always start clean for every command.
    """

    def __init__(
        self,
        executable: PowerShellExecutable,
        working_dir: str | None = None,
    ) -> None:
        self.executable = executable
        self.working_dir = working_dir

    def run(self, command: str, timeout: float) -> ExecutionResult:
        """Run ``command`` synchronously, enforcing ``timeout`` seconds.

        Intended to be called from a worker thread (e.g. via
        ``asyncio.to_thread``) since it blocks on ``subprocess`` I/O.
        """
        runner_encoded = encode_command(RUNNER_SCRIPT)
        args = [
            self.executable.path,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            runner_encoded,
        ]

        # POSIX gets start_new_session=True (a timeout kill can then reach
        # the whole process group -- same spirit as the Windows Job Object,
        # though weaker: no guaranteed-atomic kill). On Windows, cleanup
        # instead goes through the Job Object (TerminateJobObject kills
        # everything it contains at once), so no extra process-group flag
        # is needed there. Two explicit branches (rather than merging a
        # kwargs dict) so the Popen call stays a single, staticly-typed
        # bytes-mode invocation -- Popen's AnyStr generic is invariant, and
        # letting `text=`/`start_new_session=` flow through an untyped
        # dict made the return type ambiguous between Popen[str]/Popen[bytes].
        proc: subprocess.Popen[bytes]
        if sys.platform == "win32":
            proc = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.working_dir,
                text=False,
            )
        else:
            proc = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.working_dir,
                text=False,
                start_new_session=True,
            )

        job_protected = False
        if sys.platform == "win32":
            # Assign to the Job Object BEFORE writing the payload to stdin
            # -- see module docstring. The runner cannot have executed any
            # user code yet; it is still blocked on ReadLine.
            job_protected = assign_to_job(proc.pid)

        payload = encode_command(instrument_command(command)) + "\n"

        try:
            assert proc.stdin is not None
            stdout_bytes, stderr_bytes = self._communicate(proc, payload, timeout)
        except subprocess.TimeoutExpired:
            self._kill(proc, job_protected)
            # Drain whatever already arrived so a slow-but-real command
            # doesn't get reported with empty output on timeout.
            try:
                stdout_bytes, stderr_bytes = proc.communicate(timeout=1.0)
            except Exception:
                stdout_bytes, stderr_bytes = b"", b""
            return ExecutionResult(
                success=False,
                exit_code=None,
                stdout=stdout_bytes.decode("utf-8", errors="replace"),
                stderr=stderr_bytes.decode("utf-8", errors="replace"),
                timed_out=True,
                job_protected=job_protected,
            )

        exit_code = proc.returncode
        return ExecutionResult(
            success=exit_code == 0,
            exit_code=exit_code,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            job_protected=job_protected,
        )

    def _communicate(
        self, proc: subprocess.Popen, payload: str, timeout: float
    ) -> tuple[bytes, bytes]:
        return proc.communicate(input=payload.encode("ascii"), timeout=timeout)

    def _kill(self, proc: subprocess.Popen, job_protected: bool) -> None:
        """Best-effort teardown on timeout.

        Prefers killing the whole Job Object (every process it contains,
        including anything the user's command spawned) over killing only
        the one PID we started -- that is the entire point of using a Job
        Object instead of a bare ``process.kill()``.
        """
        killed_via_job = job_protected and sys.platform == "win32" and terminate_job()
        if not killed_via_job:
            try:
                proc.kill()
            except Exception as exc:  # pragma: no cover - defense in depth
                logger.debug("tool-pwsh: failed to kill timed-out process (%s)", exc)
