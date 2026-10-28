from __future__ import annotations


class ToolkitError(Exception):
    """Base error for the toolkit."""


class ConfigurationError(ToolkitError):
    """Raised when configuration is invalid."""


class DataValidationError(ToolkitError):
    """Raised when input data cannot be validated or normalized safely."""
