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


from fastapi.responses import JSONResponse, HTMLResponse

# Root endpoint with interactive preview HTML
@app.get("/", response_class=HTMLResponse, tags=["General"])
async def root():
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{settings.PROJECT_NAME} — API & Platform Preview</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.2/css/all.min.css">
        <style>
            body {{
                background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
                color: #f8fafc;
                min-height: 100vh;
                font-family: system-ui, -apple-system, sans-serif;
            }}
            .card {{
                background: rgba(30, 41, 59, 0.7);
                backdrop-filter: blur(12px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 16px;
            }}
            .badge-live {{
                background: #10b981;
                color: #ffffff;
                box-shadow: 0 0 12px rgba(16, 185, 129, 0.4);
            }}
            .channel-card {{
                background: rgba(15, 23, 42, 0.6);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
                padding: 1rem;
                transition: transform 0.2s;
            }}
            .channel-card:hover {{
                transform: translateY(-3px);
            }}
            .btn-accent {{
                background: #2563eb;
                color: white;
                font-weight: 600;
                border-radius: 8px;
            }}
            .btn-accent:hover {{
                background: #1d4ed8;
                color: white;
            }}
        </style>
    </head>
    <body class="py-5">
        <div class="container">
            <div class="row justify-content-center">
                <div class="col-lg-10">
                    <div class="text-center mb-5">
                        <span class="badge badge-live px-3 py-2 mb-3 rounded-pill">
                            <i class="fa-solid fa-circle fa-beat-fade me-1 text-light"></i> SERVER ONLINE • XAF (Cameroon)
                        </span>
                        <h1 class="display-4 fw-bold">PayDay e-Wallet API</h1>
                        <p class="lead text-secondary">
                            Integrated Multi-Channel Electronic Banking System for <strong>MTN Mobile Money</strong>, <strong>Orange Money</strong> & <strong>UBA Bank</strong>
                        </p>
                        <p class="badge bg-warning text-dark px-3 py-2">EVERYDAY IS A PAYDAY</p>
                    </div>

                    <div class="card p-4 shadow-lg mb-4">
                        <h4 class="mb-3"><i class="fa-solid fa-book-open text-primary me-2"></i>Developer Documentation & Contracts</h4>
                        <p class="text-secondary">Interactive OpenAPI 3.1 contracts ready for <strong>Flutter Mobile</strong> and <strong>Angular Web / Admin</strong> clients:</p>
                        <div class="d-flex flex-wrap gap-3 mb-3">
                            <a href="/docs" target="_blank" class="btn btn-accent px-4 py-2">
                                <i class="fa-solid fa-bolt me-2"></i>Swagger UI (/docs)
                            </a>
                            <a href="/redoc" target="_blank" class="btn btn-outline-light px-4 py-2">
                                <i class="fa-solid fa-file-code me-2"></i>ReDoc (/redoc)
                            </a>
                            <a href="/openapi.json" target="_blank" class="btn btn-outline-secondary px-3 py-2">
                                <i class="fa-solid fa-code me-2"></i>OpenAPI JSON
                            </a>
                            <a href="/api/v1/public/health" target="_blank" class="btn btn-outline-success px-3 py-2">
                                <i class="fa-solid fa-heart-pulse me-2"></i>Health Probe
                            </a>
                        </div>
                    </div>

                    <div class="row g-3 mb-4">
                        <div class="col-md-4">
                            <div class="channel-card h-100">
                                <div class="d-flex justify-content-between align-items-center mb-2">
                                    <h5 class="mb-0 text-warning"><i class="fa-solid fa-mobile-screen-button me-2"></i>MTN MoMo</h5>
                                    <span class="badge bg-success">Adapter Active</span>
                                </div>
                                <small class="text-secondary">RequestToPay collection & Transfer disbursement API ready for Sprint 2.</small>
                            </div>
                        </div>
                        <div class="col-md-4">
                            <div class="channel-card h-100">
                                <div class="d-flex justify-content-between align-items-center mb-2">
                                    <h5 class="mb-0 text-warning" style="color: #f97316 !important;"><i class="fa-solid fa-wallet me-2"></i>Orange Money</h5>
                                    <span class="badge bg-success">Adapter Active</span>
                                </div>
                                <small class="text-secondary">Web Payment initiation & Payout webhook listeners ready for Sprint 3.</small>
                            </div>
                        </div>
                        <div class="col-md-4">
                            <div class="channel-card h-100">
                                <div class="d-flex justify-content-between align-items-center mb-2">
                                    <h5 class="mb-0 text-danger"><i class="fa-solid fa-building-columns me-2"></i>UBA Bank</h5>
                                    <span class="badge bg-secondary">Phase 2</span>
                                </div>
                                <small class="text-secondary">Extensible Triangle model ready for bank integration.</small>
                            </div>
                        </div>
                    </div>

                    <div class="card p-4 shadow-sm">
                        <h5 class="mb-3"><i class="fa-solid fa-shield-halved text-success me-2"></i>Sprint 1 Architecture & Ledger Status</h5>
                        <ul class="list-unstyled text-secondary mb-0">
                            <li class="mb-2"><i class="fa-solid fa-check text-success me-2"></i><strong>Pessimistic Concurrency:</strong> Row-level locking (<code>SELECT FOR UPDATE</code>) validated against 50 parallel withdrawal attacks (Zero double-spending).</li>
                            <li class="mb-2"><i class="fa-solid fa-check text-success me-2"></i><strong>PII Encryption at Rest:</strong> National ID & Passport credentials encrypted with AES-256-GCM.</li>
                            <li class="mb-2"><i class="fa-solid fa-check text-success me-2"></i><strong>Ledger Invariant:</strong> Balance mathematically bounded to transaction log records with database check constraints.</li>
                            <li class="mb-2"><i class="fa-solid fa-check text-success me-2"></i><strong>Multi-Client Support:</strong> JWT authentication & CORS pre-configured for Flutter & Angular.</li>
                        </ul>
                    </div>

                    <footer class="text-center text-secondary mt-5">
                        <small>PayDay e-Wallet • Cameroon (XAF) • Sprint 1 Completed</small>
                    </footer>
                </div>
            </div>
        </div>
    </body>
    </html>
    """


# Include API v1 router
app.include_router(api_router, prefix=settings.API_V1_STR)
