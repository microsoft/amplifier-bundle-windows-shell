---
skill:
  name: powershell-windows-dev
  version: 1.0.0
  description: Comprehensive PowerShell and Windows development reference — command equivalents, scripting patterns, file operations, and Windows-specific conventions
  authors:
    - name: Microsoft MADE:Explorations Team
---

# PowerShell and Windows Development

## When to Use This Skill

Load this skill when:
- Working on a Windows platform where PowerShell is the primary shell
- Writing PowerShell scripts or commands
- Translating Unix/bash scripts to PowerShell
- Performing system administration tasks on Windows
- Working with Windows-specific features (registry, services, WMI, Active Directory)

## Tool Selection Guide

| Platform | Preferred Shell | Fallback | Notes |
|---|---|---|---|
| Windows (native) | `pwsh` | `bash` (Git Bash/WSL) | Unix tools unavailable natively |
| WSL | `bash` | `pwsh` | Full Linux tools available |
| Linux | `bash` | `pwsh` (if installed) | pwsh available via snap/apt |
| macOS | `bash` | `pwsh` (if installed) | pwsh available via brew |

## File Operations

### Reading Files
```powershell
# Read entire file
Get-Content file.txt

# First N lines (head)
Get-Content file.txt -TotalCount 20

# Last N lines (tail)
Get-Content file.txt -Tail 20

# Specific line range (0-indexed array)
(Get-Content file.txt)[9..19]    # Lines 10-20

# Read as single string (not line array)
Get-Content file.txt -Raw

# Read with encoding
Get-Content file.txt -Encoding UTF8
```

### Writing Files
```powershell
# Write string to file (overwrite)
"content" | Set-Content file.txt

# Append to file
"more content" | Add-Content file.txt

# Write from pipeline
Get-Process | Out-File processes.txt

# Write with specific encoding
"content" | Set-Content file.txt -Encoding UTF8

# Create file if not exists, don't overwrite
if (-not (Test-Path file.txt)) { New-Item file.txt }
```

### File Search and Discovery
```powershell
# Find files by name pattern (like find -name)
Get-ChildItem -Recurse -Filter "*.py"

# Find files by regex name
Get-ChildItem -Recurse | Where-Object { $_.Name -match "test_.*\.py$" }

# Find directories only
Get-ChildItem -Recurse -Directory

# Find files modified in last 24h
Get-ChildItem -Recurse | Where-Object { $_.LastWriteTime -gt (Get-Date).AddDays(-1) }

# Find files larger than 1MB
Get-ChildItem -Recurse | Where-Object { $_.Length -gt 1MB }

# Exclude directories
Get-ChildItem -Recurse -Exclude "node_modules","__pycache__",".git"
```

### Content Search (grep equivalents)
```powershell
# Basic search in file
Select-String -Path "file.txt" -Pattern "error"

# Recursive search in directory
Get-ChildItem -Recurse -Filter "*.py" | Select-String "import asyncio"

# Case-sensitive search (PowerShell is case-insensitive by default)
Select-String -Path "*.py" -Pattern "Error" -CaseSensitive

# Search with context lines (like grep -C)
Select-String -Path "*.py" -Pattern "def mount" -Context 2,2

# Regex search
Select-String -Path "*.log" -Pattern "\d{4}-\d{2}-\d{2}.*ERROR"

# Search and extract matches
Select-String -Path "*.py" -Pattern "version\s*=\s*['\"](.+?)['\"]" |
    ForEach-Object { $_.Matches.Groups[1].Value }

# Count matches (like grep -c)
(Select-String -Path "*.py" -Pattern "TODO").Count

# List files with matches (like grep -l)
Select-String -Path "*.py" -Pattern "import" | Select-Object -Unique Path
```

### Text Processing (sed/awk equivalents)
```powershell
# Replace in file content (like sed 's/old/new/g')
(Get-Content file.txt) -replace 'old', 'new' | Set-Content file.txt

# Replace with regex
(Get-Content file.txt) -replace '\d{4}', 'YEAR' | Set-Content file.txt

# Process line by line (like awk)
Get-Content data.csv | ForEach-Object {
    $fields = $_ -split ','
    "$($fields[0]) - $($fields[2])"
}

# Filter lines (like grep)
Get-Content file.txt | Where-Object { $_ -match 'pattern' }

# Number lines (like nl or cat -n)
$i = 1; Get-Content file.txt | ForEach-Object { "{0,4} {1}" -f $i++, $_ }

# Sort and unique
Get-Content file.txt | Sort-Object -Unique
```

## Process Management

```powershell
# List processes (like ps aux)
Get-Process

# Find specific process
Get-Process -Name "python*"
Get-Process | Where-Object { $_.CPU -gt 10 }

# Kill process by PID
Stop-Process -Id 1234

# Kill process by name
Stop-Process -Name "python" -Force

# Start process in background
Start-Process pwsh -ArgumentList "-Command", "long-running-script.ps1" -NoNewWindow

# Wait for process to exit
$proc = Start-Process pwsh -ArgumentList "-c", "Start-Sleep 5" -PassThru
$proc | Wait-Process -Timeout 10
```

