class BugEyeError(Exception):
    """An expected failure whose message is safe to show to end users.

    Anything that is *not* a BugEyeError is treated as an internal error: it is logged
    server-side and the user only sees a generic message.
    """

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class RateLimitedError(BugEyeError):
    """The LLM provider refused a request because a rate limit was reached."""

    def __init__(self, message: str, *, retry_after: float | None = None, daily: bool = False):
        super().__init__(message, status_code=429)
        self.retry_after = retry_after  # Seconds until the limit frees up, when the provider says
        self.daily = daily  # A daily quota: waiting a few seconds will not help
