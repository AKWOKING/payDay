"""Sprint 4 — Security Review (OWASP API Top 10 oriented).

Sprint 3 established RBAC boundaries. This file goes after the adjacent
failure modes: token forgery, object-level authorisation, mass assignment,
injection, PII leakage, and transport/CORS configuration.

One finding here was live and is now fixed — see
`test_cors_does_not_reflect_arbitrary_origins`. `main.py` previously passed
`allow_origins=["*"]` together with `allow_credentials=True`, so Starlette
echoed any caller's Origin back with
`Access-Control-Allow-Credentials: true`, letting any website issue
credentialed cross-origin calls to this financial API. The
`BACKEND_CORS_ORIGINS` allowlist existed in config but was never referenced.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import jose.jwt as jose_jwt
import pytest
from httpx import ASGITransport, AsyncClient

from payday.core.config import settings
from payday.core.security import create_access_token
from payday.main import app
from payday.models.user import User

PASSWORD = "SecretP@ssword123"


# --------------------------------------------------------------------------- #
# API2:2023 — Broken Authentication
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_tampered_jwt_signature_is_rejected(
    client: AsyncClient, test_user: User, user_auth_headers: dict
) -> None:
    """A token re-signed with an attacker's key must not authenticate."""
    valid = user_auth_headers["Authorization"].split(" ", 1)[1]
    claims = jose_jwt.get_unverified_claims(valid)

    forged = jose_jwt.encode(claims, "attacker-controlled-key", algorithm="HS256")

    response = await client.get(
        "/api/v1/wallet/balance", headers={"Authorization": f"Bearer {forged}"}
    )
    assert response.status_code == 401, f"Forged token accepted: {response.text}"


@pytest.mark.asyncio
async def test_alg_none_token_is_rejected(
    client: AsyncClient, test_user: User, user_auth_headers: dict
) -> None:
    """The classic `alg: none` downgrade must not bypass verification."""
    valid = user_auth_headers["Authorization"].split(" ", 1)[1]
    claims = jose_jwt.get_unverified_claims(valid)

    try:
        unsigned = jose_jwt.encode(claims, key="", algorithm="none")
    except Exception:
        pytest.skip("Local JOSE library refuses to mint alg=none tokens")

    response = await client.get(
        "/api/v1/wallet/balance", headers={"Authorization": f"Bearer {unsigned}"}
    )
    assert response.status_code == 401, f"alg=none token accepted: {response.text}"


