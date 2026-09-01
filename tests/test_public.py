import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_and_info(client: AsyncClient):
    # Health check
    res = await client.get("/api/v1/public/health")
    assert res.status_code == 200
    assert res.json()["data"]["status"] == "UP"

    # Info check for Landing Page demo
    info_res = await client.get("/api/v1/public/info")
    assert info_res.status_code == 200
    assert info_res.json()["data"]["status"] == "OPERATIONAL"
    assert "MTN_MOMO" in info_res.json()["data"]["active_channels"]


@pytest.mark.asyncio
async def test_fee_calculator(client: AsyncClient):
    # Deposit calculation: 50,000 XAF with 0.5% fee = 250 XAF
    deposit_res = await client.post(
        "/api/v1/public/fee-calculator",
        json={"type": "DEPOSIT", "channel": "MTN", "amount": 50000.00},
    )
    assert deposit_res.status_code == 200
    data = deposit_res.json()["data"]
    assert float(data["fee"]) == 250.00
    assert float(data["net_credited"]) == 49750.00

    # Withdraw calculation: 50,000 XAF with 1.0% fee = 500 XAF
    withdraw_res = await client.post(
        "/api/v1/public/fee-calculator",
        json={"type": "WITHDRAW", "channel": "ORANGE", "amount": 50000.00},
    )
    assert withdraw_res.status_code == 200
    w_data = withdraw_res.json()["data"]
    assert float(w_data["fee"]) == 500.00
    assert float(w_data["total_charged"]) == 50500.00
