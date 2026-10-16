class AppError(Exception):
    """Error con un mensaje claro para el conductor (en español, sin tecnicismos)."""

    def __init__(self, message: str, status: int = 400, code: str = "error"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