## Environment and System

```powershell
# Get/set environment variables
$env:MY_VAR = "value"
Write-Output $env:MY_VAR
Get-ChildItem Env:                    # List all env vars

# System information
$PSVersionTable                        # PowerShell version
[System.Environment]::OSVersion       # OS version
[System.Environment]::MachineName     # Computer name
[System.Environment]::UserName        # Current user

# Path operations
$env:PATH -split [IO.Path]::PathSeparator    # Split PATH
Join-Path $HOME "Documents" "file.txt"       # Join path segments
[IO.Path]::GetExtension("file.py")           # Get extension
Resolve-Path "~/Documents"                    # Resolve ~ and relative paths

# Check if running as admin
([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
```

## Package Management

```powershell
# Windows Package Manager (winget)
winget search python
winget install Python.Python.3.12
winget upgrade --all

# Chocolatey (if installed)
choco install nodejs
choco upgrade all

# PowerShell modules
Install-Module -Name Az -Scope CurrentUser
Get-InstalledModule
Update-Module -Name Az

# pip (Python) — works the same
pip install package-name
pip install -r requirements.txt
```

## Network Operations

```powershell
# HTTP requests (like curl)
Invoke-WebRequest -Uri "https://api.example.com/data"

# REST API calls (returns parsed JSON directly)
$response = Invoke-RestMethod -Uri "https://api.example.com/data"
$response.items | Format-Table

# Download file (like wget)
Invoke-WebRequest -Uri "https://example.com/file.zip" -OutFile "file.zip"

# POST with JSON body
$body = @{ name = "test"; value = 42 } | ConvertTo-Json
Invoke-RestMethod -Uri "https://api.example.com" -Method Post -Body $body -ContentType "application/json"

# Check port (like nc or telnet)
Test-NetConnection -ComputerName "localhost" -Port 8080
```

## Windows-Specific Operations

### Services
```powershell
Get-Service                              # List all services
Get-Service -Name "wuauserv"            # Specific service
Start-Service -Name "ServiceName"
Stop-Service -Name "ServiceName"
Restart-Service -Name "ServiceName"
```

### Registry
```powershell
# Read registry
Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion"

# Set registry value
Set-ItemProperty -Path "HKCU:\Software\MyApp" -Name "Setting" -Value "NewValue"

# Test if key exists
Test-Path "HKLM:\SOFTWARE\MyApp"
```

### Scheduled Tasks
```powershell
Get-ScheduledTask
Get-ScheduledTask -TaskName "TaskName" | Get-ScheduledTaskInfo
```

## Scripting Patterns

### Error Handling
```powershell
try {
    Get-Content "nonexistent.txt" -ErrorAction Stop
} catch {
    Write-Error "Failed: $_"
} finally {
    # Cleanup code
}
```

### Functions
```powershell
function Get-ProjectFiles {
    param(
        [string]$Path = ".",
        [string]$Extension = "*.py",
        [switch]$IncludeTests
    )

    $files = Get-ChildItem -Path $Path -Recurse -Filter $Extension
    if (-not $IncludeTests) {
        $files = $files | Where-Object { $_.FullName -notmatch "test" }
    }
    return $files
}
```

### Conditional Execution
```powershell
# If/else
if (Test-Path $filePath) {
    Get-Content $filePath
} else {
    Write-Warning "File not found: $filePath"
}

# Ternary (PowerShell 7+)
$result = Test-Path $file ? "exists" : "missing"

# Null coalescing (PowerShell 7+)
$value = $env:MY_VAR ?? "default"
```

### Pipeline Best Practices
```powershell
# PowerShell pipelines pass OBJECTS, not text
# This is a fundamental difference from bash

# Bad (bash-style text parsing):
Get-Process | Out-String | Select-String "python"

# Good (object pipeline):
Get-Process | Where-Object Name -like "*python*"

# Object properties are directly accessible
Get-ChildItem *.py | Select-Object Name, Length, LastWriteTime

# Measure and aggregate
Get-ChildItem -Recurse *.py | Measure-Object -Property Length -Sum
```

## Path Conventions

| Convention | Windows | Linux/macOS |
|---|---|---|
| Separator | `\` (also accepts `/`) | `/` |
| Home | `$HOME` or `$env:USERPROFILE` | `$HOME` or `~` |
| Temp | `$env:TEMP` | `/tmp` |
| Current dir | `$PWD` or `.` | `$PWD` or `.` |
| Drive root | `C:\` | `/` |
| Program files | `$env:ProgramFiles` | `/usr/local` |

**Tip**: PowerShell accepts forward slashes on Windows: `Get-Content C:/Users/me/file.txt` works fine.
