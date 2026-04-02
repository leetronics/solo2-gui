"""Error types for the Solo 2 core library."""


class Solo2Error(Exception):
    """Base error for Solo 2 core operations."""


class Solo2NotFoundError(Solo2Error):
    """Raised when no suitable Solo 2 device is available."""


class Solo2TransportError(Solo2Error):
    """Raised when a transport call fails."""
