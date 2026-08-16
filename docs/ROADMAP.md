# Roadmap

What is done, what is deliberately deferred, and what is not yet proven.

---

## Closed

### 1. Pin the command payload encoding — DONE, both directions

**Idea credited to** [@DavidKoleczek](https://github.com/DavidKoleczek).

The payload is pinned to UTF-16LE (PowerShell's `-EncodedCommand` convention)
on the Python encode side and decoded with `[System.Text.Encoding]::Unicode`
on the PowerShell side. One named constant, no platform defaults, no guessing.

**We initially pinned only that direction and shipped it green on Linux.** The
output side was unpinned — PowerShell wrote stdout in the console's OEM
codepage while Python decoded UTF-8, and `café-日本-🚀` came back as
`caf\ufffd-??-??` on both editions. The runner now pins
`[Console]::OutputEncoding` and `$OutputEncoding` to UTF-8 before the user's
command runs.

Guarded by `test_output_encoding_pinned_to_utf8`, which fails against the
pre-fix runner.

### 2. Job Object containment — DONE, stdin-gated

**Idea credited to** [@DavidKoleczek](https://github.com/DavidKoleczek).

The runner blocks on `[Console]::In.ReadLine()` as its first statement using
fixed, trusted script text with zero user input. The parent assigns the
process to a Win32 Job Object *before* writing anything to stdin, so no user
code can have executed — the assignment race is removed, not narrowed.

Every `ctypes` Win32 call declares explicit `restype`/`argtypes`;
`INVALID_HANDLE_VALUE` is compared against `ctypes.c_void_p(-1).value`. Only
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` is set — **no breakaway limits**, which
measurement confirmed is the containing configuration.

### 3. Exit-code semantics — DONE, and harder than it looked

**Idea credited to** [@DavidKoleczek](https://github.com/DavidKoleczek), whose
implementation instruments the *command* rather than the runner. We did it in
the runner first, and a real-Windows run showed why that fails: `$?` after
`& $sb` reports the scriptblock *invocation*, and a non-terminating error
inside it is still a successful invocation. Cmdlet failures reported exit 0 on
both editions.

The capture now rides inside the user's command text. `$LASTEXITCODE` is
cleared beforehand so "set" means "set by this command", and **failure wins** —
a zero native code cannot launder a cmdlet failure into success.

Full detail in [`EVIDENCE.md`](EVIDENCE.md).

---

## Open

### 4. AST-based constraint checking

**Idea credited to** [@DavidKoleczek](https://github.com/DavidKoleczek).

His reference pipes commands through PowerShell's own parser
(`[System.Management.Automation.Language.Parser]::ParseInput`), extracts
`CommandAst` nodes, and checks each one — handling `a; b | c` as three
commands, reaching into subexpressions and script blocks, and **denying on
parse error**.

We adopted the *principle* — a validator that cannot parse its input must not
report it safe — via a cheap structural gate (PowerShell-aware quote and
bracket balance, honoring backtick and doubled-quote escapes) that denies
before the pattern layer runs.

**Judgement: not obviously worth buying yet.** Full AST checking needs a live
PowerShell process per validation call, and PowerShell's grammar moves. Worth
doing if the pattern layer proves insufficient in practice. Recorded so the
option is not lost, and labelled honestly as a substitute rather than an
equivalent.

### 5. Version-specific guidance in the tool description

**Idea credited to** [@DavidKoleczek](https://github.com/DavidKoleczek).

Windows PowerShell 5.1 and PowerShell 7+ diverge in behaviour-affecting ways
(`Invoke-WebRequest`, `??`, `ForEach-Object -Parallel`). Guessing wrong usually
fails *quietly*, which is what makes it worth always-on description tokens.

`Tool.description` is an instance property assembled at `mount()` — `tool-bash`
already appends a Windows shell note this way — so the mechanism exists. Not
yet wired.

### 6. Windows CI

**Test shape credited to** [@DavidKoleczek](https://github.com/DavidKoleczek).

The live-execution suite parametrizes across both editions and skips
individually when one is absent. Both exist on a GitHub `windows-latest`
runner, so it would genuinely execute there.

**This is the highest-value open item**, because the entire reason this module
is correct is that someone ran it on real Windows. Two bugs sat green on Linux.
A Windows leg is what makes that repeatable rather than a one-off.

Two traps to avoid, both hit during this work:

- **A platform-skipped test passes vacuously.** Assert the expected count
  actually ran; do not read a green summary as coverage.
- **A cancelled job can report as passing.** A Windows CI leg was recorded as
  green while having been cancelled mid-build, never executing a single test.
  Check that tests ran, not that the job is not red.

### 7. Background execution ergonomics

Currently minimal: starts the process, tracks the PID. No log-polling or
output-streaming like `tool-bash` offers. A scope choice, not an oversight.

---

## Not a roadmap item, but worth knowing

**`hooks-process-guard` gives this tool zero coverage.** It hard-codes
`if tool_name != "bash": return` (`__init__.py:95`), so any tool under another
name is unguarded. Likewise, ~11 mode and skill tool-policy lines list `bash`
by name — a mode blocking `bash` does **not** block `pwsh`.

Neither is fixable from inside this bundle; both are foundation-level
couplings to a literal string. Flagged so the gap is known rather than
discovered later. The durable fix is dispatching on a capability rather than a
tool name.
