# amplifier-module-tool-pwsh

Native PowerShell (`pwsh`) execution tool for Amplifier. See the parent
bundle's `ATTRIBUTION.md`, `docs/ROADMAP.md`, and `docs/EVIDENCE.md` for
design background and measured evidence this implementation builds on.

## Contract

- Tool name: `pwsh`
- Config keys mirror `tool-bash`: `safety_profile`, `allowed_commands`,
  `denied_commands`, `safety_overrides`, `timeout`, `working_dir`,
  `max_output_bytes`.
- Exit code contract: PowerShell's native `$LASTEXITCODE` takes precedence
  when set; otherwise cmdlet-level `$?` maps to 0 (success) / 1 (failure).
  An explicit `exit N` inside the user's command always wins outright.
- Command payloads are transmitted as base64-of-UTF-16LE, matching
  PowerShell's own `-EncodedCommand` convention exactly (never guessed).

## Layout

- `encoding.py` — pinned UTF-16LE command payload encode/decode contract.
- `discovery.py` — locates `pwsh`/`powershell.exe`, preferring PowerShell 7+.
- `jobobject.py` — Win32 Job Object ctypes calls (Windows-only at runtime;
  safe to import anywhere).
- `runner.py` — stdin-gated execution sequence and exit-code mapping.
- `safety.py` — four-profile safety validator (strict/standard/permissive/unrestricted).
- `__init__.py` — the `Tool` implementation and `mount()` entry point.
