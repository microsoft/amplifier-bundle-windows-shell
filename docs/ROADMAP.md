# Roadmap

Known gaps in the vendored module, each with attribution for where the better
answer already exists. Nothing here is a blocker for the current state — the
tool is proven working on native Windows — but each is a real improvement with
a known source.

---

## 1. Pin the command payload encoding

**Status:** open
**Severity:** silent corruption, no failure signal
**Source of the fix:** [@DavidKoleczek](https://github.com/DavidKoleczek)'s
`agent-server/powershell.py`

The vendored module handles UTF-8 only in *output truncation* helpers. The
command payload's encoding is not pinned on both ends.

This was measured directly, not theorised. A UTF-16LE payload fed to a UTF-8
decoder does **not** error:

```
rc=0   err="The term 'W' is not recognized..."
```

Exit code **zero**, and PowerShell attempted to execute a mangled command.
PowerShell's own `-EncodedCommand` convention is base64-of-UTF-16LE, not UTF-8
— so an implementation that guesses will pass every ASCII test and corrupt the
first non-ASCII command with no signal that anything went wrong.

**Fix:** pin the encoding explicitly on both the writing and decoding side.
David's runner does this — `[Text.Encoding]::UTF8.GetString(...)` paired with
a UTF-8 `b64encode` — and it is a small, well-bounded change.

---

## 2. Adopt the stdin-gated Job Object

**Status:** open
**Severity:** correctness under process-tree teardown
**Source of the fix:** [@DavidKoleczek](https://github.com/DavidKoleczek)'s
`agent-server/powershell.py`

The vendored module uses `CREATE_NEW_PROCESS_GROUP` on Windows and
`start_new_session=True` on POSIX. Portable and reasonable — but there is no
Win32 Job Object, so descendant containment relies on process-group semantics.

David's design is categorically better: the runner blocks on
`[Console]::In.ReadLine()`, is assigned to a Job Object *while still blocked*,
and only then receives its payload. There is no interval during which a child
can escape the job, so the PID-reuse race is not narrowed — it is **removed**.

Worth noting this is the same race an independent security review flagged in
Amplifier's own `tool-bash` descendant sweep. The gate design answers it in
both places.

**Measured caveat, so the work is scoped honestly:** on this Windows 11 host, a
bare Job Object (no breakaway limits set) *did* contain a grandchild spawned
with `CREATE_BREAKAWAY_FROM_JOB` — `breakaway_escaped: False`. That matches
Microsoft's documentation: setting neither `JOB_OBJECT_LIMIT_BREAKAWAY_OK` nor
`JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK` *is* the safe configuration. So the
default is sound; the gain from the gate is the removal of the assignment
window, not breakaway protection.

---

## 3. AST-based constraint checking

**Status:** open, larger scope
**Source of the idea:** [@DavidKoleczek](https://github.com/DavidKoleczek)

The vendored safety layer matches on substrings, command position, and regex —
the same approach `tool-bash` uses, which is a deliberate and defensible
consistency choice.

David's reference pipes commands through PowerShell's own parser
(`[System.Management.Automation.Language.Parser]::ParseInput`), extracts
`CommandAst` nodes, and checks each one. That handles `a; b | c` as three
distinct commands, reaches into subexpressions and script blocks, and — the
part that generalises furthest — **denies on parse error**.

> A validator that cannot parse its input must never report it safe.

**Judgement:** this is real work with a real ongoing cost. PowerShell's grammar
moves, and an AST layer must move with it. Worth doing only if the pattern
layer proves insufficient in practice. Recorded so the option is not lost, not
because it is obviously correct to buy now.

---

## 4. Version detection in the tool description

**Status:** open, small
**Source of the idea:** [@DavidKoleczek](https://github.com/DavidKoleczek)

Windows PowerShell 5.1 and PowerShell 7+ diverge in real, behaviour-affecting
ways (`Invoke-WebRequest`, `??`, `ForEach-Object -Parallel`). Guessing wrong
usually fails *quietly* rather than loudly, which makes it exactly the kind of
thing worth spending always-on description tokens on.

David's implementation queries `$PSVersionTable` once at mount and injects
version-specific guidance into the tool description. Amplifier's `Tool.description`
is an instance property, assembled at `mount()` — `tool-bash` already does this
to append a Windows shell note — so the mechanism is available and proven.

Both editions are present on the test host
(`C:\Program Files\PowerShell\7\pwsh.exe` and
`C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`), with `pwsh`
resolving first.

---

## 5. CI that actually runs

**Status:** open
**Source of the idea:** [@DavidKoleczek](https://github.com/DavidKoleczek)'s
test suite

His tests parametrize across both editions and skip individually when one is
absent:

```python
pytestmark = pytest.mark.skipif(sys.platform != "win32", ...)

@pytest.fixture(params=[None, "pwsh.exe", "powershell.exe"],
                ids=["default", "pwsh", "windows-powershell"])
```

Both editions exist on a GitHub `windows-latest` runner, so tests in this shape
genuinely execute in CI rather than skipping vacuously.

This matters more than it looks. Elsewhere in this effort, two Windows
regression tests sat green on Linux for days and failed the first time they
actually ran on Windows — and separately, a Windows CI leg was reported as
passing while it had in fact been cancelled mid-build and never executed a
single test. **A platform-skipped test passes vacuously everywhere else.**
Verify that the guard runs on the platform it guards.
