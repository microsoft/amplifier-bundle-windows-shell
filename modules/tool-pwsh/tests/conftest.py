"""Pytest configuration for tool-pwsh tests.

Adds the module's own directory to sys.path so
``amplifier_module_tool_pwsh`` is importable without requiring a package
install/build step first -- this module has no dependencies beyond the
amplifier-core peer dependency, so a plain sys.path insertion is sufficient
and keeps the test suite runnable with just ``pytest`` from this directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

_MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(_MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(_MODULE_ROOT))
