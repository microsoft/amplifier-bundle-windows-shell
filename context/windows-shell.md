# Windows Shell Environment

You have access to `pwsh` (PowerShell) for executing commands.

## Tool Preference on Windows

When operating on Windows, **prefer `pwsh` over `bash`** for all system operations:

- `bash` on Windows uses Git Bash or WSL — most Unix tools (`grep`, `find`, `cat`, `sed`, `awk`) are **NOT** natively available
- `pwsh` is the native Windows shell with full system access
- Use `bash` ONLY when running a cross-platform script explicitly written for bash

## Quick Unix to PowerShell Equivalents

| Unix Command | PowerShell Equivalent | Notes |
|---|---|---|
| `grep pattern file` | `Select-String -Pattern "pattern" file` | Returns match objects with line numbers |
| `grep -r pattern dir` | `Get-ChildItem dir -Recurse \| Select-String "pattern"` | Recursive search |
| `find . -name "*.py"` | `Get-ChildItem -Recurse -Filter "*.py"` | Returns FileInfo objects |
| `cat file` | `Get-Content file` | Can slice: `(Get-Content file)[0..19]` for first 20 lines |
| `head -n 20 file` | `Get-Content file -TotalCount 20` | |
| `tail -n 20 file` | `Get-Content file -Tail 20` | |
| `ls -la` | `Get-ChildItem` | Rich output with file properties |
| `which cmd` | `Get-Command cmd` | Shows command source and type |
| `echo $VAR` | `Write-Output $env:VAR` | Env vars use `$env:` prefix |
| `env` | `Get-ChildItem Env:` | PowerShell drive provider |
| `mkdir -p path` | `New-Item -ItemType Directory -Force path` | `-Force` creates parents |
| `rm -rf dir` | `Remove-Item -Recurse -Force dir` | |
| `cp -r src dst` | `Copy-Item -Recurse src dst` | |
| `mv src dst` | `Move-Item src dst` | |
| `wc -l file` | `(Get-Content file).Count` | Or `Measure-Object -Line` |
| `sort file` | `Get-Content file \| Sort-Object` | Can sort by any property |
| `curl url` | `Invoke-WebRequest url` | Or `Invoke-RestMethod` for JSON APIs |
| `ps aux` | `Get-Process` | Rich process objects |
| `kill PID` | `Stop-Process -Id PID` | |

## PowerShell Key Differences from Bash

- **Object pipeline**: PowerShell passes objects, not text. `Get-Process | Where-Object CPU -gt 10` filters by actual CPU property
- **Verb-Noun naming**: Commands follow `Verb-Noun` pattern (Get-Content, Set-Location, New-Item)
- **Parameters**: Use `-ParameterName value` not `--parameter=value`
- **Variables**: `$variable` (no export needed), env vars via `$env:NAME`
- **String interpolation**: Double quotes expand variables `"Hello $name"`, single quotes are literal `'Hello $name'`
- **Comparison operators**: `-eq`, `-ne`, `-gt`, `-lt`, `-like`, `-match` (not `==`, `!=`, `>`, `<`)

For comprehensive PowerShell guidance — advanced patterns, scripting, and Windows dev workflows — load the full skill:
```
load_skill(source="git+https://github.com/colombod/amplifier-module-tool-pwsh@main#subdirectory=skills")
```
