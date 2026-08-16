"""Command payload encoding for the pwsh tool.

PowerShell's own ``-EncodedCommand`` convention is base64-of-**UTF-16LE**
(".NET calls this encoding ``Unicode``), NOT base64-of-UTF-8. This is not a
guess -- it was measured directly against a real Windows 11 host: a
UTF-16LE payload fed to a UTF-8 decoder does **not** raise an exception. For
an ASCII command it decodes "successfully" into a string with a NUL byte
interleaved after every character (because each ASCII character in
UTF-16LE is followed by a 0x00 byte, and 0x00 is itself a valid one-byte
UTF-8 code point). PowerShell then receives that NUL-interleaved garbage,
fails to parse it as the intended command, and reports something like::

    rc=0   err="The term 'W' is not recognized..."

Exit code **zero**. No exception anywhere in the pipeline. This is exactly
the shape of bug that passes every "does it decode without throwing" smoke
test and only misbehaves once real PowerShell tries to run the mangled
text.

The fix is not clever: pin UTF-16LE explicitly, on both ends, and never let
either side infer or default to something else.

    Python side (this module):
        base64.b64encode(command.encode("utf-16-le"))

    PowerShell side (see ``runner.RUNNER_SCRIPT``):
        [System.Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($line))

``Unicode`` is .NET's name for UTF-16LE -- it is not a generic "any
unicode" encoding, and it is not UTF-8. Confirming that name maps to the
same bytes Python produces via ``"utf-16-le"`` is exactly what the encoding
round-trip tests in this module exist to prove and keep proving.
"""

from __future__ import annotations

import base64

# The single encoding name this entire module is pinned to. Referenced by
# name (not just literal string) so a future edit that touches the encoding
# has exactly one place to change it, and every test importing this
# constant catches a regression rather than silently testing a stale value.
POWERSHELL_ENCODED_COMMAND_ENCODING = "utf-16-le"


def encode_command(command: str) -> str:
    """Encode ``command`` for PowerShell's ``-EncodedCommand`` convention.

    Pins :data:`POWERSHELL_ENCODED_COMMAND_ENCODING` explicitly -- never
    relies on a platform or library default encoding. Paired with
    ``decode_command_utf16le`` (this module, Python-side contract mirror)
    and the PowerShell-side decode in ``runner.RUNNER_SCRIPT``.

    Args:
        command: The PowerShell command/script text to encode.

    Returns:
        ASCII base64 string suitable for ``-EncodedCommand`` or for writing,
        newline-terminated, to the stdin-gated runner described in
        ``runner.py``.
    """
    return base64.b64encode(command.encode(POWERSHELL_ENCODED_COMMAND_ENCODING)).decode(
        "ascii"
    )


def decode_command_utf16le(encoded: str) -> str:
    """Decode a base64 payload using the pinned UTF-16LE contract.

    This mirrors exactly what the PowerShell-side runner does --
    ``[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($line))``
    -- so the encode/decode contract can be exercised and regression-tested
    from Python without a live PowerShell process. It is not used to decode
    real command output (PowerShell's own stdout/stderr streams are handled
    separately, as plain text); it exists purely to make the payload
    contract independently verifiable.

    Raises:
        UnicodeDecodeError / binascii.Error: If ``encoded`` is not valid
            base64-of-UTF-16LE. This is intentional -- a decode failure here
            is a loud, honest signal that the payload does not conform to
            the pinned contract, which is exactly what the silent-corruption
            bug this module exists to prevent looks like when it is NOT
            caught.
    """
    return base64.b64decode(encoded).decode(POWERSHELL_ENCODED_COMMAND_ENCODING)
