"""Canonical calculation, interpretation, applicability, and severity states."""

from __future__ import annotations

from enum import Enum


class StringEnum(str, Enum):
    """Enumerate supported string enum values."""
    def __str__(self) -> str:
        return self.value


class Layer1Status(StringEnum):
    """Represent layer1 status state and behavior."""
    NOT_RUN = "NOT RUN"
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    STALE = "STALE"


class CalculationStatus(StringEnum):
    """Represent calculation status state and behavior."""
    NOT_RUN = "not_run"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    BLOCKED = "blocked"
    FAILED = "failed"
    STALE = "stale"
    NOT_IMPLEMENTED = "not_implemented"
    NOT_ASSESSED = "not_assessed"
    OUTSIDE_VALIDATED_SCOPE = "outside_validated_scope"


class InterpretationStatus(StringEnum):
    """Represent interpretation status state and behavior."""
    NOT_INTERPRETABLE = "not_interpretable"
    PROVISIONAL = "provisional"
    PROTOCOL_INTERPRETABLE = "protocol_interpretable"


class Applicability(StringEnum):
    """Represent applicability state and behavior."""
    VALID = "valid"
    INVALID = "invalid"
    NOT_APPLICABLE = "not_applicable"
    APPLICABLE = "applicable"
    BLOCKED = "blocked"
    NOT_ASSESSED = "not_assessed"


class Severity(StringEnum):
    """Represent severity state and behavior."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
