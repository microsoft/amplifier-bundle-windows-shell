"""Contract tests for RUNNER_SCRIPT's exit-code decision logic.

RUNNER_SCRIPT is PowerShell source text -- its branching logic cannot be
unit-tested by literally executing it without a live PowerShell process.
No pwsh/powershell.exe is available in this environment (verified: neither
resolves on PATH here), so these tests instead:

1. Replicate the exact documented decision table in pure Python and test
   it exhaustively against the cases measured on a real Windows 11 host
   (docs/EVIDENCE.md in the parent bundle: 7/7 passed) -- proving the
   INTENDED logic is correct.
2. Statically assert RUNNER_SCRIPT's source contains that same decision
   table, in the same priority order -- proving the PowerShell text
   actually encodes what (1) tested, not a paraphrase of it.

Neither of these is a substitute for actually running RUNNER_SCRIPT
through a live pwsh process on Windows. That verification gap is real and
is called out explicitly in the final report rather than papered over.
"""

from __future__ import annotations

import re

from amplifier_module_tool_pwsh.runner import RUNNER_SCRIPT, instrument_command


def resolve_exit_code(last_exit_code: int | None, dollar_hook: bool) -> int:
    """Pure-Python replica of RUNNER_SCRIPT's post-execution decision:

        & $sb
        $__ok  = $?              # captured IMMEDIATELY -- see below
        $__lec = $LASTEXITCODE
        if (-not $__ok) {
            if ($null -ne $__lec -and $__lec -ne 0) { exit $__lec }
            exit 1
        }
        if ($null -ne $__lec) { exit $__lec }
        exit 0

    `dollar_hook` stands in for PowerShell's `$?` (cmdlet-level success).

    FAILURE WINS. An earlier version of this table checked $LASTEXITCODE
    first and let it win outright. That is wrong twice over, and running
    the suite on a real Windows host is what exposed it:

    1. A cmdlet failure leaves $LASTEXITCODE holding whatever the last
       NATIVE command set -- frequently a stale 0 -- so `$? = false` with
       `$LASTEXITCODE = 0` reported SUCCESS. Measured on Windows 11:
       test_cmdlet_failure_maps_to_one failed on BOTH pwsh 7 and
       powershell 5.1.
    2. `$?` must be captured into a variable on the very next statement
       after the user command. PowerShell resets it after every statement,
       so by the time an `if` has evaluated its own condition, `$?`
       reflects the `if`, not the command being judged.

    RUNNER_SCRIPT also clears $LASTEXITCODE to $null before invoking the
    user command, so "stale" can only mean "set by this command".
    """
    if not dollar_hook:
        if last_exit_code is not None and last_exit_code != 0:
            return last_exit_code
        return 1
    if last_exit_code is not None:
        return last_exit_code
    return 0


class TestExitCodeDecisionTableMatchesMeasuredEvidence:
    """Each case name matches the corresponding row in docs/EVIDENCE.md's
    7/7 measured table so the mapping can be cross-checked directly.
    """

    def test_hello_no_native_command_success(self) -> None:
        # Fresh process, no native command run -> $LASTEXITCODE is $null,
        # $? is true (Write-Output succeeded).
        assert resolve_exit_code(last_exit_code=None, dollar_hook=True) == 0

    def test_native_exit_code_preserved(self) -> None:
        # cmd /c exit 42 sets $LASTEXITCODE=42; this must win outright,
        # regardless of $?.
        assert resolve_exit_code(last_exit_code=42, dollar_hook=True) == 42
        assert resolve_exit_code(last_exit_code=42, dollar_hook=False) == 42

    def test_cmdlet_failure_maps_to_one(self) -> None:
        # A non-terminating error sets $? = False but never touches
        # $LASTEXITCODE (stays null in a fresh process).
        assert resolve_exit_code(last_exit_code=None, dollar_hook=False) == 1

    def test_cmdlet_failure_wins_over_a_zero_native_exit_code(self) -> None:
        # THE REGRESSION THIS FILE EXISTS FOR.
        #
        # $? = false with $LASTEXITCODE = 0 is the exact state a cmdlet
        # failure leaves behind when any native command ran earlier in the
        # same command string. The earlier table returned 0 here and
        # reported SUCCESS for a failed command.
        #
        # Measured on Windows 11, both editions:
        #   FAILED test_cmdlet_failure_maps_to_one[pwsh]
        #   FAILED test_cmdlet_failure_maps_to_one[powershell]
        #
        # Failure must win. A zero native code cannot launder it.
        assert resolve_exit_code(last_exit_code=0, dollar_hook=False) == 1

    def test_success_with_zero_native_exit_code_is_still_success(self) -> None:
        # The control for the case above: when $? is true, a native 0 means
        # exactly what it says. A fix that made *everything* fail would pass
        # the regression test alone, so pin the success side too.
        assert resolve_exit_code(last_exit_code=0, dollar_hook=True) == 0

    def test_handled_failure_short_circuits_via_explicit_exit(self) -> None:
        # `try { ... } catch {}; exit 0` calls `exit` directly inside the
        # user's own command -- this terminates the whole pwsh process
        # immediately, before RUNNER_SCRIPT's own post-execution decision
        # logic ever runs. There is nothing for resolve_exit_code to model
        # here; the explicit `exit 0` IS the exit code. Documented as a
        # no-op case rather than silently omitted.
        pass


