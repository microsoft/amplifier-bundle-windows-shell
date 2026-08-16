# Attribution

This bundle consolidates work from two team members. It is a packaging effort,
not an original implementation — the substance came from elsewhere and is
credited here explicitly.

---

## Diego Colombo ([@colombod](https://github.com/colombod)) — the module

**Source:** [`colombod/amplifier-module-tool-pwsh`](https://github.com/colombod/amplifier-module-tool-pwsh)
**Vendored at:** commit `e9139f0` (2026-04-28)
**License:** MIT, "Copyright (c) Microsoft Corporation"

Everything under `modules/tool-pwsh/`, plus `context/windows-shell.md` and
`skills/powershell-windows-dev/`, is Diego's work, vendored essentially
unchanged. That includes:

- The complete `pwsh` tool — 559 lines of module implementation
- A **621-line PowerShell-shaped safety layer** with four profiles
  (`strict` / `standard` / `permissive` / `unrestricted`)
- Background execution, output truncation with UTF-8 boundary handling,
  and process-group management for clean timeouts
- The behavior, the always-on Windows context, the PowerShell skill,
  and four test files

### Two design decisions of his worth calling out

**He mirrored `tool-bash`'s config surface exactly.** Same four profile names,
same `safety_profile` / `allowed_commands` / `denied_commands` keys. This
matters more than it sounds: bundle config merges by *module ID*, so a second
shell module would normally mean a second config surface that no existing
bundle populates — an operator sets `safety_profile: strict` and it silently
fails to apply to the new tool. Matching the surface means the muscle memory,
the documentation, and the operator's mental model all transfer intact.

**His safety layer is stronger than the shipped bash one.** Measured directly
against `tool-bash`'s `safety.py`: it has **zero** patterns for remote-script
execution across *all four* profiles — `curl … | sh` passes under `strict`.
Diego's covers `|\s*Invoke-Expression`, along with `Set-ExecutionPolicy
Bypass/Unrestricted`, `Start-Process -Verb RunAs`, registry-hive deletion, and
disk operations (`Format-Volume`, `Clear-Disk`, `Initialize-Disk`,
`Remove-Partition`).

---

## David Koleczek ([@DavidKoleczek](https://github.com/DavidKoleczek)) — the reference architecture

**Source:** [`DavidKoleczek/agent-server`](https://github.com/DavidKoleczek/agent-server) —
`src/agent_server/core/tools/powershell.py` (662 lines) and its test suite (602 lines)

No code is copied from this repository. It was studied as a reference
implementation, and it contributed the sharpest ideas in this design space.
Several are not yet adopted here — see `docs/ROADMAP.md`, where each is
attributed.

**Job Object assignment behind a stdin gate.** A runner process blocks on
`[Console]::In.ReadLine()`, is assigned to a Win32 Job Object *while still
blocked*, and only then receives its payload. There is no window in which a
child can escape the job — which means no PID-reuse race to narrow, because
the race cannot occur. This is a categorically better answer than
assign-then-sweep-for-descendants.

**Separating `$?` from `$LASTEXITCODE`.** PowerShell has two distinct notions
of failure — cmdlet failure and native process exit — and collapsing them
misreports outcomes. His test suite pins the subtlety precisely:
`-ErrorAction SilentlyContinue` still yields **exit 1**, and only
`try { … } catch {}; exit 0` genuinely succeeds.

**Constraint checking via PowerShell's own AST parser.** Commands are parsed
with `[System.Management.Automation.Language.Parser]::ParseInput`, checked as
`CommandAst` nodes, and **parse errors are denied**. A validator that cannot
parse its input must never report it safe — a principle worth stating in the
general case.

**Parametrized tests across both editions.** His suite runs against `pwsh.exe`
and `powershell.exe` separately, skipping individually when one is absent.
Both editions exist on a GitHub `windows-latest` runner, so tests in that
shape genuinely execute in CI.

---

## This bundle

The packaging, the local-source rewiring, the Windows verification, and this
document. The verification evidence is in `docs/EVIDENCE.md` — including the
confound checks, and the hazards found but *not* fixed.
