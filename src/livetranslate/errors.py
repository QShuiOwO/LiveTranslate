class LiveTranslateError(Exception):
    """Expected operational failure, safe to report to a CLI user."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)