class TestRunnerScriptEncodesTheSameDecisionTableInPriorityOrder:
    """Statically verifies RUNNER_SCRIPT's PowerShell text contains the same
    branches, in the same order, as the Python replica above -- so a change
    to one without the other is caught.
    """

    def test_dollar_hook_captured_inside_the_user_scriptblock(self) -> None:
        """`$?` must be captured by INSTRUMENTING THE USER'S COMMAND, not by
        reading it in the runner after `& $sb`.

        Reading it in the runner does not work, and this is the second bug a
        real-Windows run exposed after the first fix. `$?` after `& $sb`
        reports whether the *scriptblock invocation* succeeded -- and a
        non-terminating error inside it (the default under
        `$ErrorActionPreference = 'Continue'`) is still a successful
        invocation. Measured on Windows 11, both editions, twice:

            FAILED test_cmdlet_failure_maps_to_one[pwsh]
            FAILED test_cmdlet_failure_maps_to_one[powershell]

        The capture has to ride along inside the scriptblock, on the
        statement immediately after the user's last one.
        """
        instrumented = instrument_command("Get-Item nope")
        lines = [ln.strip() for ln in instrumented.splitlines() if ln.strip()]
        assert lines[0] == "Get-Item nope", (
            "the user's command must come first, verbatim"
        )
        assert re.fullmatch(r"\$global:\w+\s*=\s*\$\?", lines[1]), (
            "the statement immediately after the user's command must capture "
            f"$? into a global -- PowerShell clobbers it otherwise. Got: {lines[1]!r}"
        )

    def test_runner_reads_the_captured_global_not_bare_dollar_hook(self) -> None:
        """The runner must consume what the instrumentation captured. Reading
        a bare `$?` after `& $sb` is the exact bug above.
        """
        lines = [ln.strip() for ln in RUNNER_SCRIPT.splitlines() if ln.strip()]
        invoke_idx = next(i for i, ln in enumerate(lines) if ln == "& $sb")
        after = " ".join(lines[invoke_idx + 1 :])
        assert "$global:__ok" in after, "runner must read the captured global"
        assert not re.search(r"=\s*\$\?\s*$", lines[invoke_idx + 1]), (
            "runner must NOT re-read a bare $? after & $sb -- it reports the "
            "scriptblock invocation, not the user's command"
        )

    def test_failure_branch_precedes_success_branch(self) -> None:
        """Failure wins. The `-not $<captured>` branch must be evaluated
        before any unconditional success path, so a cmdlet failure carrying
        a stale/zero native code cannot be laundered into success.
        """
        failure_pos = RUNNER_SCRIPT.find("if (-not $")
        assert failure_pos != -1, "RUNNER_SCRIPT must branch on captured -not $<var>"
        assert failure_pos < RUNNER_SCRIPT.rfind("exit 0"), (
            "the failure branch must be checked before the success exit path"
        )

    def test_stale_native_exit_code_cleared_before_user_command(self) -> None:
        """$LASTEXITCODE persists across commands in a session. Without
        clearing it, a cmdlet failure inherits whatever the last native
        command left behind. Clearing makes "set" mean "set by THIS command".
        """
        clear_pos = RUNNER_SCRIPT.find("$global:LASTEXITCODE = $null")
        invoke_pos = RUNNER_SCRIPT.find("& $sb")
        assert clear_pos != -1, "RUNNER_SCRIPT must clear $LASTEXITCODE"
        assert clear_pos < invoke_pos, "the clear must happen BEFORE the user command"

    def test_both_exit_paths_present(self) -> None:
        assert re.search(r"exit\s+\$\w+", RUNNER_SCRIPT), (
            "must exit with a captured code"
        )
        assert re.search(r"exit\s+1\b", RUNNER_SCRIPT)
        assert re.search(r"exit\s+0\b", RUNNER_SCRIPT)

    def test_null_check_uses_null_ne_not_truthiness(self) -> None:
        """`0` is falsy but IS a real exit code. `$null -ne $x` distinguishes
        "never set" from "set to zero"; a naive `if ($x)` would not.
        """
        assert re.search(r"\$null -ne \$\w+", RUNNER_SCRIPT)

    def test_output_encoding_pinned_to_utf8(self) -> None:
        """THE OTHER REGRESSION THIS FILE EXISTS FOR.

        Input encoding was pinned (UTF-16LE) but OUTPUT was not. PowerShell
        then wrote stdout in the console's OEM codepage while Python decoded
        UTF-8. Measured on Windows 11, both editions:

            'cafe-<CJK>-<emoji>'  came back as  'caf\\ufffd-??-??'
            FAILED test_non_ascii_round_trips[pwsh]
            FAILED test_non_ascii_round_trips[powershell]

        Pinning one direction is not pinning the contract.
        """
        assert "[Console]::OutputEncoding" in RUNNER_SCRIPT
        assert "UTF8Encoding" in RUNNER_SCRIPT
        encoding_pos = RUNNER_SCRIPT.find("[Console]::OutputEncoding")
        invoke_pos = RUNNER_SCRIPT.find("& $sb")
        assert encoding_pos < invoke_pos, (
            "output encoding must be pinned BEFORE the user command runs"
        )

    def test_payload_decoded_via_unicode_encoding_matching_python_side(self) -> None:
        """.NET's 'Unicode' encoding IS UTF-16LE -- must match the Python
        side's encode_command() contract exactly (see test_encoding.py).
        """
        assert "[System.Text.Encoding]::Unicode.GetString(" in RUNNER_SCRIPT
        assert "[Convert]::FromBase64String(" in RUNNER_SCRIPT

    def test_reads_stdin_before_decoding_anything(self) -> None:
        """The whole Job Object sequencing guarantee (see runner.py module
        docstring) depends on this being the FIRST executable statement:
        the process must be blocked on stdin before it does anything with
        user-controlled input.
        """
        read_line_pos = RUNNER_SCRIPT.find("[Console]::In.ReadLine()")
        decode_pos = RUNNER_SCRIPT.find("FromBase64String")
        assert read_line_pos != -1
        assert decode_pos != -1
        assert read_line_pos < decode_pos

    def test_executes_via_scriptblock_create_not_invoke_expression(self) -> None:
        """The runner decodes and runs the (already safety-validated) user
        command via [scriptblock]::Create(...).Invoke-equivalent (`& $sb`)
        rather than literally calling Invoke-Expression/iex on it -- iex is
        exactly the primitive the safety layer blocks by default (see
        safety.py), so the trusted runner avoids using the same construct
        it disallows in user input.
        """
        assert "[scriptblock]::Create(" in RUNNER_SCRIPT
        assert "Invoke-Expression" not in RUNNER_SCRIPT
        assert re.search(r"(?<!\w)iex(?!\w)", RUNNER_SCRIPT, re.IGNORECASE) is None