@pytest.mark.asyncio
async def test_expired_token_is_rejected(client: AsyncClient, test_user: User) -> None:
    """Expiry must be enforced, not merely present in the claims."""
    expired = jose_jwt.encode(
        {
            "sub": test_user.user_id,
            "role": test_user.role.value,
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    response = await client.get(
        "/api/v1/wallet/balance", headers={"Authorization": f"Bearer {expired}"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_malformed_authorization_headers_are_rejected(
    client: AsyncClient, test_user: User
) -> None:
    """Malformed credentials must yield 401/403 — never 500 or success."""
    for header in (
        "Bearer",
        "Bearer ",
        "Basic YWRtaW46YWRtaW4=",
        "Bearer not.a.jwt",
        "Bearer ../../etc/passwd",
        f"Bearer {'A' * 5000}",
    ):
        response = await client.get(
            "/api/v1/wallet/balance", headers={"Authorization": header}
        )
        assert response.status_code in (401, 403), (
            f"Header {header[:40]!r} produced {response.status_code}"
        )


# --------------------------------------------------------------------------- #
# API1:2023 — Broken Object Level Authorisation
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_user_cannot_read_another_users_transaction(
    client: AsyncClient, test_user: User, user_auth_headers: dict, db_session
) -> None:
    """Horizontal escalation: victim's transaction must not be readable.

    The attacker is authenticated and simply substitutes a transaction id
    belonging to someone else — the single most common real-world API flaw.
    """
    from decimal import Decimal

    from payday.core.security import get_password_hash
    from payday.models.user import KycStatus, UserRole, UserStatus
    from payday.models.wallet import Wallet, WalletStatus

    victim = User(
        full_name="Victim User",
        phone_number="+237655443322",
        email="victim@example.cm",
        password_hash=get_password_hash(PASSWORD),
        kyc_status=KycStatus.VERIFIED,
        role=UserRole.CUSTOMER,
        status=UserStatus.ACTIVE,
    )
    db_session.add(victim)
    await db_session.flush()
    db_session.add(
        Wallet(
            user_id=victim.user_id,
            balance=Decimal("90000.00"),
            locked_balance=Decimal("0.00"),
            currency="XAF",
            status=WalletStatus.ACTIVE,
            daily_limit=Decimal("500000.00"),
            monthly_limit=Decimal("5000000.00"),
        )
    )
    await db_session.commit()

    victim_token = create_access_token(
        subject=victim.user_id,
        role=victim.role.value,
        token_version=victim.token_version,
    )
    victim_headers = {"Authorization": f"Bearer {victim_token}"}

    created = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 12345.00, "phone_number": "+237655443322"},
        headers=victim_headers,
    )
    assert created.status_code == 202, created.text
    victim_tx_id = created.json()["data"]["transaction_id"]

    stolen = await client.get(
        f"/api/v1/wallet/transactions/{victim_tx_id}", headers=user_auth_headers
    )
    assert stolen.status_code in (403, 404), (
        f"Cross-user transaction read returned {stolen.status_code}: {stolen.text[:300]}"
    )
    assert "12345" not in stolen.text, "Victim's transaction amount leaked in the error body"


# --------------------------------------------------------------------------- #
# API3:2023 — Broken Object Property Level Authorisation (mass assignment)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_registration_cannot_self_assign_admin_role(client: AsyncClient) -> None:
    """Privilege fields in the request body must be ignored at registration."""
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Privilege Escalator",
            "phone_number": "+237699000111",
            "email": "escalate@example.cm",
            "password": PASSWORD,
            "id_document_no": "123456789",
            "id_document_type": "NATIONAL_ID",
            # Injected privilege fields — must not be honoured.
            "role": "ADMIN",
            "kyc_status": "VERIFIED",
            "status": "ACTIVE",
        },
    )
    assert response.status_code in (200, 201), response.text

    body = response.json()
    payload = body.get("data", body)
    assert str(payload.get("role", "CUSTOMER")).upper() != "ADMIN", (
        "Registration allowed self-assignment of the ADMIN role"
    )
    # KYC must still require review rather than being granted on request.
    assert str(payload.get("kyc_status", "PENDING")).upper() != "VERIFIED", (
        "Registration allowed self-assignment of VERIFIED KYC status"
    )


# --------------------------------------------------------------------------- #
# Injection
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_sql_injection_payloads_are_neutralised(
    client: AsyncClient, test_user: User, user_auth_headers: dict
) -> None:
    """Injection strings must never reach the database as SQL.

    A 500 here would suggest string-built SQL; the ORM should either reject
    the input or treat it as an inert literal.
    """
    payloads = [
        "' OR '1'='1",
        "'; DROP TABLE transactions; --",
        "1 UNION SELECT password_hash FROM users",
        "%27%20OR%201%3D1",
    ]

    for payload in payloads:
        response = await client.get(
            f"/api/v1/wallet/transactions/{payload}", headers=user_auth_headers
        )
        assert response.status_code != 500, (
            f"Injection payload {payload!r} caused a server error"
        )
        # Check for actual leaked credential material rather than the payload
        # echoed back: the 404 body quotes the requested id, so searching for
        # "password_hash" would match the attacker's own input.
        assert "$2b$" not in response.text, f"bcrypt hash leaked for payload {payload!r}"
        assert "SecretP@ssword123" not in response.text
        assert re.search(r'"(password|pin)_hash"\s*:', response.text) is None, (
            f"A credential field was serialised into the response for {payload!r}"
        )

    # The table must still be there.
    survived = await client.get("/api/v1/wallet/transactions", headers=user_auth_headers)
    assert survived.status_code == 200, "transactions table did not survive injection attempts"


