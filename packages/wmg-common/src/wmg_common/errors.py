"""JSON error envelope used by every HTTP API: {"error": {"code": "UPPER_SNAKE", "message": "..."}}
(DESIGN §7.3, §20). Messages are for humans and must never contain secrets or stack traces."""

from pydantic import BaseModel
from starlette.responses import JSONResponse


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorBody(BaseModel):
    error: ErrorDetail


class AppError(Exception):
    """Raise from handlers to return a clean JSON error with the given HTTP status."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def error_response(status: int, code: str, message: str) -> JSONResponse:
    body = ErrorBody(error=ErrorDetail(code=code, message=message))
    return JSONResponse(body.model_dump(), status_code=status)
