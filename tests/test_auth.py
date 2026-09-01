import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_user_success(client: AsyncClient):
    payload = {
        "full_name": "Samuel Eto'o",
        "phone_number": "+237699445566",
        "email": "samuel@example.cm",
        "password": "StrongPassword2026!",
        "id_document_no": "1092837465",
        "id_document_type": "NATIONAL_ID",
    }
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["success"] is True
    assert data["data"]["full_name"] == "Samuel Eto'o"
    assert data["data"]["phone_number"] == "+237699445566"
    assert data["data"]["wallet_id"] is not None
    assert data["data"]["currency"] == "XAF"
    assert data["data"]["balance"] == "0.00"


@pytest.mark.asyncio
async def test_register_duplicate_phone(client: AsyncClient):
    payload = {
        "full_name": "User One",
        "phone_number": "677990011",  # Unprefixed format should be normalized to +237677990011
        "password": "Password123!",
        "id_document_no": "99887766",
    }
    res1 = await client.post("/api/v1/auth/register", json=payload)
    assert res1.status_code == 201

    # Attempt to register again with same phone
    res2 = await client.post("/api/v1/auth/register", json=payload)
    assert res2.status_code == 409
    assert res2.json()["code"] == "USER_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_login_and_profile_flow(client: AsyncClient):
    # 1. Register
    reg_payload = {
        "full_name": "Rigobert Song",
        "phone_number": "+237677223344",
        "email": "song@example.cm",
        "password": "CaptainPassword2026!",
        "id_document_no": "5544332211",
    }
    reg_res = await client.post("/api/v1/auth/register", json=reg_payload)
    assert reg_res.status_code == 201

    # 2. Login
    login_payload = {
        "phone_number": "677223344",
        "password": "CaptainPassword2026!",
    }
    login_res = await client.post("/api/v1/auth/login", json=login_payload)
    assert login_res.status_code == 200
    token_data = login_res.json()["data"]
    access_token = token_data["access_token"]
    refresh_token = token_data["refresh_token"]
    assert token_data["role"] == "CUSTOMER"
    assert token_data["has_pin"] is False

    # 3. Access Protected Profile /auth/me
    headers = {"Authorization": f"Bearer {access_token}"}
    me_res = await client.get("/api/v1/auth/me", headers=headers)
    assert me_res.status_code == 200
    me_data = me_res.json()["data"]
    assert me_data["full_name"] == "Rigobert Song"

    # 4. Set Transaction PIN
    pin_payload = {
        "pin": "4321",
        "password": "CaptainPassword2026!",
    }
    pin_res = await client.post("/api/v1/auth/set-pin", json=pin_payload, headers=headers)
    assert pin_res.status_code == 200
    assert pin_res.json()["data"]["has_pin"] is True

    # 5. Refresh Access Token
    refresh_res = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert refresh_res.status_code == 200
    new_access_token = refresh_res.json()["data"]["access_token"]
    assert new_access_token is not None


@pytest.mark.asyncio
async def test_login_invalid_credentials(client: AsyncClient):
    payload = {
        "phone_number": "+237699999999",
        "password": "WrongPassword!",
    }
    res = await client.post("/api/v1/auth/login", json=payload)
    assert res.status_code == 401
    assert res.json()["code"] == "AUTHENTICATION_FAILED"
