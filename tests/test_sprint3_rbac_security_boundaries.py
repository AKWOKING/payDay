from datetime import timedelta
import pytest
from httpx import AsyncClient
from payday.models.user import User
from payday.core.security import create_access_token


@pytest.mark.asyncio
async def test_admin_rbac_unauthenticated_rejection(
    client: AsyncClient,
    test_user: User,
):
    """
    RBAC Boundary Test 1: Unauthenticated Requests
    Calls all Sprint 3 admin endpoints with NO authorization header; verifies HTTP 401 rejection.
    """
    admin_endpoints = [
        ("GET", "/api/v1/admin/transactions"),
        ("POST", "/api/v1/admin/transactions/00000000-0000-0000-0000-000000000000/reverse", {"reason": "Test"}),
        ("POST", "/api/v1/admin/reconcile", {"channel": "MTN"}),
        ("GET", "/api/v1/admin/audit-logs"),
    ]

    for method, path, *payload in admin_endpoints:
        body = payload[0] if payload else None
        if method == "GET":
            res = await client.get(path)
        else:
            res = await client.post(path, json=body)

        assert res.status_code == 401, f"Expected 401 for unauthenticated {method} {path}, got {res.status_code}"
        assert res.json()["code"] == "AUTHENTICATION_FAILED"


@pytest.mark.asyncio
async def test_admin_rbac_customer_permission_denied(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
):
    """
    RBAC Boundary Test 2: Standard CUSTOMER Role Access Attempt
    Calls all Sprint 3 admin endpoints with valid Customer JWT; verifies HTTP 403 PERMISSION_DENIED.
    """
    admin_endpoints = [
        ("GET", "/api/v1/admin/transactions"),
        ("POST", "/api/v1/admin/transactions/00000000-0000-0000-0000-000000000000/reverse", {"reason": "Test"}),
        ("POST", "/api/v1/admin/reconcile", {"channel": "MTN"}),
        ("GET", "/api/v1/admin/audit-logs"),
    ]

    for method, path, *payload in admin_endpoints:
        body = payload[0] if payload else None
        if method == "GET":
            res = await client.get(path, headers=user_auth_headers)
        else:
            res = await client.post(path, json=body, headers=user_auth_headers)

        assert res.status_code == 403, f"Expected 403 for Customer accessing {method} {path}, got {res.status_code}"
        assert res.json()["code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_admin_rbac_expired_token_rejection(
    client: AsyncClient,
    test_admin: User,
):
    """
    RBAC Boundary Test 3: Expired ADMIN JWT Access Attempt
    Generates an expired Admin JWT (timestamp in past); verifies HTTP 401 rejection.
    """
    expired_token = create_access_token(
        subject=test_admin.user_id,
        role=test_admin.role.value,
        expires_delta=timedelta(minutes=-10), # Expired 10 mins ago
    )
    expired_headers = {"Authorization": f"Bearer {expired_token}"}

    admin_endpoints = [
        ("GET", "/api/v1/admin/transactions"),
        ("POST", "/api/v1/admin/reconcile", {"channel": "MTN"}),
        ("GET", "/api/v1/admin/audit-logs"),
    ]

    for method, path, *payload in admin_endpoints:
        body = payload[0] if payload else None
        if method == "GET":
            res = await client.get(path, headers=expired_headers)
        else:
            res = await client.post(path, json=body, headers=expired_headers)

        assert res.status_code == 401, f"Expected 401 for expired admin token on {method} {path}, got {res.status_code}"
        assert res.json()["code"] == "AUTHENTICATION_FAILED"
