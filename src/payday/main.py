from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError

from payday.core.config import settings
from payday.core.database import Base, engine
from payday.core.exceptions import PayDayException
from payday.core.logging import logger
from payday.schemas.common import ProblemDetail
from payday.api.v1.router import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create tables if not existing (useful for local dev / testing)
    logger.info(f"Starting {settings.PROJECT_NAME} v{settings.VERSION} [{settings.ENVIRONMENT}]")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema initialized.")
    yield
    # Shutdown
    logger.info("Shutting down PayDay backend.")
    await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="""
# PayDay — Integrated e-Wallet API (Republic of Cameroon)

**EVERYDAY IS A PAYDAY**

PayDay is an integrated, neutral e-wallet bridging **MTN Mobile Money**, **Orange Money**, and **UBA Bank** using the **Triangle Model**.

### Client Integrations:
- **Flutter Mobile App:** End-customer mobile wallet on Android & iOS.
- **Angular Landing Page:** Public marketing portal, demo simulator, and fee calculator.
- **Angular Admin Dashboard:** Operations portal for KYC approvals, ledger audits, wallet freezes, and settlement reconciliation.
    """,
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS Configuration for Angular & Flutter
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Permissive for multi-client & sandbox preview
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception Handlers (RFC 7807)
@app.exception_handler(PayDayException)
async def payday_exception_handler(request: Request, exc: PayDayException):
    problem = ProblemDetail(
        type=f"https://payday.cm/errors/{exc.code.lower().replace('_', '-')}",
        title=exc.title,
        status=exc.status_code,
        detail=exc.detail,
        instance=str(request.url.path),
        code=exc.code,
        extra=exc.extra,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=problem.model_dump(mode="json"),
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    details_str = "; ".join([f"{e.get('loc', ['field'])[-1]}: {e.get('msg')}" for e in errors])
    problem = ProblemDetail(
        type="https://payday.cm/errors/validation-error",
        title="Request Validation Error",
        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=details_str,
        instance=str(request.url.path),
        code="VALIDATION_ERROR",
        extra={"errors": errors},
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=problem.model_dump(mode="json"),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled exception on {request.url.path}: {exc}")
    problem = ProblemDetail(
        type="https://payday.cm/errors/internal-server-error",
        title="Internal Server Error",
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="An unexpected error occurred while processing your request.",
        instance=str(request.url.path),
        code="INTERNAL_SERVER_ERROR",
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=problem.model_dump(mode="json"),
    )


# Root endpoint
@app.get("/", tags=["General"])
async def root():
    return {
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "ONLINE",
        "documentation": "/docs",
        "openapi_spec": "/openapi.json",
    }


# Include API v1 router
app.include_router(api_router, prefix=settings.API_V1_STR)
