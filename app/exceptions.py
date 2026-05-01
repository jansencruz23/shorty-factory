"""Project-wide exception hierarchy.

Two-level tree: ShortyError as the root, then category bases (ProviderError,
PipelineError, ConfigError) for the runner to branch on. Concrete provider
exceptions (e.g. MetaSessionExpired) subclass the category bases so callers
can catch by category without knowing which provider raised.
"""

from __future__ import annotations


class ShortyError(Exception):
    """Root for all project-defined exceptions."""


class ProviderError(ShortyError):
    """A backend provider (video, music, llm) failed in a known way."""

    provider: str = ""


class ProviderRateLimited(ProviderError):
    """Transient throttling. Retry after backoff is appropriate."""


class ProviderSessionExpired(ProviderError):
    """Authentication state is no longer valid. Operator action required
    (e.g. re-run capture_session.py); retrying alone won't recover."""


class ProviderUIChanged(ProviderError):
    """A provider that scrapes a UI saw an unexpected layout. Code change
    required (typically a selector update)."""


class ProviderQuotaExceeded(ProviderError):
    """Daily/monthly cap reached. Wait until the quota window resets."""


class PipelineError(ShortyError):
    """An ffmpeg / file / IO step in the local pipeline failed."""


class ConfigError(ShortyError):
    """A required config or asset is missing/invalid (font, music dir, etc.)."""


# Mapping for the runner's webhook payload — gives n8n a stable token to
# branch on without reflecting on Python class names.
ERROR_TYPE_BY_CLASS: dict[type[Exception], str] = {
    ProviderRateLimited: "rate_limited",
    ProviderSessionExpired: "session_expired",
    ProviderUIChanged: "ui_changed",
    ProviderQuotaExceeded: "quota_exceeded",
    PipelineError: "pipeline",
    ConfigError: "config",
}


def classify_error(exc: BaseException) -> str:
    """Return the most specific error_type token for `exc`, falling back
    to 'unknown' when the exception is not in the project hierarchy."""
    for cls, token in ERROR_TYPE_BY_CLASS.items():
        if isinstance(exc, cls):
            return token
    if isinstance(exc, ProviderError):
        return "provider"
    if isinstance(exc, ShortyError):
        return "shorty"
    return "unknown"
