"""S&L-specific exceptions."""

from aionanit_jr.exceptions import NanitError


class NanitTransportError(NanitError):
    """WebSocket transport error for the S&L device."""
