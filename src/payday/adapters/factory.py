from payday.adapters.base import PaymentChannelAdapter
from payday.adapters import mtn_momo, orange_money
from payday.core.exceptions import PayDayException


def reset_adapter_cache() -> None:
    """Rebuild the adapters from current settings.

    The adapters are process-wide singletons (so that each operator's OAuth
    token cache is shared rather than duplicated per call site). Tests and
    anything that reloads configuration call this to pick up a change to
    `TELCO_MODE` or to operator credentials instead of reusing a stale instance.
    """
    mtn_momo.mtn_momo_adapter = mtn_momo.build_mtn_adapter()
    orange_money.orange_money_adapter = orange_money.build_orange_adapter()


class ChannelAdapterFactory:
    """Resolves the payment-channel adapter for a provider.

    The adapters were previously hard-coded `use_mock=True` singletons; they are
    now built from configuration, so the same code path serves mock, sandbox and
    live deployments (M1 / LB-8).
    """

    @staticmethod
    def get_adapter(channel: str) -> PaymentChannelAdapter:
        key = str(channel).upper()

        if "MTN" in key:
            return mtn_momo.mtn_momo_adapter

        if "ORANGE" in key:
            return orange_money.orange_money_adapter

        if "UBA" in key:
            raise PayDayException(
                status_code=400,
                detail="UBA Bank channel integration is scheduled for Phase 2.",
                code="CHANNEL_NOT_AVAILABLE",
            )

        raise PayDayException(
            status_code=400,
            detail=f"Unsupported payment channel: {channel}",
            code="INVALID_CHANNEL",
        )


adapter_factory = ChannelAdapterFactory()
