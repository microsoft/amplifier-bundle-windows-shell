---
bundle:
  name: windows-shell
  version: 0.1.0
  description: >-
    Native Windows shell support for Amplifier. Provides the `pwsh` tool for
    PowerShell execution with a PowerShell-shaped safety layer, plus
    platform-aware guidance so agents prefer the native shell on Windows.

includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main
  - bundle: windows-shell:behaviors/windows-shell
---

# Windows Shell

@windows-shell:context/windows-shell.md

---

@foundation:context/shared/common-system-base.md
