"""Tests for PowerShell executable discovery (pwsh vs powershell.exe).

Pure `shutil.which` wrapping -- mocked here, runs on every platform.
"""

from __future__ import annotations

from unittest.mock import patch

from amplifier_module_tool_pwsh.discovery import find_powershell


def _which_map(mapping: dict[str, str]):
    def fake_which(name: str) -> str | None:
        return mapping.get(name)

    return fake_which


class TestDiscoveryPreference:
    def test_prefers_pwsh_over_windows_powershell_when_both_present(self) -> None:
        mapping = {
            "pwsh.exe": r"C:\Program Files\PowerShell\7\pwsh.exe",
            "powershell.exe": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        }
        with patch("shutil.which", side_effect=_which_map(mapping)):
            result = find_powershell()

        assert result is not None
        assert result.is_core is True
        assert result.path == mapping["pwsh.exe"]
        assert "PowerShell 7" in result.edition

    def test_falls_back_to_windows_powershell_when_pwsh_absent(self) -> None:
        mapping = {
            "powershell.exe": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        }
        with patch("shutil.which", side_effect=_which_map(mapping)):
            result = find_powershell()

        assert result is not None
        assert result.is_core is False
        assert result.path == mapping["powershell.exe"]
        assert "5.1" in result.edition

    def test_returns_none_when_neither_present(self) -> None:
        with patch("shutil.which", side_effect=_which_map({})):
            result = find_powershell()

        assert result is None

    def test_falls_back_to_bare_pwsh_name_without_exe_suffix(self) -> None:
        """POSIX installs of PowerShell 7 register as `pwsh`, not
        `pwsh.exe` -- confirms the non-.exe fallback lookup is exercised.
        """
        mapping = {"pwsh": "/usr/bin/pwsh"}
        with patch("shutil.which", side_effect=_which_map(mapping)):
            result = find_powershell()

        assert result is not None
        assert result.is_core is True
        assert result.path == "/usr/bin/pwsh"
