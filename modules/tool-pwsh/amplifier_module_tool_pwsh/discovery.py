"""Discovery of a PowerShell executable to run commands with.

Resolution order, preferring PowerShell 7+ (Core) over Windows PowerShell:

    1. ``pwsh.exe`` / ``pwsh`` on PATH   -> PowerShell 7+ (Core)
    2. ``powershell.exe`` on PATH         -> Windows PowerShell 5.1

Both editions were confirmed present on a real Windows 11 test host, with
``pwsh`` resolving first (matching this order)::

    pwsh.exe        C:\\Program Files\\PowerShell\\7\\pwsh.exe
    powershell.exe  C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe

PowerShell 7 is itself cross-platform (installable on Linux/macOS), so this
module performs no platform gating of its own -- it just reports what
``shutil.which`` finds. Callers decide what "nothing found" means for their
platform (see ``__init__.PwshTool`` for the loud, actionable degradation
message required when this returns ``None`` on a platform where a
PowerShell install would normally be expected).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class PowerShellExecutable:
    """A resolved PowerShell executable."""

    path: str
    is_core: bool  # True: pwsh (PowerShell 7+/Core). False: Windows PowerShell 5.1.

    @property
    def edition(self) -> str:
        return (
            "PowerShell 7+ (pwsh)"
            if self.is_core
            else "Windows PowerShell 5.1 (powershell.exe)"
        )


def find_powershell() -> PowerShellExecutable | None:
    """Locate a PowerShell executable on PATH, preferring pwsh (7+).

    Returns:
        The first matching :class:`PowerShellExecutable`, or ``None`` if
        neither ``pwsh`` nor ``powershell`` can be found on PATH.
    """
    pwsh_path = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if pwsh_path:
        return PowerShellExecutable(path=pwsh_path, is_core=True)

    powershell_path = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell_path:
        return PowerShellExecutable(path=powershell_path, is_core=False)

    return None
