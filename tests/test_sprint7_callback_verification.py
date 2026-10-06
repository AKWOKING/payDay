"""M1 / A8 — operator callback verification and authoritative settlement.

What this file is defending
---------------------------
Neither operator signs its notifications. MTN signs nothing at all; Orange echoes
a per-order `notif_token`. Before this work the callback body itself moved the
ledger, which means anything that could POST JSON to the webhook could credit a
wallet — and the body shape was PayDay's own invention, so a *real* callback
would have been rejected with 422 and never settled.

These tests pin the two halves of the fix:

* **LB-12** — the operators' real callback shapes parse (golden bodies, taken
  from the vendor documentation), and a body that is not an operator callback is
  refused rather than interpreted.
* **LB-13** — no callback body moves money. Settlement happens only on the
  answer from the operator's status endpoint, so a forged, replayed or
  misdirected callback cannot credit anything.

The transport is faked (`query_status` returns a canned operator answer) because
no sandbox credentials exist; the *parsers and settlement rules* are the real
ones. What that leaves unproven is recorded in the plan doc, not papered over.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from payday.adapters.base import ChannelResponse, ProviderCallback
from payday.adapters import mtn_momo, orange_money
from payday.core.config import settings
from payday.models.transaction import Transaction, TransactionStatus
from payday.models.user import User
from payday.models.wallet import Wallet

# --------------------------------------------------------------------------- #
# Golden callback bodies (from the vendors' own documentation)
# --------------------------------------------------------------------------- #
# MTN MoMo collection callback. `externalId` is the caller's own value — this
# codebase sends the transaction id there — and the status key is
# `transactionStatus`, not `status`.
MTN_CALLBACK = {
    "externalId": "PLACEHOLDER",
    "amount": "10000",
    "currency": "XAF",
    "financialTransactionId": "1633100230",
    "transactionStatus": "SUCCESSFUL",
    "payee": {"partyIdType": "MSISDN", "partyId": "237677112233"},
}

# Orange Money notification: three fields, nothing else. No order id, no amount,
# no reference — which is why the notif_token has to be stored at initiation.
ORANGE_CALLBACK = {
    "status": "SUCCESS",
    "notif_token": "dd497bda3b250e536186fc0663f32f40",
    "txnid": "MP150709.1341.A00073",
}


def test_mtn_callback_body_parses_exactly() -> None:
    callback = mtn_momo.parse_callback({**MTN_CALLBACK, "externalId": "tx-123"})
    assert callback is not None
    assert callback.transaction_id == "tx-123"
    assert callback.provider_status == "SUCCESSFUL"
    assert callback.provider_txn_id == "1633100230"
    assert callback.notif_token is None
    assert callback.raw["currency"] == "XAF"


def test_mtn_callback_without_an_external_id_is_not_an_mtn_callback() -> None:
    assert mtn_momo.parse_callback({"status": "SUCCESSFUL"}) is None
    assert mtn_momo.parse_callback({}) is None
    assert mtn_momo.parse_callback({"notif_token": "x", "status": "SUCCESS"}) is None


def test_mtn_failure_reason_is_flattened_for_support() -> None:
    """MTN sends `reason` as an object; a dict repr in the audit log is useless."""
    callback = mtn_momo.parse_callback(
        {
            "externalId": "tx-1",
            "transactionStatus": "FAILED",
            "reason": {"code": "NOT_ENOUGH_FUNDS", "message": "Insufficient balance"},
        }
    )
    assert callback is not None
    assert callback.reason == "NOT_ENOUGH_FUNDS: Insufficient balance"


def test_orange_callback_body_parses_exactly() -> None:
    callback = orange_money.parse_callback(ORANGE_CALLBACK)
    assert callback is not None
    assert callback.notif_token == "dd497bda3b250e536186fc0663f32f40"
    assert callback.provider_status == "SUCCESS"
    assert callback.provider_txn_id == "MP150709.1341.A00073"
    # Orange does not tell us which order this is; the token is the only link.
    assert callback.transaction_id is None


def test_orange_callback_without_a_notif_token_is_refused() -> None:
    assert orange_money.parse_callback({"status": "SUCCESS", "txnid": "MP1"}) is None
    assert orange_money.parse_callback({}) is None
    assert orange_money.parse_callback({"externalId": "tx-1", "transactionStatus": "SUCCESSFUL"}) is None


def test_each_parser_refuses_the_other_operators_body() -> None:
    """A misrouted body must not be interpreted by the wrong adapter."""
    assert mtn_momo.parse_callback(ORANGE_CALLBACK) is None
    assert orange_money.parse_callback({**MTN_CALLBACK, "externalId": "tx-1"}) is None


def test_orange_authenticity_is_a_constant_time_token_comparison() -> None:
    adapter = orange_money.OrangeMoneyAdapter(use_mock=False)
    callback = orange_money.parse_callback(ORANGE_CALLBACK)
    assert callback is not None

    assert adapter.verify_callback_authenticity(callback, "dd497bda3b250e536186fc0663f32f40") is True
    assert adapter.verify_callback_authenticity(callback, "a-different-token") is False
    # No stored token means no order to attribute this to: refuse, never assume.
    assert adapter.verify_callback_authenticity(callback, None) is False
    assert adapter.verify_callback_authenticity(callback, "") is False


def test_mtn_has_nothing_to_verify_locally_and_says_so() -> None:
    """MTN signs nothing, so authenticity must come from the requery, not here.

    Returning True is only acceptable because the caller does not act on the
    callback body — it re-queries. This test documents that dependency.
    """
    adapter = mtn_momo.MTNMoMoAdapter(use_mock=False)
    callback = mtn_momo.parse_callback({**MTN_CALLBACK, "externalId": "tx-1"})
    assert callback is not None
    assert adapter.verify_callback_authenticity(callback, None) is True
    assert mtn_momo.MTNMoMoAdapter(use_mock=False).verify_callback_authenticity(
        ProviderCallback(), None
    ) is False


# --------------------------------------------------------------------------- #
# Status requery contract (the authority)
# --------------------------------------------------------------------------- #
class FakeAdapter:
    """A real parser plus a canned operator answer. Only the transport is fake."""

    use_mock = False

    def __init__(
        self,
        provider: str,
        status: str = "SUCCESS",
        amount: Optional[str] = None,
        raises: bool = False,
    ) -> None:
        self.provider = provider
        self.status = status
        self.amount = amount
        self.raises = raises
        self.queries: list = []

    def parse_callback(self, body: Dict[str, Any]):
        return mtn_momo.parse_callback(body) if self.provider == "MTN" else orange_money.parse_callback(body)

    def verify_callback_authenticity(self, callback, expected_notif_token) -> bool:
        if self.provider == "MTN":
            return callback.transaction_id is not None
        return orange_money.OrangeMoneyAdapter(use_mock=False).verify_callback_authenticity(
            callback, expected_notif_token
        )

    async def query_status(self, channel_ref, tx_type="DEPOSIT", order_id=None, amount=None):
        self.queries.append(channel_ref)
        if self.raises:
            raise RuntimeError("operator unreachable")
        raw = {"status": self.status, "order_id": order_id}
        if self.amount is not None:
            raw["amount"] = self.amount
        return ChannelResponse(
            success=self.status in {"SUCCESS", "FAILED"},
            channel_ref=channel_ref,
            status=self.status,
            message=f"{self.provider} reports {self.status}",
            raw_response=raw,
            provider_txn_id="PROVIDER-TXN-1",
        )


class FakeFactory:
    def __init__(self, adapters: Dict[str, FakeAdapter]) -> None:
        self.adapters = adapters

    def get_adapter(self, channel: str) -> FakeAdapter:
        return self.adapters[channel]


@pytest.fixture
def operator_mode(monkeypatch):
    """Put the webhook route in sandbox mode with a faked operator transport."""
    import payday.api.v1.webhooks as webhooks_module

    def _apply(adapters: Dict[str, FakeAdapter]):
        monkeypatch.setattr(webhooks_module.settings, "TELCO_MODE", "sandbox")
        monkeypatch.setattr(webhooks_module, "adapter_factory", FakeFactory(adapters))
        return adapters

    return _apply


async def _initiate_deposit(client: AsyncClient, headers: dict, *, amount: float = 10000.0,
                            channel: str = "MTN", key: str = "a8-key-1") -> dict:
    res = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": channel, "amount": amount, "phone_number": "677112233", "idempotency_key": key},
        headers=headers,
    )
    assert res.status_code == 202, res.text
    return res.json()["data"]


async def _balance(client: AsyncClient, headers: dict) -> Decimal:
    res = await client.get("/api/v1/wallet/balance", headers=headers)
    return Decimal(str(res.json()["data"]["balance"]))


async def _tx(db, transaction_id: str) -> Transaction:
    result = await db.execute(select(Transaction).where(Transaction.transaction_id == transaction_id))
    return result.scalars().one()


# --------------------------------------------------------------------------- #
# The verified path: requery settles, callbacks never do
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_mtn_callback_settles_from_the_requery(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict, operator_mode
):
    before = await _balance(client, user_auth_headers)
    tx = await _initiate_deposit(client, user_auth_headers)
    operator_mode({"MTN": FakeAdapter("MTN", status="SUCCESS")})

    res = await client.post("/api/v1/webhooks/mtn", json={**MTN_CALLBACK, "externalId": tx["transaction_id"]})

    assert res.status_code == 200, res.text
    assert res.json()["data"]["status"] == "SUCCESS"
    assert res.json()["data"]["settled"] is True
    # Net of the 50 XAF fee on 10 000.
    assert await _balance(client, user_auth_headers) == before + Decimal("9950.00")


@pytest.mark.asyncio
async def test_orange_callback_is_matched_by_the_stored_notif_token(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict, operator_mode
):
    before = await _balance(client, user_auth_headers)
    tx = await _initiate_deposit(
        client, user_auth_headers, amount=20000.0, channel="ORANGE", key="a8-orange-1"
    )
    # Orange's initiation response carries the notif_token; the deposit path
    # stores it so the three-field notification can be attributed to this order.
    stored = await _tx(db_session, tx["transaction_id"])
    stored.provider_notif_token = "dd497bda3b250e536186fc0663f32f40"
    stored.provider_order_id = "OM-COL-ABC123"
    await db_session.commit()

    operator_mode({"ORANGE": FakeAdapter("ORANGE", status="SUCCESS")})
    res = await client.post("/api/v1/webhooks/orange", json=ORANGE_CALLBACK)

    assert res.status_code == 200, res.text
    assert res.json()["data"]["settled"] is True
    assert await _balance(client, user_auth_headers) == before + Decimal("19900.00")


@pytest.mark.asyncio
async def test_orange_callback_with_the_wrong_token_is_rejected_before_any_lookup(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict, operator_mode
):
    before = await _balance(client, user_auth_headers)
    tx = await _initiate_deposit(client, user_auth_headers, channel="ORANGE", key="a8-orange-2")
    stored = await _tx(db_session, tx["transaction_id"])
    stored.provider_notif_token = "the-real-token"
    await db_session.commit()

    operator_mode({"ORANGE": FakeAdapter("ORANGE", status="SUCCESS")})
    res = await client.post("/api/v1/webhooks/orange", json=ORANGE_CALLBACK)

    assert res.status_code == 403
    assert res.json()["code"] == "WEBHOOK_UNAUTHORIZED"
    assert await _balance(client, user_auth_headers) == before
    assert (await _tx(db_session, tx["transaction_id"])).status == TransactionStatus.PROCESSING


@pytest.mark.asyncio
async def test_a_forged_status_cannot_settle_anything(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict, operator_mode
):
    """The operator says PENDING. The callback says SUCCESSFUL. Nothing moves.

    This is LB-13: the callback body used to be the source of truth, so posting
    `"transactionStatus": "SUCCESSFUL"` credited the wallet.
    """
    before = await _balance(client, user_auth_headers)
    tx = await _initiate_deposit(client, user_auth_headers, key="a8-forge-1")
    operator_mode({"MTN": FakeAdapter("MTN", status="PROCESSING")})

    res = await client.post(
        "/api/v1/webhooks/mtn",
        json={**MTN_CALLBACK, "externalId": tx["transaction_id"], "transactionStatus": "SUCCESSFUL"},
    )

    # 503, not 200: we did not settle and a retry is wanted.
    assert res.status_code == 503
    assert res.json()["code"] == "PROVIDER_STATUS_PENDING"
    assert await _balance(client, user_auth_headers) == before
    assert (await _tx(db_session, tx["transaction_id"])).status == TransactionStatus.PROCESSING


@pytest.mark.asyncio
async def test_an_amount_mismatch_is_recorded_but_never_settled(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict, operator_mode
):
    """A 200 XAF payment must not settle a 20 000 XAF deposit."""
    before = await _balance(client, user_auth_headers)
    tx = await _initiate_deposit(client, user_auth_headers, amount=20000.0, key="a8-mismatch-1")
    operator_mode({"MTN": FakeAdapter("MTN", status="SUCCESS", amount="200")})

    res = await client.post("/api/v1/webhooks/mtn", json={**MTN_CALLBACK, "externalId": tx["transaction_id"]})

    assert res.status_code == 202
    body = res.json()["data"]
    assert body["settled"] is False
    assert "AMOUNT_MISMATCH" in body["reason"]
    assert await _balance(client, user_auth_headers) == before
    stored = await _tx(db_session, tx["transaction_id"])
    assert stored.status == TransactionStatus.PROCESSING
    assert "AMOUNT_MISMATCH" in stored.failure_reason


@pytest.mark.asyncio
async def test_a_duplicate_callback_does_not_credit_twice(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict, operator_mode
):
    """MTN retries any non-2xx response, so duplicates are normal traffic."""
    tx = await _initiate_deposit(client, user_auth_headers, key="a8-dupe-1")
    adapter = FakeAdapter("MTN", status="SUCCESS")
    operator_mode({"MTN": adapter})
    payload = {**MTN_CALLBACK, "externalId": tx["transaction_id"]}

    first = await client.post("/api/v1/webhooks/mtn", json=payload)
    after_first = await _balance(client, user_auth_headers)
    second = await client.post("/api/v1/webhooks/mtn", json=payload)

    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["data"]["settled"] is False
    assert await _balance(client, user_auth_headers) == after_first
    # The second call must not even ask the operator again.
    assert len(adapter.queries) == 1


@pytest.mark.asyncio
async def test_a_callback_for_an_unknown_transaction_is_refused(
    client: AsyncClient, test_user: User, user_auth_headers: dict, operator_mode
):
    operator_mode({"MTN": FakeAdapter("MTN", status="SUCCESS")})
    res = await client.post("/api/v1/webhooks/mtn", json={**MTN_CALLBACK, "externalId": "does-not-exist"})
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_a_callback_cannot_settle_a_transaction_on_another_channel(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict, operator_mode
):
    """An Orange notification whose token belongs to an MTN transaction."""
    tx = await _initiate_deposit(client, user_auth_headers, key="a8-cross-1", channel="MTN")
    stored = await _tx(db_session, tx["transaction_id"])
    stored.provider_notif_token = "cross-channel-token"
    await db_session.commit()

    operator_mode({"ORANGE": FakeAdapter("ORANGE", status="SUCCESS")})
    res = await client.post(
        "/api/v1/webhooks/orange",
        json={**ORANGE_CALLBACK, "notif_token": "cross-channel-token"},
    )
    assert res.status_code == 403
    assert (await _tx(db_session, tx["transaction_id"])).status == TransactionStatus.PROCESSING


@pytest.mark.asyncio
async def test_the_invented_body_shape_is_refused_once_real_money_is_possible(
    client: AsyncClient, test_user: User, user_auth_headers: dict, operator_mode
):
    """LB-12/LB-13 guard: the internal shape is a mock-mode convenience only.

    It carries a status and an identifier with no authenticity evidence, so
    accepting it outside mock mode would be a credit-anything endpoint.
    """
    tx = await _initiate_deposit(client, user_auth_headers, key="a8-legacy-1")
    operator_mode({"MTN": FakeAdapter("MTN", status="SUCCESS")})

    res = await client.post(
        "/api/v1/webhooks/mtn",
        json={
            "transaction_id": tx["transaction_id"],
            "external_ref": tx["external_ref"],
            "status": "SUCCESSFUL",
            "amount": 10000.0,
        },
    )
    assert res.status_code == 400
    assert res.json()["code"] == "WEBHOOK_UNRECOGNISED"


@pytest.mark.asyncio
async def test_mock_mode_still_accepts_the_simulator_shape(
    client: AsyncClient, test_user: User, user_auth_headers: dict
):
    """The dev tool and the whole existing suite depend on this path."""
    tx = await _initiate_deposit(client, user_auth_headers, key="a8-mock-1")
    res = await client.post(
        "/api/v1/webhooks/mtn",
        json={
            "transaction_id": tx["transaction_id"],
            "external_ref": tx["external_ref"],
            "status": "SUCCESSFUL",
        },
    )
    assert res.status_code == 200
    assert res.json()["data"]["status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_mock_mode_still_rejects_a_forged_signature(
    client: AsyncClient, test_user: User, user_auth_headers: dict
):
    tx = await _initiate_deposit(client, user_auth_headers, key="a8-mock-2")
    res = await client.post(
        "/api/v1/webhooks/mtn",
        json={
            "transaction_id": tx["transaction_id"],
            "external_ref": tx["external_ref"],
            "status": "SUCCESSFUL",
        },
        headers={"X-Signature": "invalid-signature"},
    )
    assert res.status_code == 403


# --------------------------------------------------------------------------- #
# The sweep: the notification that never arrives
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_sweep_settles_a_transaction_whose_callback_never_arrived(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    from payday.services.status_sweep import StatusSweepService

    before = await _balance(client, user_auth_headers)
    tx = await _initiate_deposit(client, user_auth_headers, key="a8-sweep-1")

    service = StatusSweepService(adapter_factory_override=FakeFactory({"MTN": FakeAdapter("MTN", "SUCCESS")}))
    # min_age 0: the transaction is only just PROCESSING, but the sweep's age
    # filter is not what is under test here (see the next test).
    report = await service.sweep_once(db_session, min_age_seconds=0)

    assert report.examined == 1
    assert report.settled == 1
    assert await _balance(client, user_auth_headers) == before + Decimal("9950.00")
    assert (await _tx(db_session, tx["transaction_id"])).status == TransactionStatus.SUCCESS


@pytest.mark.asyncio
async def test_sweep_leaves_an_unconfirmed_transaction_alone(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    from payday.services.status_sweep import StatusSweepService

    before = await _balance(client, user_auth_headers)
    tx = await _initiate_deposit(client, user_auth_headers, key="a8-sweep-2")

    service = StatusSweepService(adapter_factory_override=FakeFactory({"MTN": FakeAdapter("MTN", "PROCESSING")}))
    report = await service.sweep_once(db_session, min_age_seconds=0)

    assert report.inconclusive == 1
    assert report.settled == 0
    assert await _balance(client, user_auth_headers) == before
    assert (await _tx(db_session, tx["transaction_id"])).status == TransactionStatus.PROCESSING


@pytest.mark.asyncio
async def test_sweep_honours_the_minimum_age(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    """A just-initiated transaction is not chased: the operator still has it open."""
    from payday.services.status_sweep import StatusSweepService

    await _initiate_deposit(client, user_auth_headers, key="a8-sweep-3")
    service = StatusSweepService(adapter_factory_override=FakeFactory({"MTN": FakeAdapter("MTN", "SUCCESS")}))
    report = await service.sweep_once(db_session, min_age_seconds=600)

    assert report.examined == 0
    assert report.settled == 0


@pytest.mark.asyncio
async def test_one_failing_transaction_does_not_abort_the_sweep(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    from payday.services.status_sweep import StatusSweepService

    await _initiate_deposit(client, user_auth_headers, key="a8-sweep-4")
    await _initiate_deposit(client, user_auth_headers, key="a8-sweep-5")

    class SelectiveFactory(FakeFactory):
        def __init__(self):
            super().__init__({})
            self.calls = 0

        def get_adapter(self, channel: str):
            self.calls += 1
            return FakeAdapter("MTN", "SUCCESS", raises=(self.calls == 1))

    report = await StatusSweepService(adapter_factory_override=SelectiveFactory()).sweep_once(
        db_session, min_age_seconds=0
    )

    assert report.examined == 2
    assert report.errors == 1
    assert report.settled == 1


@pytest.mark.asyncio
async def test_sweep_skips_mock_channels(client: AsyncClient, db_session, test_user: User, user_auth_headers: dict):
    """The simulator settles synchronously; there is nothing to ask it about."""
    from payday.services.status_sweep import StatusSweepService

    await _initiate_deposit(client, user_auth_headers, key="a8-sweep-6")

    class MockAdapter(FakeAdapter):
        use_mock = True

    service = StatusSweepService(adapter_factory_override=FakeFactory({"MTN": MockAdapter("MTN", "SUCCESS")}))
    report = await service.sweep_once(db_session, min_age_seconds=0)

    assert report.examined == 1
    assert report.skipped_mock == 1
    assert report.settled == 0


# --------------------------------------------------------------------------- #
# Live mode is gated on the safety net, not on an unimplemented feature
# --------------------------------------------------------------------------- #
def test_live_mode_requires_a_running_status_sweep():
    """Live mode is startable only with the sweep that rescues lost callbacks.

    Both operators document notifications that never arrive. Enabling real money
    without the sweep means a customer can pay and never be credited, with our
    record still saying PROCESSING — so the flag is a precondition, not a nicety.
    """
    from payday.core.config import TelcoConfigurationError, validate_telco_configuration

    configured = settings.model_copy(
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
            "TELCO_STATUS_SWEEP_ENABLED": False,
        }
    )
    with pytest.raises(TelcoConfigurationError) as exc:
        validate_telco_configuration(configured)
    assert "TELCO_STATUS_SWEEP_ENABLED" in str(exc.value)

    # With the sweep on, live mode is no longer refused.
    validate_telco_configuration(configured.model_copy(update={"TELCO_STATUS_SWEEP_ENABLED": True}))
