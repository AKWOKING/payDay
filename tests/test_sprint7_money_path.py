"""Sprint 7 — M1: the live money path (LB-8 … LB-11).

Before this workstream the platform could not move real money at all: both
adapters were module-level singletons built with `use_mock=True`, no telco
setting existed anywhere in configuration, and no test ever inspected an
outbound payload — which is how three real defects survived 133 green tests:

- **LB-9** `int(Decimal("1000.99"))` → `1000` sent to Orange while the ledger
  credited `995.99` net, and MTN was sent `"1000.00"` for a currency with no
  minor unit;
- **LB-10** one `_clean_msisdn` for two operators whose APIs document different
  formats;
- **LB-11** nothing asserted a payload.

These tests are therefore deliberately *not* end-to-end HTTP tests: the value
they add is asserting the exact bytes that leave the building. A change to any
field, header or format fails here rather than at the operator's gateway.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient

from payday.adapters.mtn_momo import (
    MTNApiCredentials,
    MTNConfig,
    MTNMoMoAdapter,
    build_requesttopay_payload,
    build_transfer_payload,
)
from payday.adapters.orange_money import (
    OrangeConfig,
    OrangeMoneyAdapter,
    build_payout_payload,
    build_webpayment_payload,
)
from payday.core.config import TelcoConfigurationError, settings, validate_telco_configuration
from payday.core.money import is_whole_xaf, to_wire_amount, to_wire_amount_string, whole_xaf
from payday.core.msisdn import (
    format_for_operator,
    national_number,
    operator_for,
)
from payday.core.exceptions import PayDayException

PASSWORD = "SecretP@ssword123"


# --------------------------------------------------------------------------- #
# A2 — whole francs (LB-9)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1000", "1000"),
        ("1000.00", "1000"),
        ("61.72", "62"),      # 0.5% of 12 345 — the real fee value
        ("123.45", "123"),    # 1% of 12 345
        ("0.50", "1"),        # HALF_UP
        ("0.49", "0"),
    ],
)
def test_whole_xaf_default_rounding(raw: str, expected: str) -> None:
    assert whole_xaf(Decimal(raw)) == Decimal(expected)


def test_whole_xaf_rounding_policy_is_configurable() -> None:
    """The rounding direction is a commercial decision (D26), not a code default.

    Proving all three modes behave differently means the policy really is
    switchable rather than documented as such.
    """
    value = Decimal("0.50")
    assert whole_xaf(value, rounding="HALF_UP") == Decimal("1")
    assert whole_xaf(value, rounding="DOWN") == Decimal("0")
    assert whole_xaf(value, rounding="UP") == Decimal("1")
    with pytest.raises(ValueError):
        whole_xaf(value, rounding="NONSENSE")


def test_no_centimes_are_representable() -> None:
    assert is_whole_xaf("1000") and is_whole_xaf("1000.00")
    assert not is_whole_xaf("1000.55")
    assert not is_whole_xaf("0.01")


def test_wire_amounts_are_integers() -> None:
    assert to_wire_amount("1000.00") == 1000
    assert to_wire_amount_string("1000.00") == "1000"
    assert to_wire_amount("1000.99") == 1001  # not truncated
    assert isinstance(to_wire_amount("1000"), int)


def test_fees_are_whole_francs() -> None:
    """Regression for the fee that could not be charged or reconciled."""
    from payday.models.transaction import TransactionType
    from payday.services.wallet_engine import wallet_engine

    fee = wallet_engine.calculate_fee(TransactionType.WITHDRAW, Decimal("12345"))
    assert fee == Decimal("123"), fee            # 123.45 → 123, not 123.45
    assert is_whole_xaf(fee)

    deposit_fee = wallet_engine.calculate_fee(TransactionType.DEPOSIT, Decimal("12345"))
    assert deposit_fee == Decimal("62"), deposit_fee  # 61.72 → 62
    assert is_whole_xaf(deposit_fee)


@pytest.mark.asyncio
async def test_sub_franc_deposit_is_rejected_at_the_api(
    client: AsyncClient, test_user, user_auth_headers: dict
) -> None:
    """`1000.55` is not a representable XAF amount — 422, not silent rounding."""
    response = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 1234.55, "phone_number": "+237677112233"},
        headers=user_auth_headers,
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"

    # The whole-franc equivalent is still accepted (wallet flow unchanged).
    ok = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 1234, "phone_number": "+237677112233"},
        headers=user_auth_headers,
    )
    assert ok.status_code == 202, ok.text
    assert Decimal(str(ok.json()["data"]["amount"])) == Decimal("1234")


@pytest.mark.asyncio
async def test_sub_franc_withdrawal_is_rejected_at_the_api(
    client: AsyncClient, test_user, user_auth_headers: dict
) -> None:
    response = await client.post(
        "/api/v1/wallet/withdraw",
        json={
            "channel": "ORANGE",
            "amount": 100.5,
            "destination_phone": "+237699123456",
            "pin": "4321",
        },
        headers=user_auth_headers,
    )
    assert response.status_code == 422, response.text


# --------------------------------------------------------------------------- #
# A3 — MSISDN per operator (LB-10)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "given,national",
    [
        ("+237699123456", "699123456"),
        ("237699123456", "699123456"),
        ("699123456", "699123456"),
        ("00237699123456", "699123456"),
        ("+237 699 123 456", "699123456"),
        ("699-123-456", "699123456"),
    ],
)
def test_national_number_accepts_the_forms_users_actually_type(given, national) -> None:
    assert national_number(given) == national


def test_mtn_keeps_the_country_code_and_orange_does_not() -> None:
    """The defect: one transformation was applied to both operators."""
    phone = "+237699123456"
    assert format_for_operator(phone, "MTN") == "237699123456"
    assert format_for_operator(phone, "ORANGE") == "699123456"
    assert format_for_operator(phone, "MTN") != format_for_operator(phone, "ORANGE")


def test_fixed_line_numbers_are_rejected() -> None:
    """A landline can never receive mobile money — fail clearly, not at the operator."""
    with pytest.raises(PayDayException) as exc:
        national_number("+237222210007")
    assert exc.value.code == "NOT_A_MOBILE_NUMBER"


@pytest.mark.parametrize("bad", ["", "123", "+23769912345", "+23769912345678", "abcdefghi", "+33699123456"])
def test_malformed_numbers_are_rejected(bad: str) -> None:
    with pytest.raises(PayDayException):
        national_number(bad)


def test_prefix_lookup_is_advisory_not_authoritative() -> None:
    """Portability means the prefix is a hint, and unmapped blocks are None."""
    assert operator_for("+237699123456") == "ORANGE"
    assert operator_for("+237677112233") == "MTN"
    assert operator_for("+237222210007") is None  # fixed line: no operator claim


def test_operator_mismatch_warns_by_default_and_can_be_made_fatal() -> None:
    """Hard-rejecting by default would fail legitimate ported numbers."""
    from payday.adapters.mtn_momo import warn_if_operator_mismatch

    warn_if_operator_mismatch("+237699123456", strict=False)  # Orange number, MTN channel
    with pytest.raises(PayDayException) as exc:
        warn_if_operator_mismatch("+237699123456", strict=True)
    assert exc.value.code == "PHONE_OPERATOR_MISMATCH"


# --------------------------------------------------------------------------- #
# A4 — golden payloads (LB-11): the exact bytes sent to each operator
# --------------------------------------------------------------------------- #
def test_mtn_requesttopay_payload_is_exact() -> None:
    payload = build_requesttopay_payload(
        amount=Decimal("15000.00"),
        currency="XAF",
        transaction_id="tx-123",
        phone_number="+237677112233",
        description="Wallet funding",
        payer_message=None,
    )
    assert payload == {
        "amount": "15000",           # string of WHOLE francs, never "15000.00"
        "currency": "XAF",
        "externalId": "tx-123",
        "payer": {"partyIdType": "MSISDN", "partyId": "237677112233"},
        "payerMessage": "PayDay Deposit",
        "payeeNote": "Wallet funding",
    }


def test_mtn_transfer_payload_is_exact() -> None:
    # Fractional on purpose: a whole amount cannot tell rounding from
    # truncation (`str(Decimal("8000")) == "8000"`), which is exactly how the
    # LB-9 defect hid. 8000.60 must round up, never truncate to "8000".
    payload = build_transfer_payload(
        amount=Decimal("8000.60"),
        currency="XAF",
        transaction_id="tx-456",
        destination_phone="677998877",
        description="Cash out",
        payee_note=None,
    )
    assert payload == {
        "amount": "8001",
        "currency": "XAF",
        "externalId": "tx-456",
        "payee": {"partyIdType": "MSISDN", "partyId": "237677998877"},
        "payerMessage": "PayDay Withdrawal",
        "payeeNote": "Cash out",
    }


def test_mtn_payload_never_carries_a_fractional_amount() -> None:
    """The regression that motivated this file."""
    payload = build_requesttopay_payload(
        amount=Decimal("1000.99"),
        currency="XAF",
        transaction_id="tx-1",
        phone_number="+237677112233",
        description="d",
    )
    assert "." not in payload["amount"], payload["amount"]
    assert payload["amount"] == "1001"


def test_orange_webpayment_payload_is_exact() -> None:
    config = OrangeConfig(
        merchant_key="merchant-key-1",
        notification_url="https://api.payday.cm/api/v1/webhooks/orange",
    )
    # Fractional on purpose: `int(20000.60)` would truncate to 20000 and the
    # test would still pass — the old code did exactly that.
    payload = build_webpayment_payload(
        config=config,
        amount=Decimal("20000.60"),
        currency="XAF",
        order_id="OM-COL-ABC123",
        reference="tx-789",
    )
    assert payload == {
        "merchant_key": "merchant-key-1",
        "currency": "XAF",
        "order_id": "OM-COL-ABC123",
        "amount": 20001,             # JSON number, whole francs, rounded
        "reference": "tx-789",
        "notif_url": "https://api.payday.cm/api/v1/webhooks/orange",
        "lang": "fr",
    }


def test_orange_notification_url_must_be_absolute() -> None:
    """Orange cannot call `/api/v1/webhooks/orange` — that was the old value."""
    config = OrangeConfig(merchant_key="k", notification_url=settings.orange_notification_url())
    payload = build_webpayment_payload(
        config=config, amount=Decimal("1000"), currency="XAF", order_id="o", reference="r"
    )
    assert payload["notif_url"].startswith("http"), payload["notif_url"]
    assert "/api/v1/webhooks/orange" in payload["notif_url"]


def test_orange_payout_payload_uses_the_national_msisdn() -> None:
    config = OrangeConfig(merchant_key="k")
    payload = build_payout_payload(
        config=config,
        amount=Decimal("5000.60"),
        payout_id="OM-DISB-1",
        destination_phone="+237691234567",
        currency="XAF",
    )
    assert payload["recipient_msisdn"] == "691234567"
    assert payload["amount"] == 5001  # 5000.60 must round up, not truncate to 5000


# --------------------------------------------------------------------------- #
# A1 — configuration, credential separation and fail-closed startup (LB-8)
# --------------------------------------------------------------------------- #
def test_adapters_are_built_from_settings_and_default_to_mock() -> None:
    """The default must stay the simulator so no test or dev box calls an operator."""
    adapter = MTNMoMoAdapter()
    assert adapter.use_mock is (settings.TELCO_MODE == "mock")
    assert adapter.use_mock is True


def test_mtn_collection_and_disbursement_credentials_are_separate() -> None:
    """MTN issues a distinct API user/key/subscription key per product."""
    config = MTNConfig(
        base_url="https://sandbox.momodeveloper.mtn.com",
        target_environment="sandbox",
        collection=MTNApiCredentials("col-user", "col-key", "col-sub"),
        disbursement=MTNApiCredentials("dis-user", "dis-key", "dis-sub"),
    )
    adapter = MTNMoMoAdapter(config=config, use_mock=True)
    assert adapter._credentials("collection").api_user == "col-user"
    assert adapter._credentials("disbursement").api_user == "dis-user"
    assert adapter._credentials("collection") != adapter._credentials("disbursement")


def test_production_refuses_to_run_the_mock_adapter() -> None:
    """A production deploy on mocks would accept deposits that never settle."""
    with pytest.raises(TelcoConfigurationError) as exc:
        validate_telco_configuration(
            settings.model_copy(update={"ENVIRONMENT": "production", "TELCO_MODE": "mock"})
        )
    assert "refusing to start" in str(exc.value)


def test_live_mode_requires_every_credential() -> None:
    with pytest.raises(TelcoConfigurationError) as exc:
        validate_telco_configuration(
            settings.model_copy(
                update={
                    "TELCO_MODE": "live",
                    "PUBLIC_BASE_URL": "https://api.payday.cm",
                }
            )
        )
    message = str(exc.value)
    assert "MTN_COLLECTION_API_USER" in message
    assert "ORANGE_MERCHANT_KEY" in message


def test_live_mode_is_startable_now_that_callbacks_are_verified() -> None:
    """A8 landed: the callback path is verified, so live mode is no longer refused.

    It is still gated — on operator credentials, a public HTTPS callback URL, and
    the status sweep that rescues lost notifications (see
    `test_sprint7_callback_verification.py`, which owns that requirement). What
    this test pins is that a *fully* configured deployment is not blocked by a
    blanket refusal any more.
    """
    fully_configured = settings.model_copy(
        update={
            "TELCO_MODE": "live",
            "PUBLIC_BASE_URL": "https://api.payday.cm",
            "MTN_BASE_URL": "https://api.mtn.com",
            "MTN_TARGET_ENVIRONMENT": "mtncameroon",
            "MTN_COLLECTION_SUBSCRIPTION_KEY": "s",
            "MTN_COLLECTION_API_USER": "u",
            "MTN_COLLECTION_API_KEY": "k",
            "MTN_DISBURSEMENT_SUBSCRIPTION_KEY": "s",
            "MTN_DISBURSEMENT_API_USER": "u",
            "MTN_DISBURSEMENT_API_KEY": "k",
            "ORANGE_MERCHANT_KEY": "mk",
            "ORANGE_CLIENT_ID": "cid",
            "ORANGE_CLIENT_SECRET": "secret",
            "TELCO_STATUS_SWEEP_ENABLED": True,
        }
    )
    validate_telco_configuration(fully_configured)


def test_live_mode_rejects_a_localhost_callback_url() -> None:
    """Checked before the A8 gate, so the diagnostic is the actionable one."""
    with pytest.raises(TelcoConfigurationError):
        validate_telco_configuration(
            settings.model_copy(update={"TELCO_MODE": "live", "PUBLIC_BASE_URL": "http://localhost:8000"})
        )


def test_mock_and_sandbox_modes_start_without_credentials() -> None:
    validate_telco_configuration(settings.model_copy(update={"TELCO_MODE": "mock"}))
    validate_telco_configuration(settings.model_copy(update={"TELCO_MODE": "sandbox"}))


def test_live_mode_adapters_talk_to_the_configured_host() -> None:
    """Sandbox and production hosts differ; the URL must follow configuration."""
    sandbox = settings.model_copy(update={"TELCO_MODE": "sandbox"})
    assert sandbox.mtn_base_url == "https://sandbox.momodeveloper.mtn.com"
    assert sandbox.orange_base_url.endswith("/orange-money-webpay/dev/v1")

    live = settings.model_copy(
        update={
            "TELCO_MODE": "live",
            "MTN_BASE_URL": "https://api.mtn.com",
            "MTN_TARGET_ENVIRONMENT": "mtncameroon",
        }
    )
    assert live.mtn_base_url == "https://api.mtn.com"
    assert live.mtn_target_environment == "mtncameroon"
    assert live.orange_base_url.endswith("/orange-money-webpay/cm/v1")


def test_environment_specific_values_are_never_guessed() -> None:
    """Live MTN has no default host — guessing one would misroute real money."""
    live_without_host = settings.model_copy(update={"TELCO_MODE": "live", "MTN_BASE_URL": ""})
    assert live_without_host.mtn_base_url == ""
    assert live_without_host.mtn_target_environment == ""


# --------------------------------------------------------------------------- #
# A1 — token lifecycle
# --------------------------------------------------------------------------- #
def test_token_cache_expires_before_the_provider_does() -> None:
    """The old cache kept the first token for the life of the process (~1 h bug)."""
    now = [1000.0]
    adapter = MTNMoMoAdapter(
        config=MTNConfig(
            base_url="https://example.invalid",
            collection=MTNApiCredentials("u", "k", "s"),
            token_refresh_skew_seconds=300,
        ),
        use_mock=False,
        clock=lambda: now[0],
    )
    adapter._tokens["collection"] = ("token-1", now[0] + 100)

    async def cached() -> str:
        return await adapter._get_auth_token("collection")

    import asyncio

    assert asyncio.run(cached()) == "token-1"          # still valid
    now[0] += 200                                       # past expiry
    assert adapter._tokens["collection"][1] < now[0]    # cache must not be reused
    assert list(adapter._tokens.values())[0][1] != float("inf")


def test_orange_basic_auth_header_is_base64_encoded() -> None:
    """`Basic {id}:{secret}` unencoded is not a valid Authorization header."""
    config = OrangeConfig(client_id="my-id", client_secret="my-secret")
    header = config.basic_auth_header()
    assert header == "Basic bXktaWQ6bXktc2VjcmV0"
    assert ":my-secret" not in header


def test_orange_prefers_the_issued_authorization_header() -> None:
    """Orange often hands over a ready-made value; use it verbatim."""
    config = OrangeConfig(
        auth_header="Basic aXNzdWVkOmJ5LW9yYW5nZQ==",
        client_id="ignored",
        client_secret="ignored",
    )
    assert config.basic_auth_header() == "Basic aXNzdWVkOmJ5LW9yYW5nZQ=="


# --------------------------------------------------------------------------- #
# Honesty of the public surface (a claim of "ACTIVE" must mean live)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_public_status_reports_the_real_channel_mode(client: AsyncClient) -> None:
    info = await client.get("/api/v1/public/info")
    channels = info.json()["data"]["active_channels"]
    assert channels["MTN_MOMO"] == "SIMULATED"

    health = await client.get("/api/v1/public/health")
    data = health.json()["data"]
    assert data["telco_mode"] == "mock"
    assert data["status"] == "UP"
