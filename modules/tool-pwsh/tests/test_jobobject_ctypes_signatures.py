"""Regression test: every Win32 ctypes call site declares argtypes/restype.

ctypes silently defaults an undeclared `restype`/`argtypes` to C `int`
(32-bit signed). On 64-bit Windows, a HANDLE or pointer-sized value routed
through that default gets truncated -- producing wrong results or memory
corruption rather than a clean error. This is exactly the kind of bug that
looks fine in review and only misbehaves on a real 64-bit Windows box.

`ctypes.WinDLL` does not exist as a real attribute on non-Windows platforms
(hence `create=True` below), but `ctypes.wintypes` is plain, portable
`ctypes.Structure`/`c_*` aliasing that imports and behaves identically on
any platform. That means the real Windows-only functions under test run
for real here -- only the DLL handle itself is a stand-in -- so this test
drives the genuine signature-declaration code, not a paraphrase of it.

A load-bearing detail this file is deliberately careful about: a bare
``MagicMock()`` auto-vivifies every attribute on first access, so
``mock.SomeFunc.restype`` is *never* ``None`` even if the code under test
never assigns it -- ``assert fn.restype is not None`` against a plain
MagicMock is a test that cannot fail, regardless of whether ``jobobject.py``
actually declares the signature. Verified directly::

    >>> from unittest.mock import MagicMock
    >>> MagicMock().Foo.restype is not None
    True

So every ``kernel32`` mock here has ``.restype``/``.argtypes`` explicitly
pre-set to ``None`` on each function *before* the code under test runs.
If ``jobobject.py`` ever stops declaring a signature, the corresponding
attribute stays at that pre-set ``None`` and the assertion genuinely
fails -- this was verified by deliberately reverting the restype/argtypes
declarations in ``jobobject.py`` and confirming these exact tests turned
red (see the task report for the full mutation-testing numbers).
"""

from __future__ import annotations

import ctypes
from unittest.mock import MagicMock, patch

import amplifier_module_tool_pwsh.jobobject as jobobject


def _kernel32_with_nulled_signatures(function_names: tuple[str, ...]) -> MagicMock:
    """A kernel32 mock whose named functions start with restype/argtypes
    explicitly set to None -- so the "was a signature ever declared"
    assertion below is falsifiable rather than trivially always-true.
    """
    kernel32 = MagicMock(name="kernel32")
    for fn_name in function_names:
        fn = getattr(kernel32, fn_name)
        fn.restype = None
        fn.argtypes = None
    return kernel32


def _assert_signature_declared(kernel32_mock: MagicMock, name: str) -> None:
    fn = getattr(kernel32_mock, name)
    assert fn.argtypes is not None, (
        f"kernel32.{name} has no argtypes declared -- ctypes will default "
        "arguments to C int, truncating a 64-bit pointer/HANDLE on 64-bit "
        "Windows"
    )
    assert fn.restype is not None, (
        f"kernel32.{name} has no restype declared -- ctypes defaults the "
        "return value to C int, truncating a 64-bit HANDLE on 64-bit Windows"
    )


class TestJobObjectCtypesSignatures:
    def setup_method(self) -> None:
        jobobject._reset_for_tests()

    def teardown_method(self) -> None:
        jobobject._reset_for_tests()

    def test_create_job_object_declares_signatures(self) -> None:
        names = ("CreateJobObjectW", "SetInformationJobObject", "CloseHandle")
        kernel32 = _kernel32_with_nulled_signatures(names)
        windll_factory = MagicMock(return_value=kernel32)

        with patch.object(ctypes, "WinDLL", windll_factory, create=True):
            job = jobobject.create_job_object()

        assert job is not None, (
            "job creation must succeed against a mocked, all-truthy kernel32"
        )
        for name in names:
            _assert_signature_declared(kernel32, name)

    def test_assign_to_job_declares_signatures(self) -> None:
        names = ("OpenProcess", "AssignProcessToJobObject", "CloseHandle")
        kernel32 = _kernel32_with_nulled_signatures(names)
        windll_factory = MagicMock(return_value=kernel32)

        with patch.object(ctypes, "WinDLL", windll_factory, create=True):
            assigned = jobobject.assign_to_job(4242)

        assert assigned is True, (
            "assignment must succeed against a mocked, all-truthy kernel32"
        )
        for name in names:
            _assert_signature_declared(kernel32, name)

    def test_terminate_job_declares_signatures(self) -> None:
        kernel32 = _kernel32_with_nulled_signatures(
            (
                "CreateJobObjectW",
                "SetInformationJobObject",
                "CloseHandle",
                "TerminateJobObject",
            )
        )
        windll_factory = MagicMock(return_value=kernel32)

        with patch.object(ctypes, "WinDLL", windll_factory, create=True):
            jobobject.create_job_object()  # populate _job_handle first
            result = jobobject.terminate_job()

        assert result is True
        _assert_signature_declared(kernel32, "TerminateJobObject")

    def test_create_job_object_reports_failure_gracefully_not_raising(self) -> None:
        """CreateJobObjectW returning a falsy/NULL handle must be treated
        as "no protection available", never raise up through the caller --
        this is defense-in-depth, not a requirement for command execution.
        """
        kernel32 = MagicMock(name="kernel32")
        kernel32.CreateJobObjectW.return_value = None
        windll_factory = MagicMock(return_value=kernel32)

        with patch.object(ctypes, "WinDLL", windll_factory, create=True):
            job = jobobject.create_job_object()

        assert job is None

    def test_assign_to_job_returns_false_when_job_creation_failed(self) -> None:
        kernel32 = MagicMock(name="kernel32")
        kernel32.CreateJobObjectW.return_value = None
        windll_factory = MagicMock(return_value=kernel32)

        with patch.object(ctypes, "WinDLL", windll_factory, create=True):
            assigned = jobobject.assign_to_job(1234)

        assert assigned is False

    def test_assign_to_job_returns_false_when_open_process_fails(self) -> None:
        """The dominant real-world cause: the process already exited
        between spawn and assignment (benign for fast commands) -- must
        return False, not raise.
        """
        kernel32 = MagicMock(name="kernel32")
        kernel32.OpenProcess.return_value = None
        windll_factory = MagicMock(return_value=kernel32)

        with patch.object(ctypes, "WinDLL", windll_factory, create=True):
            assigned = jobobject.assign_to_job(1234)

        assert assigned is False

    def test_job_handle_is_cached_across_calls(self) -> None:
        """CreateJobObjectW must only be called once per process -- a
        second call to create_job_object() reuses the cached handle.
        """
        kernel32 = MagicMock(name="kernel32")
        windll_factory = MagicMock(return_value=kernel32)

        with patch.object(ctypes, "WinDLL", windll_factory, create=True):
            first = jobobject.create_job_object()
            second = jobobject.create_job_object()

        assert first is second
        assert kernel32.CreateJobObjectW.call_count == 1
