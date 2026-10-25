"""Typed errors with source offsets suitable for Blender diagnostics."""

from __future__ import annotations


class Kz2AssetError(Exception):
    """Base class for importer failures."""


class FormatError(Kz2AssetError):
    """Raised when a binary asset violates the supported format contract."""

    def __init__(
        self,
        message: str,
        *,
        source: str | None = None,
        offset: int | None = None,
        expected: object | None = None,
        observed: object | None = None,
    ) -> None:
        self.message = message
        self.source = source
        self.offset = offset
        self.expected = expected
        self.observed = observed

        details: list[str] = []
        if source:
            details.append(source)
        if offset is not None:
            details.append(f"offset 0x{offset:x}")
        if expected is not None:
            details.append(f"expected {expected!r}")
        if observed is not None:
            details.append(f"observed {observed!r}")

        suffix = f" ({', '.join(details)})" if details else ""
        super().__init__(message + suffix)


class UnsupportedFeatureError(Kz2AssetError):
    """Raised for a valid KZ2 feature that the importer cannot decode."""
