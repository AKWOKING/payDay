from payday.models.linked_account import ChannelProvider
from payday.models.transaction import TransactionChannel
from payday.adapters.base import PaymentChannelAdapter
from payday.adapters.mtn_momo import mtn_momo_adapter
from payday.core.exceptions import PayDayException


class ChannelAdapterFactory:
    """Factory to retrieve the appropriate channel adapter for a given provider."""

    @staticmethod
    def get_adapter(channel: str) -> PaymentChannelAdapter:
        ch_upper = str(channel).upper()
        if "MTN" in ch_upper:
            return mtn_momo_adapter
        elif "ORANGE" in ch_upper:
            # In Sprint 2, Orange returns the mock adapter or stub until Sprint 3
            return mtn_momo_adapter
        elif "UBA" in ch_upper:
            raise PayDayException(
                status_code=400,
                detail="UBA Bank channel integration is scheduled for Phase 2.",
                code="CHANNEL_NOT_AVAILABLE",
            )
        else:
            raise PayDayException(
                status_code=400,
                detail=f"Unsupported payment channel: {channel}",
                code="INVALID_CHANNEL",
            )


adapter_factory = ChannelAdapterFactory()
