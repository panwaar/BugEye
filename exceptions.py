class BugEyeError(Exception):
    """An expected failure whose message is safe to show to end users.

    Anything that is *not* a BugEyeError is treated as an internal error: it is logged
    server-side and the user only sees a generic message.
    """

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code
