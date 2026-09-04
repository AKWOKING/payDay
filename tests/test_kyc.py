import pytest
from httpx import AsyncClient
from payday.models.user import User


@pytest.mark.asyncio
async def test_kyc_submit_and_status(client: AsyncClient, test_user: User, user_auth_headers: dict):
    # Check initial KYC status
    status_res = await client.get("/api/v1/kyc/status", headers=user_auth_headers)
    assert status_res.status_code == 200

    # Submit KYC document
    submit_res = await client.post(
        "/api/v1/kyc/submit",
        json={"id_document_no": "CM-2026-987654321", "id_document_type": "NATIONAL_ID"},
        headers=user_auth_headers,
    )
    assert submit_res.status_code == 200
    assert submit_res.json()["data"]["kyc_status"] == "PENDING"

    # Verify masked output
    status_res2 = await client.get("/api/v1/kyc/status", headers=user_auth_headers)
    assert status_res2.status_code == 200
    data = status_res2.json()["data"]
    assert data["kyc_status"] == "PENDING"
    assert data["id_document_masked"].endswith("4321")
    assert "****" in data["id_document_masked"]


@pytest.mark.asyncio
async def test_admin_kyc_review(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
    admin_auth_headers: dict,
):
    # User submits KYC
    await client.post(
        "/api/v1/kyc/submit",
        json={"id_document_no": "PASSPORT-778899", "id_document_type": "PASSPORT"},
        headers=user_auth_headers,
    )

    # Customer cannot review KYC (Forbidden)
    forbidden_res = await client.post(
        f"/api/v1/kyc/review/{test_user.user_id}",
        json={"status": "VERIFIED"},
        headers=user_auth_headers,
    )
    assert forbidden_res.status_code == 403

    # Admin approves KYC
    admin_res = await client.post(
        f"/api/v1/kyc/review/{test_user.user_id}",
        json={"status": "VERIFIED"},
        headers=admin_auth_headers,
    )
    assert admin_res.status_code == 200
    assert admin_res.json()["data"]["kyc_status"] == "VERIFIED"
