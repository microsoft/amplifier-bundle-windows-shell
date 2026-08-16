"""Live, end-to-end execution tests against a real PowerShell process.

Windows-only by construction (`skipif` below) -- these are the tests that
must actually run on the platform they guard, not merely compile. A
platform-skipped test passes vacuously everywhere else; two Windows
regression tests elsewhere in this effort sat green on Linux for days and
failed the first time they actually ran on Windows. Parametrized across
both PowerShell editions (pwsh and Windows PowerShell), skipping
individually when one is absent -- both are present on a
`windows-latest` GitHub Actions runner, so this genuinely executes there
rather than skipping wholesale.

Mirrors the 7/7 case shape measured directly in docs/EVIDENCE.md (parent
bundle) so the module's own test suite proves the same semantics the
design relies on, rather than trusting the earlier spike harness alone.

Honesty note (see the task report): this file could not be executed in
the environment this module was authored in -- no Windows host and no
`pwsh`/`powershell.exe` binary were available (verified: `shutil.which`
returns nothing for either name here). It is written to run for real on
CI/Windows, following exactly the pattern in docs/ROADMAP.md item 5, but
its PASS/FAIL status on real Windows is unverified by this session.
"""

from __future__ import annotations

import shutil
import sys

import pytest
from amplifier_module_tool_pwsh.discovery import PowerShellExecutable
from amplifier_module_tool_pwsh.runner import PwshRunner

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="live PowerShell execution requires Windows"
)


def _available_editions() -> list[str]:
    editions = []
    if shutil.which("pwsh.exe") or shutil.which("pwsh"):
        editions.append("pwsh")
    if shutil.which("powershell.exe"):
        editions.append("powershell")
    return editions


@pytest.fixture(params=["pwsh", "powershell"])
def executable(request) -> PowerShellExecutable:
    edition = request.param
    if edition not in _available_editions():
        pytest.skip(f"{edition} not installed on this host")
    if edition == "pwsh":
        path = shutil.which("pwsh.exe") or shutil.which("pwsh")
        is_core = True
    else:
        path = shutil.which("powershell.exe")
        is_core = False
    assert path is not None
    return PowerShellExecutable(path=path, is_core=is_core)


class TestLiveExitCodeSemantics:
    """Mirrors docs/EVIDENCE.md's 7/7 measured case table exactly."""

    def test_hello(self, executable: PowerShellExecutable) -> None:
        result = PwshRunner(executable).run("Write-Output 'PS_SPIKE_OK'", timeout=15)
        assert result.exit_code == 0
        assert "PS_SPIKE_OK" in result.stdout

    def test_native_exit_code_preserved(self, executable: PowerShellExecutable) -> None:
        result = PwshRunner(executable).run("cmd /c exit 42", timeout=15)
        assert result.exit_code == 42

    def test_cmdlet_failure_maps_to_one(self, executable: PowerShellExecutable) -> None:
        result = PwshRunner(executable).run(
            "Get-Item -Path 'Z:\\this\\path\\does\\not\\exist' -ErrorAction SilentlyContinue",
            timeout=15,
        )
        assert result.exit_code == 1

    def test_handled_failure_genuinely_succeeds(
        self, executable: PowerShellExecutable
    ) -> None:
        result = PwshRunner(executable).run(
            "try { throw 'boom' } catch {}; exit 0", timeout=15
        )
        assert result.exit_code == 0

    def test_stderr_is_captured(self, executable: PowerShellExecutable) -> None:
        result = PwshRunner(executable).run(
            "[Console]::Error.WriteLine('err-line')", timeout=15
        )
        assert "err-line" in result.stderr

    def test_non_ascii_round_trips(self, executable: PowerShellExecutable) -> None:
        result = PwshRunner(executable).run("Write-Output 'café-日本-🚀'", timeout=15)
        assert result.exit_code == 0
        assert "café-日本-🚀" in result.stdout

    def test_nested_quoting_survives(self, executable: PowerShellExecutable) -> None:
        result = PwshRunner(executable).run('Write-Output "a\'b`"c"', timeout=15)
        assert result.exit_code == 0
