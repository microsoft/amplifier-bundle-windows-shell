"""Windows Job Object containment for the pwsh tool.

A Win32 Job Object lets us treat "the pwsh process plus everything it
spawns" as a single unit: assign a process to the job, and closing the job
handle (or an explicit ``TerminateJobObject`` call) tears down every
process in it at once -- including descendants the pwsh process itself
spawned, not just the one PID we launched.

This module is deliberately narrow: create-once job handle, assign-a-pid,
terminate-everything. The *sequencing* that makes assignment race-free
(assign before any user-controlled code can run) lives in ``runner.py``,
which is the caller of ``assign_to_job``.

Every ctypes call here declares explicit ``restype``/``argtypes``. Leaving
either undeclared makes ctypes default to C ``int`` (32-bit signed), which
truncates a 64-bit ``HANDLE`` on 64-bit Windows -- a bug that looks fine in
review and only misbehaves on a real 64-bit box. See
``tests/test_jobobject_ctypes_signatures.py`` for the regression guard
(modeled on amplifier-module-tool-bash's equivalent test, which is where
this exact hazard was first found and fixed).

Design note on breakaway children (see docs/EVIDENCE.md / ROADMAP.md in the
parent bundle): a bare Job Object -- neither ``JOB_OBJECT_LIMIT_BREAKAWAY_OK``
nor ``JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK`` set -- was measured on a real
Windows 11 host to still contain a grandchild spawned with
``CREATE_BREAKAWAY_FROM_JOB``. That matches Microsoft's own documentation:
setting *neither* breakaway limit is the safe configuration. We therefore
set only ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` and touch no breakaway bit.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Any

logger = logging.getLogger(__name__)

# Lazily created, process-wide job handle plus the lock guarding its
# creation. Mirrors amplifier-module-tool-bash's pattern for the same
# reason: one job per process is enough, and creation must not race.
_job_handle: Any = None
_job_lock: threading.Lock | None = None

# JOBOBJECT_EXTENDED_LIMIT_INFORMATION class (JobObjectInfo class ordinal 9)
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_PROCESS_ALL_ACCESS = 0x1F0FFF


def _build_job_structs(ctypes_mod: Any) -> tuple[type, type]:
    """Build the JOBOBJECT_* ctypes.Structure classes.

    Split out so both ``create_job_object`` and the test suite can
    construct the exact same layout without duplicating the field lists.
    Field order/types follow winnt.h.
    """
    from ctypes import wintypes

    class IO_COUNTERS(ctypes_mod.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes_mod.c_uint64),
            ("WriteOperationCount", ctypes_mod.c_uint64),
            ("OtherOperationCount", ctypes_mod.c_uint64),
            ("ReadTransferCount", ctypes_mod.c_uint64),
            ("WriteTransferCount", ctypes_mod.c_uint64),
            ("OtherTransferCount", ctypes_mod.c_uint64),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes_mod.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes_mod.c_int64),
            ("PerJobUserTimeLimit", ctypes_mod.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes_mod.c_size_t),
            ("MaximumWorkingSetSize", ctypes_mod.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes_mod.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes_mod.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes_mod.c_size_t),
            ("JobMemoryLimit", ctypes_mod.c_size_t),
            ("PeakProcessMemoryUsed", ctypes_mod.c_size_t),
            ("PeakJobMemoryUsed", ctypes_mod.c_size_t),
        ]

    return JOBOBJECT_BASIC_LIMIT_INFORMATION, JOBOBJECT_EXTENDED_LIMIT_INFORMATION


def create_job_object() -> Any:
    """Create (or return the cached) kill-on-close Job Object.

    Returns the job handle, or ``None`` if creation failed for any reason.
    Callers MUST treat ``None`` as "no extra containment available, proceed
    without it" -- this is defense-in-depth on top of the normal
    process-exit/timeout handling, not a correctness requirement for a
    command to run. A pwsh install with unusual security-agent
    restrictions that blocks job-object creation should still be able to
    execute commands; it just loses the guaranteed-cleanup property.

    Not Windows-specific by construction check here (that's the caller's
    job) so this can be exercised on any platform with a mocked
    ``ctypes.WinDLL`` -- see the ctypes-signature regression test.
    """
    global _job_handle, _job_lock
    if _job_lock is None:
        _job_lock = threading.Lock()
    with _job_lock:
        if _job_handle is not None:
            return _job_handle
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
            kernel32.SetInformationJobObject.restype = wintypes.BOOL
            kernel32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                wintypes.LPVOID,
                wintypes.DWORD,
            ]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                logger.warning(
                    "tool-pwsh: CreateJobObjectW failed (%s); proceeding without "
                    "Job Object containment",
                    ctypes.WinError(ctypes.get_last_error()),
                )
                return None

            _, extended_info_cls = _build_job_structs(ctypes)
            info = extended_info_cls()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

            ok = kernel32.SetInformationJobObject(
                job,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not ok:
                logger.warning(
                    "tool-pwsh: SetInformationJobObject failed (%s); proceeding "
                    "without Job Object containment",
                    ctypes.WinError(ctypes.get_last_error()),
                )
                kernel32.CloseHandle(job)
                return None

            _job_handle = job
            return job
        except Exception as exc:  # pragma: no cover - defense in depth only
            logger.debug(
                "tool-pwsh: Windows job-object setup failed (%s); proceeding "
                "without orphan protection",
                exc,
            )
            return None


def assign_to_job(pid: int) -> bool:
    """Best-effort: assign process ``pid`` to the process-wide job object.

    Returns whether assignment actually succeeded. Callers that only need
    "did I do my best" semantics can ignore the return value; failure is
    logged at debug level (process already exited is the overwhelmingly
    common, benign cause) and never raises -- this is defense-in-depth
    cleanup, not a requirement for the command itself to run.
    """
    job = create_job_object()
    if job is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        hproc = kernel32.OpenProcess(_PROCESS_ALL_ACCESS, False, pid)
        if not hproc:
            logger.debug(
                "tool-pwsh: OpenProcess(%s) failed (%s); pid not job-protected "
                "(usually means it already exited)",
                pid,
                ctypes.WinError(ctypes.get_last_error()),
            )
            return False
        try:
            if not kernel32.AssignProcessToJobObject(job, hproc):
                logger.debug(
                    "tool-pwsh: AssignProcessToJobObject(pid=%s) failed (%s)",
                    pid,
                    ctypes.WinError(ctypes.get_last_error()),
                )
                return False
            return True
        finally:
            kernel32.CloseHandle(hproc)
    except Exception as exc:  # pragma: no cover - defense in depth only
        logger.debug("tool-pwsh: failed to job-protect pid %s (%s)", pid, exc)
        return False


def terminate_job() -> bool:
    """Terminate every process currently in the process-wide job object.

    Used on timeout: killing the job kills the pwsh process *and* any
    descendants it spawned in one call, which is the entire reason to use
    a Job Object over a plain ``process.kill()`` -- a naive kill only ever
    reaches the one PID we started, not anything it spawned.

    Returns whether termination was attempted and reported success; a
    ``False`` return (job never created, or the call failed) means the
    caller should fall back to killing the single known PID directly.
    """
    if _job_handle is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]

        return bool(kernel32.TerminateJobObject(_job_handle, 1))
    except Exception as exc:  # pragma: no cover - defense in depth only
        logger.debug("tool-pwsh: TerminateJobObject failed (%s)", exc)
        return False


def _reset_for_tests() -> None:
    """Test-only: clear the module-level job handle cache.

    Not part of the public contract -- exists so the test suite can
    exercise ``create_job_object`` repeatedly against different mocked
    ``kernel32`` instances without leaking state between tests.
    """
    global _job_handle
    _job_handle = None


if sys.platform != "win32":
    # No behavior change on import for non-Windows platforms -- this module
    # only ever calls into ctypes.WinDLL from inside the functions above,
    # never at import time, so importing it is always safe everywhere.
    pass
