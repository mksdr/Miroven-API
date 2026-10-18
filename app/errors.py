from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message


def error_response(request: Request, status: int, code: str, message: str):
    headers = {"X-Request-ID": request.state.request_id, "Cache-Control": "no-store"}
    # Unhandled errors run outside user middleware, including CORSMiddleware.
    if request.headers.get("origin") == request.app.state.settings.public_origin:
        headers.update(
            {
                "Access-Control-Allow-Origin": request.app.state.settings.public_origin,
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Expose-Headers": "X-Request-ID",
                "Vary": "Origin",
            }
        )
    return JSONResponse(
        status_code=status,
        content={"code": code, "message": message, "request_id": request.state.request_id},
        headers=headers,
    )


async def api_error_handler(request: Request, exc: ApiError):
    return error_response(request, exc.status, exc.code, exc.message)


async def validation_error_handler(request: Request, exc: RequestValidationError):
    # Never echo input: validation errors can contain email addresses and authentication codes.
    return error_response(request, 422, "invalid_request", "The request is invalid.")


async def http_error_handler(request: Request, exc: HTTPException):
    return error_response(
        request, exc.status_code, "http_error", "The request could not be handled."
    )


async def unexpected_error_handler(request: Request, exc: Exception):
    # Do not log raw exception messages; database errors may embed bound values.
    return error_response(request, 500, "internal_error", "An unexpected error occurred.")
