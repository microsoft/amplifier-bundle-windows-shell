"""Regression tests for the pinned UTF-16LE command-payload encoding.

These tests exist to catch exactly one class of regression: someone
changing ``encode_command`` (or the runner's decode contract) to guess an
encoding instead of pinning UTF-16LE explicitly. That bug was measured on
a real Windows host and does NOT raise an exception for ASCII input -- it
silently produces a NUL-interleaved garbage string, decodes with exit code
0, and only fails later when PowerShell tries to parse the mangled text.
See ``encoding.py`` and the parent bundle's docs/EVIDENCE.md /
docs/ROADMAP.md for the full story.

Platform-independent: pure Python string/byte manipulation, no ctypes, no
subprocess. Runs on Linux, macOS, and Windows identically -- this is the
"anything platform-independent must run everywhere so Linux CI has teeth"
requirement in practice.
"""

from __future__ import annotations

import base64

import pytest
from amplifier_module_tool_pwsh.encoding import decode_command_utf16le, encode_command


def _simulate_ps_side_decode(b64: str) -> str:
    """Mirrors exactly what the PowerShell-side runner does:

        [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($line))

    .NET's "Unicode" encoding IS UTF-16LE -- this helper exists to prove
    that identity and to exercise the real contract from the Python side
    without needing a live PowerShell process.
    """
    return decode_command_utf16le(b64)


class TestEncodingRoundTrip:
    """Proves encode_command() actually produces UTF-16LE, not a guess."""

    @pytest.mark.parametrize(
        "command",
        [
            "Get-Process",
            "Set-Content -Path proof.txt -Value hello",
            "café-日本-🚀",
            "café-日本-🚀 && echo hi",
            "echo 'nested \"quotes\" here'",
            "",  # empty command must still round-trip cleanly
            "a" * 5000,  # large payload, exercises no accidental truncation
        ],
    )
    def test_round_trips_through_the_powershell_side_decode_contract(
        self, command: str
    ) -> None:
        encoded = encode_command(command)
        assert _simulate_ps_side_decode(encoded) == command

    def test_encode_command_uses_utf16le_bytes_exactly(self) -> None:
        """Directly inspects the produced bytes rather than trusting a
        round trip alone -- a buggy implementation could theoretically
        round-trip through a *different* wrong encoding that happens to be
        self-consistent. Pin the actual byte layout.
        """
        command = "hi"
        encoded = encode_command(command)
        raw = base64.b64decode(encoded)
        # UTF-16LE: each ASCII char is 2 bytes, low byte first, high byte 0x00.
        assert raw == b"h\x00i\x00"

    def test_encoded_payload_is_plain_ascii(self) -> None:
        """The base64 alphabet is ASCII-only by definition; the tool writes
        this string (plus a newline) to a subprocess stdin pipe encoded as
        ASCII (see runner.py's `payload.encode("ascii")`). If encode_command
        ever produced non-ASCII output this would corrupt that write
        silently -- assert the invariant directly.
        """
        encoded = encode_command("anything with unicode: 日本語 🎉")
        encoded.encode("ascii")  # raises UnicodeEncodeError if this ever breaks


class TestTheEncodingTrap:
    """Demonstrates the exact reported bug and proves our contract avoids it.

    This is the test the task calls out by name: it must fail against an
    implementation that guesses the encoding instead of pinning it.
    """

    def test_ascii_command_misdecoded_as_utf8_is_silently_wrong_not_loud(self) -> None:
        """The dangerous case: UTF-16LE bytes for an ASCII command, if
        decoded as UTF-8 by mistake, produce NO exception -- just a garbage
        string with a NUL byte interleaved after every character. This is
        the exact shape of the measured bug: `rc=0` with a confusing
        downstream parse error, not a clean failure anywhere in the
        encode/decode step itself.
        """
        command = "Set-Content -Path proof.txt -Value hello"
        correct_b64 = encode_command(command)  # our real, pinned encoder

        # Simulate the regression: decode with UTF-8 instead of the
        # documented UTF-16LE contract.
        wrongly_decoded = base64.b64decode(correct_b64).decode("utf-8")

        assert wrongly_decoded != command, (
            "if this ever matches, the two encodings have become "
            "indistinguishable for this input and the test needs a "
            "different command to prove the corruption"
        )
        assert "\x00" in wrongly_decoded, (
            "the corruption signature (NUL bytes interleaved between "
            "characters) must be present -- this is what 'silently wrong, "
            "not loud' looks like for ASCII input"
        )

        # Our documented, contractual decode must recover the original
        # exactly -- this is the assertion that fails if encode_command
        # (or the decode contract) ever regresses to guessing UTF-8.
        assert _simulate_ps_side_decode(correct_b64) == command

    def test_non_ascii_command_would_corrupt_or_raise_under_utf8_misdecode(
        self,
    ) -> None:
        """For non-ASCII input, a UTF-8 misdecode of UTF-16LE bytes is even
        less forgiving than the ASCII case: it frequently raises outright
        (odd-length byte sequences, invalid continuation bytes) rather than
        silently producing garbage. Either way it must never match the
        original command -- proving there is no encoding for which
        guessing UTF-8 happens to be safe.
        """
        command = "café"
        correct_b64 = encode_command(command)
        raw = base64.b64decode(correct_b64)

        try:
            wrongly_decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            wrongly_decoded = None  # loud failure is an acceptable outcome too

        assert wrongly_decoded != command

        # Our pinned contract still recovers the original correctly.
        assert _simulate_ps_side_decode(correct_b64) == command

    def test_utf8_encoding_side_would_break_the_powershell_side_decode(self) -> None:
        """Inverts the direction: if the ENCODE side regressed to UTF-8
        (instead of UTF-16LE), the PowerShell-side decode contract
        (`_simulate_ps_side_decode`, i.e. UTF-16LE) must fail to recover
        the original text -- proving the two sides are not accidentally
        compatible and that pinning genuinely matters on both ends.
        """
        command = "café-日本-🚀"
        wrong_b64 = base64.b64encode(command.encode("utf-8")).decode("ascii")

        recovered: str | None
        try:
            recovered = decode_command_utf16le(wrong_b64)
        except UnicodeDecodeError:
            recovered = None

        assert recovered != command
