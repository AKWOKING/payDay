"""Sprint 4 — API Contract & Drift Verification.

Three Figma-driven clients (Flutter, Angular landing, Angular admin) generate
against this OpenAPI document. A silently removed path or renamed field breaks
them at runtime, after release. These tests hold the contract still.

`docs/api/openapi-baseline.json` is the committed snapshot. Adding paths is
fine; removing or renaming them is a breaking change and fails here. Regenerate
the baseline deliberately, in the same commit as the client updates:

    python -c "import json,sys; sys.path.insert(0,'src'); \\
      from payday.main import app; \\
      json.dump(app.openapi(), open('docs/api/openapi-baseline.json','w'), \\
                indent=2, ensure_ascii=False, sort_keys=True)"
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient

from payday.main import app

BASELINE_PATH = Path(__file__).resolve().parents[1] / "docs" / "api" / "openapi-baseline.json"

# Endpoints intentionally reachable without a bearer token.
PUBLIC_PATHS = {
    "/",
    "/api/v1/public/health",
    "/api/v1/public/info",
    "/api/v1/public/fee-calculator",
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/refresh",
    # Provider callbacks authenticate by HMAC signature, not JWT.
    "/api/v1/webhooks/mtn",
    "/api/v1/webhooks/orange",
    # Sandbox-only telco simulators.
    "/api/v1/mock-telco/mtn/simulate-callback",
    "/api/v1/mock-telco/orange/simulate-callback",
}


@pytest.fixture(scope="module")
def spec() -> dict:
    return app.openapi()


@pytest.fixture(scope="module")
def baseline() -> dict:
    assert BASELINE_PATH.exists(), f"Missing contract baseline at {BASELINE_PATH}"
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def test_openapi_is_version_3_1(spec: dict) -> None:
    """Client SDK generators are configured for OpenAPI 3.1."""
    assert spec["openapi"].startswith("3.1"), f"Expected OpenAPI 3.1, got {spec['openapi']}"


def test_no_documented_endpoint_has_been_removed(spec: dict, baseline: dict) -> None:
    """Removing or renaming a path/method breaks already-shipped clients.

    A mobile app in the field cannot be rolled back the way a server can, so
    removals must be caught before release.
    """
    def operations(document: dict) -> set[tuple[str, str]]:
        return {
            (path, method.upper())
            for path, methods in document["paths"].items()
            for method in methods
            if method.lower() in {"get", "post", "put", "patch", "delete"}
        }

    removed = operations(baseline) - operations(spec)
    assert not removed, (
        "Breaking contract change — these operations vanished:\n"
        + "\n".join(f"  - {method} {path}" for path, method in sorted(removed))
    )


def test_no_new_required_request_field_on_existing_endpoints(
    spec: dict, baseline: dict
) -> None:
    """Adding a required field to an existing request body is breaking.

    Older clients keep sending the previous payload and start getting 422s.
    """
    def required_by_schema(document: dict) -> dict[str, set[str]]:
        schemas = document.get("components", {}).get("schemas", {})
        return {
            name: set(schema.get("required", []))
            for name, schema in schemas.items()
        }

    old = required_by_schema(baseline)
    new = required_by_schema(spec)

    breaking: list[str] = []
    for name, old_required in old.items():
        if name not in new:
            continue
        added = new[name] - old_required
        if added:
            breaking.append(f"  - {name}: newly required {sorted(added)}")

    assert not breaking, "Breaking contract change — new required fields:\n" + "\n".join(breaking)


def test_bearer_security_scheme_is_declared(spec: dict) -> None:
    """Generators need the auth scheme to emit an authenticated client."""
    schemes = spec.get("components", {}).get("securitySchemes", {})
    assert schemes, "No securitySchemes declared; generated SDKs cannot authenticate"

    bearer = [
        name
        for name, scheme in schemes.items()
        if scheme.get("type") == "http" and scheme.get("scheme", "").lower() == "bearer"
    ]
    assert bearer, f"No HTTP bearer scheme found; got {list(schemes)}"


def test_protected_endpoints_declare_security(spec: dict) -> None:
    """Every non-public operation must advertise its auth requirement.

    Without this the generated client omits the Authorization header and every
    call 401s.
    """
    undeclared: list[str] = []
    global_security = spec.get("security")

    for path, methods in spec["paths"].items():
        if path in PUBLIC_PATHS:
            continue
        for method, operation in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not operation.get("security") and not global_security:
                undeclared.append(f"  - {method.upper()} {path}")

    assert not undeclared, (
        "Protected operations without a declared security requirement:\n"
        + "\n".join(sorted(undeclared))
    )


def test_problem_detail_schema_is_published(spec: dict) -> None:
    """RFC 7807 is the documented error format, so clients need the schema."""
    schemas = spec.get("components", {}).get("schemas", {})
    assert "ProblemDetail" in schemas, (
        f"ProblemDetail is not published; available: {sorted(schemas)[:15]}"
    )

    properties = schemas["ProblemDetail"].get("properties", {})
    for field in ("type", "title", "status", "detail", "instance"):
        assert field in properties, f"ProblemDetail is missing the RFC 7807 field {field!r}"


def test_every_operation_has_a_unique_operation_id(spec: dict) -> None:
    """Duplicate operationIds make SDK generators emit colliding methods."""
    seen: dict[str, str] = {}
    duplicates: list[str] = []

    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_id = operation.get("operationId")
            assert operation_id, f"{method.upper()} {path} has no operationId"
            if operation_id in seen:
                duplicates.append(f"  - {operation_id}: {seen[operation_id]} and {method.upper()} {path}")
            seen[operation_id] = f"{method.upper()} {path}"

    assert not duplicates, "Duplicate operationIds:\n" + "\n".join(duplicates)


@pytest.mark.asyncio
async def test_validation_errors_are_rfc7807_at_runtime(client: AsyncClient) -> None:
    """The documented error shape must match what the server actually sends."""
    response = await client.post(
        "/api/v1/public/fee-calculator",
        json={"type": "DEPOSIT"},  # `channel` and `amount` omitted
    )
    assert response.status_code == 422

    body = response.json()
    for field in ("type", "title", "status", "detail", "instance", "code"):
        assert field in body, f"422 body is missing the RFC 7807 field {field!r}: {body}"
    assert body["status"] == 422
    assert body["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_auth_errors_are_rfc7807_at_runtime(client: AsyncClient) -> None:
    """401s must carry the same Problem Details envelope as everything else."""
    response = await client.get(
        "/api/v1/wallet/balance", headers={"Authorization": "Bearer invalid.token.here"}
    )
    assert response.status_code == 401

    body = response.json()
    for field in ("type", "title", "status", "detail"):
        assert field in body, f"401 body is missing {field!r}: {body}"
    assert body["status"] == 401


@pytest.mark.asyncio
async def test_served_spec_matches_in_process_spec(client: AsyncClient, spec: dict) -> None:
    """/openapi.json must serve exactly the document the app builds."""
    response = await client.get("/openapi.json")
    assert response.status_code == 200

    served = response.json()
    assert set(served["paths"]) == set(spec["paths"]), (
        "The served OpenAPI document diverges from the in-process one"
    )
