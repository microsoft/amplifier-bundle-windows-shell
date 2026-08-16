"""
Safety validation module for the Amplifier PowerShell tool.

Provides a configurable, profile-based safety system with smart pattern matching
that avoids false positives while maintaining security for dangerous commands.

Key Features:
- Multiple safety profiles (strict, standard, permissive, unrestricted)
- Smart pattern matching that distinguishes commands from paths/strings
- Configurable allowlists that can override blocklists (profile-dependent)
- Clear error messages with hints for enabling blocked commands

Example:
    >>> from safety import SafetyValidator, SafetyConfig
    >>> validator = SafetyValidator(profile="strict")
    >>> result = validator.validate("Get-ChildItem $HOME")
    >>> assert result.allowed  # Not blocked - just listing home directory

    >>> result = validator.validate("Format-Volume -DriveLetter C")
    >>> assert not result.allowed  # Blocked in strict mode
    >>> print(result.hint)  # Suggests permissive/unrestricted profile
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class BlockPattern:
    """A pattern to match against commands for blocking.

    Attributes:
        pattern: The pattern string to match
        reason: Human-readable explanation of why this is blocked
        check_type: How to match the pattern:
            - "command": Only match at command position (not in paths/strings)
            - "substring": Simple substring match (legacy behavior)
            - "regex": Full regex pattern matching
    """

    pattern: str
    reason: str
    check_type: Literal["command", "substring", "regex"] = "substring"


@dataclass
class SafetyProfile:
    """A safety profile defining blocked patterns and override behavior.

    Attributes:
        name: Profile identifier (strict, standard, permissive, unrestricted)
        blocked_patterns: List of patterns to block
        allow_overrides: Whether allowlist can override blocked patterns
    """

    name: str
    blocked_patterns: list[BlockPattern]
    allow_overrides: bool = False


@dataclass
class SafetyResult:
    """Result of a safety validation check.

    Attributes:
        allowed: Whether the command is allowed to execute
        reason: Explanation if blocked (None if allowed)
        matched_pattern: The pattern that matched (None if allowed)
        hint: Suggestion for enabling if blocked (None if allowed)
    """

    allowed: bool
    reason: str | None = None
    matched_pattern: str | None = None
    hint: str | None = None


@dataclass
class SafetyConfig:
    """Configuration for safety validation.

    Attributes:
        profile: Name of the safety profile to use
        allowed_commands: Whitelist of allowed command patterns (supports * wildcards)
        denied_commands: Additional custom patterns to block
        safety_overrides: Fine-grained override settings (for advanced use)
    """

    profile: str = "strict"
    allowed_commands: list[str] = field(default_factory=list)
    denied_commands: list[str] = field(default_factory=list)
    safety_overrides: dict | None = None


# =============================================================================
# Predefined Safety Profiles
# =============================================================================


def _full_blocked_patterns() -> list[BlockPattern]:
    """Return the full set of blocked patterns for strict/standard profiles.

    Using a factory function ensures STRICT and STANDARD each get their own
    independent list instance while sharing the same pattern definitions.
    """
    return [
        # --- Disk / partition operations (command position) ---
        BlockPattern("Format-Volume", "Disk formatting operation", "command"),
        BlockPattern("Clear-Disk", "Disk clearing operation", "command"),
        BlockPattern("Initialize-Disk", "Disk initialization", "command"),
        BlockPattern("Remove-Partition", "Partition removal", "command"),
        # --- System power operations (command position) ---
        BlockPattern("Stop-Computer", "System shutdown", "command"),
        BlockPattern("Restart-Computer", "System restart", "command"),
        # --- Dangerous Remove-Item paths (substring) ---
        BlockPattern(
            "Remove-Item -Recurse -Force /",
            "Root directory deletion",
            "substring",
        ),
        BlockPattern(
            "Remove-Item -Recurse -Force C:\\",
            "Root directory deletion",
            "substring",
        ),
        BlockPattern(
            "Remove-Item -Recurse -Force ~",
            "Home directory deletion",
            "substring",
        ),
        BlockPattern(
            "Remove-Item -Recurse -Force $HOME",
            "Home directory deletion",
            "substring",
        ),
        BlockPattern(
            "Remove-Item -Recurse -Force $env:SystemRoot",
            "System directory deletion",
            "substring",
        ),
        # --- Privilege escalation (substring) ---
        BlockPattern(
            "Start-Process -Verb RunAs",
            "Privilege escalation",
            "substring",
        ),
        # --- Execution policy weakening (substring) ---
        BlockPattern(
            "Set-ExecutionPolicy Unrestricted",
            "Execution policy change",
            "substring",
        ),
        BlockPattern(
            "Set-ExecutionPolicy Bypass",
            "Execution policy change",
            "substring",
        ),
        # --- Registry hive operations (regex) ---
        BlockPattern(
            r"Remove-Item\s.*-Recurse.*HK[A-Z]{2}:",
            "Registry hive deletion",
            "regex",
        ),
        BlockPattern(
            r"Remove-ItemProperty\s.*HK[A-Z]{2}:",
            "Registry property deletion",
            "regex",
        ),
        # --- Pipeline code injection (regex) ---
        BlockPattern(
            r"\|\s*Invoke-Expression",
            "Pipeline code injection",
            "regex",
        ),
        # --- Fork bomb (regex) ---
        BlockPattern(
            r"while\s*\(\s*\$true\s*\)\s*\{.*Start-Process",
            "Fork bomb pattern",
            "regex",
        ),
    ]


STRICT_PROFILE = SafetyProfile(
    name="strict",
    blocked_patterns=_full_blocked_patterns(),
    allow_overrides=False,
)

STANDARD_PROFILE = SafetyProfile(
    name="standard",
    blocked_patterns=_full_blocked_patterns(),
    allow_overrides=True,  # Key difference: allowlist can override
)

PERMISSIVE_PROFILE = SafetyProfile(
    name="permissive",
    blocked_patterns=[
        BlockPattern(
            "Remove-Item -Recurse -Force /",
            "Root directory deletion",
            "substring",
        ),
        BlockPattern(
            "Remove-Item -Recurse -Force C:\\",
            "Root directory deletion",
            "substring",
        ),
        BlockPattern(
            r"while\s*\(\s*\$true\s*\)\s*\{.*Start-Process",
            "Fork bomb pattern",
            "regex",
        ),
    ],
    allow_overrides=True,
)

UNRESTRICTED_PROFILE = SafetyProfile(
    name="unrestricted",
    blocked_patterns=[],
    allow_overrides=True,
)

# Profile registry for lookup by name
PROFILES: dict[str, SafetyProfile] = {
    "strict": STRICT_PROFILE,
    "standard": STANDARD_PROFILE,
    "permissive": PERMISSIVE_PROFILE,
    "unrestricted": UNRESTRICTED_PROFILE,
}


class SafetyValidator:
    """Validates commands against safety rules based on configured profile.

    The validator uses a layered approach where deny rules always take
    priority over allow rules (except the unrestricted bypass):
    1. Unrestricted profile bypasses all checks
    2. Blocked patterns checked with smart matching
       (allowlist can bypass these for profiles with allow_overrides=True)
    3. Custom denied_commands checked — cannot be bypassed by allowlist
    4. Override blocks (safety_overrides.block) checked — cannot be bypassed
    5. Default: allow

    Example:
        >>> validator = SafetyValidator(profile="strict")
        >>> result = validator.validate("Get-Service")
        >>> assert result.allowed

        >>> result = validator.validate("Format-Volume -DriveLetter D")
        >>> assert not result.allowed
        >>> print(result.reason)  # "Disk formatting operation"
    """

    def __init__(self, profile: str = "strict", config: SafetyConfig | None = None):
        """Initialize the safety validator.

        Args:
            profile: Name of the safety profile to use (strict, standard,
                     permissive, unrestricted)
            config: Optional SafetyConfig for additional customization

        Raises:
            ValueError: If profile name is not recognized
        """
        if profile not in PROFILES:
            valid_profiles = ", ".join(PROFILES.keys())
            raise ValueError(
                f"Unknown profile '{profile}'. Valid profiles: {valid_profiles}"
            )

        self.profile = PROFILES[profile]
        self.config = config or SafetyConfig(profile=profile)

        # Extract configuration
        self.allowed_commands = self.config.allowed_commands
        self.denied_commands = self.config.denied_commands

        # Handle safety_overrides for fine-grained control
        self._override_allows: list[str] = []
        self._override_blocks: list[str] = []
        if self.config.safety_overrides:
            self._override_allows = self.config.safety_overrides.get("allow", [])
            self._override_blocks = self.config.safety_overrides.get("block", [])

    def validate(self, command: str) -> SafetyResult:
        """Validate a command against safety rules.

        Args:
            command: The PowerShell command to validate

        Returns:
            SafetyResult indicating whether command is allowed

        Priority order (deny rules checked before allowlist):
            1. Unrestricted profile bypasses all checks
            2. Custom denied_commands always block (highest deny priority)
            3. Override blocks (safety_overrides.block) always block
            4. Allowlist can override profile blocked patterns (if profile allows)
            5. Profile blocked patterns checked with smart matching
            6. Default: allow
        """
        # 1. Unrestricted profile = always allow
        if self.profile.name == "unrestricted":
            return SafetyResult(allowed=True)

        # 2. Check blocked patterns with smart matching.
        # For profiles with allow_overrides=True the allowlist can bypass
        # profile-level blocked patterns — but NOT denied_commands or
        # safety_overrides.block (checked in steps 3-4 below).
        profile_block: BlockPattern | None = None
        for pattern in self.profile.blocked_patterns:
            if self._check_pattern(command, pattern):
                profile_block = pattern
                break

        if profile_block is not None:
            # Only skip the profile block if the profile permits overrides
            # AND the command actually appears on the allowlist.
            if not (
                self.profile.allow_overrides and self._matches_allowlist(command)
            ):
                return SafetyResult(
                    allowed=False,
                    reason=profile_block.reason,
                    matched_pattern=profile_block.pattern,
                    hint="Use safety_profile: 'permissive' or 'unrestricted' for container/VM environments",
                )

        # 3. Check custom denied_commands (supports wildcards).
        # denied_commands CANNOT be bypassed by allowed_commands / allowlist.
        for denied in self.denied_commands:
            if self._matches_wildcard(command, denied):
                return SafetyResult(
                    allowed=False,
                    reason=f"Matches custom denied pattern: {denied}",
                    matched_pattern=denied,
                    hint="Remove from denied_commands to allow this command",
                )

        # 4. Check override blocks (from safety_overrides.block).
        # safety_overrides.block CANNOT be bypassed by allowed_commands / allowlist.
        for block_pattern in self._override_blocks:
            if self._matches_wildcard(command, block_pattern):
                return SafetyResult(
                    allowed=False,
                    reason=f"Blocked by safety_overrides: {block_pattern}",
                    matched_pattern=block_pattern,
                    hint="Remove from safety_overrides.block",
                )

        # 5. Default: allow
        return SafetyResult(allowed=True)

    def _matches_allowlist(self, command: str) -> bool:
        """Check if command matches any allowlist pattern.

        Supports:
        - Exact matches: "Get-Service"
        - Prefix wildcards: "Get-* " matches "Get-Process foo", etc.
        - Pattern wildcards: "Invoke-* -Uri *" etc.
        """
        # Check override allows first (highest priority)
        # Note: substring_fallback=False for allowlist - require exact or wildcard match
        for pattern in self._override_allows:
            if self._matches_wildcard(command, pattern, substring_fallback=False):
                return True

        # Check standard allowed_commands
        for pattern in self.allowed_commands:
            if self._matches_wildcard(command, pattern, substring_fallback=False):
                return True

        return False

    def _matches_wildcard(
        self, command: str, pattern: str, substring_fallback: bool = True
    ) -> bool:
        """Check if command matches a wildcard pattern.

        Args:
            command: The command to check
            pattern: Pattern with optional * wildcards
            substring_fallback: If True and pattern has no wildcards, also try
                substring matching (for backward compatibility with denied_commands)

        Returns:
            True if pattern matches command
        """
        # Exact match (case-insensitive)
        if command.lower() == pattern.lower():
            return True

        # Wildcard matching
        if "*" in pattern:
            # Convert wildcard pattern to regex
            # Escape special regex chars except *
            regex_pattern = re.escape(pattern).replace(r"\*", ".*")
            regex_pattern = f"^{regex_pattern}$"
            if re.match(regex_pattern, command, re.IGNORECASE):
                return True
        elif substring_fallback:
            # No wildcards - try substring matching for backward compatibility
            if pattern.lower() in command.lower():
                return True

        return False

    def _find_quoted_regions(self, command: str) -> list[tuple[int, int]]:
        """Find all single and double quoted regions in a command.

        Handles escaped quotes within strings.

        Args:
            command: The command string to analyze

        Returns:
            List of (start, end) tuples for quoted regions
        """
        regions = []
        i = 0
        while i < len(command):
            if command[i] in ('"', "'"):
                quote_char = command[i]
                start = i
                i += 1
                # Find the closing quote, handling escapes.
                # PowerShell uses backtick (`) as the escape character in
                # double-quoted strings only. Single-quoted strings are
                # completely literal — they have NO escape character.
                while i < len(command):
                    if (
                        quote_char == '"'
                        and command[i] == "`"
                        and i + 1 < len(command)
                    ):
                        # Skip backtick-escaped character (e.g. `" embeds a quote)
                        i += 2
                        continue
                    if command[i] == quote_char:
                        regions.append((start, i + 1))
                        break
                    i += 1
            i += 1
        return regions

    def _in_quoted_region(self, pos: int, regions: list[tuple[int, int]]) -> bool:
        """Check if a position is inside any quoted region.

        Args:
            pos: Character position to check
            regions: List of (start, end) quoted regions

        Returns:
            True if position is inside a quoted string
        """
        for start, end in regions:
            if start < pos < end:
                return True
        return False

    def _is_in_command_position(self, command: str, idx: int) -> bool:
        """Check if position is at start of a command.

        A command position is:
        - Start of the string
        - After PowerShell operators: ; | && || ( $( @( {
        - After a newline (newlines are statement separators in PowerShell)
        - Not inside a quoted string
        - Not on a backtick line continuation

        Args:
            command: The full command string
            idx: Position where the pattern was found

        Returns:
            True if this is a command position
        """
        # Check quoted regions
        quoted_regions = self._find_quoted_regions(command)
        if self._in_quoted_region(idx, quoted_regions):
            return False

        # At start of string (after optional whitespace)
        prefix = command[:idx].strip()
        if not prefix:
            return True

        # Strip trailing horizontal whitespace (spaces, tabs, carriage returns)
        # but NOT newlines, so that "\n" can be matched as a command separator.
        before = command[:idx].rstrip(" \t\r")
        if not before:
            return True

        # Check if the previous non-whitespace line ends with backtick
        # (PowerShell line continuation) — this means the current line is
        # a continuation of the previous command, NOT a new command position
        if before.endswith("`"):
            return False

        # Check what the command portion ends with.
        # PowerShell command starters: ; | && || ( $( @( { ` (backtick
        # continuation) and \n (newline as statement separator).
        command_starters = [";", "|", "&&", "||", "(", "$(", "@(", "{", "`", "\n"]
        for starter in command_starters:
            if before.endswith(starter):
                return True

        # Check if position is at the start of a new line (newlines are
        # statement separators in PowerShell, like semicolons)
        before_with_space = command[:idx]
        # Find the last newline before idx
        last_newline = before_with_space.rfind("\n")
        if last_newline != -1:
            # Check if everything between the newline and idx is whitespace
            between = before_with_space[last_newline + 1 : idx]
            if not between.strip():
                return True

        return False

    def _check_pattern(self, command: str, pattern: BlockPattern) -> bool:
        """Check if a pattern matches the command using appropriate strategy.

        Args:
            command: The command to check
            pattern: The BlockPattern to match against

        Returns:
            True if pattern matches (command should be blocked)
        """
        if pattern.check_type == "substring":
            return self._check_substring(command, pattern.pattern)
        elif pattern.check_type == "command":
            return self._check_command_position(command, pattern.pattern)
        elif pattern.check_type == "regex":
            return self._check_regex(command, pattern.pattern)
        else:
            # Unknown check type, fall back to substring
            return self._check_substring(command, pattern.pattern)

    def _check_substring(self, command: str, pattern: str) -> bool:
        """Simple case-insensitive substring match.

        Args:
            command: The command to check
            pattern: The substring to find

        Returns:
            True if pattern is found in command
        """
        return pattern.lower() in command.lower()

    def _check_command_position(self, command: str, pattern: str) -> bool:
        """Check if pattern appears at a command position.

        This is the smart matching that avoids false positives like:
        - "Write-Output 'Format-Volume'" should NOT match "Format-Volume"
        - "Get-Help Stop-Computer" should NOT match "Stop-Computer"
        - "Set-Content -Value 'Restart-Computer'" should NOT match "Restart-Computer"

        Args:
            command: The command to check
            pattern: The command pattern to match

        Returns:
            True if pattern is found at a command position
        """
        command_lower = command.lower()
        pattern_lower = pattern.lower()

        # Find all occurrences
        start = 0
        while True:
            idx = command_lower.find(pattern_lower, start)
            if idx == -1:
                break

            # Check if this occurrence is at a command position
            if self._is_in_command_position(command, idx):
                # Additional check: for path-containing patterns, verify the
                # match is not part of a longer path component (e.g. an argument)
                if "\\" in pattern or "/" in pattern:
                    # For path-containing patterns, verify the char before is
                    # a separator or whitespace, not part of a longer token
                    if idx > 0:
                        char_before = command[idx - 1]
                        if char_before not in " \t;|&(){}$@`":
                            start = idx + 1
                            continue

                return True

            start = idx + 1

        return False

    def _check_regex(self, command: str, pattern: str) -> bool:
        """Check if regex pattern matches the command.

        The regex is searched anywhere in the command, but patterns
        can be written to be position-aware (e.g., using ^ for start).

        Args:
            command: The command to check
            pattern: The regex pattern to match

        Returns:
            True if pattern matches
        """
        try:
            # Use search, not match, to find pattern anywhere.
            # re.IGNORECASE is required because PowerShell is case-insensitive,
            # so "invoke-expression" must match the "Invoke-Expression" pattern.
            return bool(re.search(pattern, command, re.IGNORECASE))
        except re.error:
            # Invalid regex, treat as no match
            return False