# --------------------------------------------------------------------------- #
# Sensitive data exposure
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_no_endpoint_leaks_credentials_or_raw_pii(
    client: AsyncClient, test_user: User, user_auth_headers: dict
) -> None:
    """Hashes and raw identity documents must never appear in responses."""
    forbidden = ("password_hash", "pin_hash", "$2b$", "SecretP@ssword123")

    for path in ("/api/v1/auth/me", "/api/v1/wallet/me", "/api/v1/wallet/balance"):
        response = await client.get(path, headers=user_auth_headers)
        assert response.status_code == 200, f"{path} -> {response.status_code}"
        for needle in forbidden:
            assert needle not in response.text, f"{path} leaked {needle!r}"


@pytest.mark.asyncio
async def test_kyc_document_number_is_masked_in_responses(
    client: AsyncClient, test_user: User, user_auth_headers: dict
) -> None:
    """The national ID is encrypted at rest; the API must only return a mask."""
    document_number = "108273948"

    submitted = await client.post(
        "/api/v1/kyc/submit",
        json={"id_document_no": document_number, "id_document_type": "NATIONAL_ID"},
        headers=user_auth_headers,
    )
    assert submitted.status_code in (200, 201, 202), submitted.text

    status = await client.get("/api/v1/kyc/status", headers=user_auth_headers)
    assert status.status_code == 200
    assert document_number not in status.text, "Raw national ID returned in plaintext"

    masked = status.json()["data"]["id_document_masked"]
    assert re.search(r"[*x•]", masked, re.IGNORECASE), f"ID does not look masked: {masked!r}"


# --------------------------------------------------------------------------- #
# API8:2023 — Security Misconfiguration
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_cors_does_not_reflect_arbitrary_origins(test_user: User) -> None:
    """Regression guard for the credentialed-wildcard CORS finding.

    `allow_origins=["*"]` with `allow_credentials=True` makes Starlette reflect
    the caller's Origin and set `Access-Control-Allow-Credentials: true`, so
    any site could call this API cross-origin with credentials and read the
    response. Uses a bare transport because CORS is middleware-level.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as raw:
        hostile = await raw.get(
            "/api/v1/public/health", headers={"Origin": "https://evil-attacker.example"}
        )
        allowed_origin = hostile.headers.get("access-control-allow-origin")
        assert allowed_origin != "https://evil-attacker.example", (
            "API reflects arbitrary origins — any website can read authenticated responses"
        )
        assert allowed_origin != "*" or (
            hostile.headers.get("access-control-allow-credentials") != "true"
        ), "Wildcard CORS combined with credentials"

        # A configured origin must still work, or the Angular clients break.
        legitimate = await raw.get(
            "/api/v1/public/health", headers={"Origin": "http://localhost:4200"}
        )
        assert (
            legitimate.headers.get("access-control-allow-origin") == "http://localhost:4200"
        ), "Configured Angular dev origin is no longer allowed"


@pytest.mark.asyncio
async def test_errors_do_not_expose_stack_traces(
    client: AsyncClient, test_user: User, user_auth_headers: dict
) -> None:
    """Error bodies must stay RFC 7807 and reveal no internals."""
    response = await client.get(
        "/api/v1/wallet/transactions/00000000-0000-0000-0000-000000000000",
        headers=user_auth_headers,
    )
    assert response.status_code >= 400

    body = response.text
    for leak in ("Traceback", "sqlalchemy", "/home/", "site-packages", "SELECT "):
        assert leak not in body, f"Error response leaked internals: {leak!r}"


@pytest.mark.asyncio
async def test_unauthenticated_access_is_refused_across_protected_surface(
    client: AsyncClient,
) -> None:
    """Every protected route must refuse anonymous callers.

    Catches a route added later without an auth dependency.
    """
    protected = [
        ("GET", "/api/v1/wallet/balance"),
        ("GET", "/api/v1/wallet/me"),
        ("GET", "/api/v1/wallet/transactions"),
        ("GET", "/api/v1/auth/me"),
        ("GET", "/api/v1/kyc/status"),
        ("GET", "/api/v1/notifications"),
        ("GET", "/api/v1/admin/users"),
        ("GET", "/api/v1/admin/transactions"),
        ("GET", "/api/v1/admin/audit-logs"),
        ("POST", "/api/v1/admin/reconcile"),
    ]

    for method, path in protected:
        response = await client.request(method, path)
        assert response.status_code in (401, 403), (
            f"{method} {path} returned {response.status_code} without credentials"
        )
