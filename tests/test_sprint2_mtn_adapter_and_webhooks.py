from decimal import Decimal
import pytest
from payday.adapters.mtn_momo import mtn_momo_adapter
from payday.adapters.base import ChannelDepositRequest, ChannelWithdrawalRequest
from payday.adapters.factory import adapter_factory


@pytest.mark.asyncio
async def test_mtn_momo_adapter_initiate_deposit():
    req = ChannelDepositRequest(
        transaction_id="tx-unit-001",
        phone_number="+237677112233",
        amount=Decimal("15000.00"),
        description="Funding test",
    )
    res = await mtn_momo_adapter.initiate_deposit(req)
    assert res.success is True
    assert res.channel_ref.startswith("MTN-MOMO-")
    assert res.status == "PROCESSING"


@pytest.mark.asyncio
async def test_mtn_momo_adapter_initiate_withdrawal():
    req = ChannelWithdrawalRequest(
        transaction_id="tx-unit-002",
        destination_phone="+237677998877",
        amount=Decimal("8000.00"),
        description="Payout test",
    )
    res = await mtn_momo_adapter.initiate_withdrawal(req)
    assert res.success is True
    assert res.channel_ref.startswith("MTN-DISB-")
    assert res.status == "PROCESSING"


def test_adapter_factory_resolution():
    mtn_adapter = adapter_factory.get_adapter("MTN")
    assert mtn_adapter is not None

    with pytest.raises(Exception):
        adapter_factory.get_adapter("INVALID_TELCO")
