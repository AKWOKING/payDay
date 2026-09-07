"""Sprint 4 — Chaos & Fault Injection.

Sprints 2 and 3 covered *graceful* telco failure: the adapter returns
`ChannelResponse(success=False)` and the compensating release runs. This file
covers the harder case — the adapter **raises**: connection reset, DNS
failure, read timeout, or a partition part-way through disbursement.

The invariant under test is that money is never created or destroyed. For a
withdrawal the sequence is hold → call telco → record transaction, with the
hold uncommitted until the end, so an exception in the middle must leave the
wallet exactly as it was.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from payday.adapters import mtn_momo, orange_money
from payday.adapters.base import ChannelResponse
from payday.models.user import User

PIN = "4417"
PASSWORD = "SecretP@ssword123"


@pytest_asyncio.fixture
async def chaos_client(client: AsyncClient) -> AsyncClient:
    """The shared client, reconfigured to surface server errors as responses.

    httpx's ASGITransport defaults to `raise_app_exceptions=True`, which
    re-raises an unhandled server exception directly into the test. That is
    the wrong model for chaos testing: under uvicorn, Starlette's
    ServerErrorMiddleware converts the same exception into an RFC 7807 500 and
    the caller receives a response, not a traceback.

    Flipping the flag on the existing transport reproduces production
    behaviour while reusing conftest's database wiring — rebuilding the client
    here would import `tests.conftest` a second time under a different module
    name, yielding a second in-memory engine with no tables.
    """
    transport = client._transport  # noqa: SLF001 — no public accessor exists
    assert isinstance(transport, ASGITransport)
    transport.raise_app_exceptions = False
    return client


async def _set_pin(client: AsyncClient, headers: dict) -> None:
    response = await client.post(
        "/api/v1/auth/set-pin",
        json={"pin": PIN, "password": PASSWORD},
        headers=headers,
    )
    assert response.status_code in (200, 201), response.text


async def _balance(client: AsyncClient, headers: dict) -> tuple[Decimal, Decimal]:
    response = await client.get("/api/v1/wallet/balance", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    return Decimal(str(data["balance"])), Decimal(str(data["locked_balance"]))


@pytest.mark.asyncio
async def test_withdrawal_survives_telco_connection_reset(
    chaos_client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raised ConnectTimeout mid-disbursement must not strand held funds.

    The hold is applied to the session before the telco call. If the adapter
    raises, the request must unwind so that neither `balance` nor
    `locked_balance` moves — otherwise the customer's money is frozen with no
    transaction record to reconcile against.
    """
    await _set_pin(chaos_client, user_auth_headers)
    before_balance, before_locked = await _balance(chaos_client, user_auth_headers)

    async def exploding_withdrawal(self, req):  # noqa: ANN001, ARG001
        raise httpx.ConnectTimeout("Simulated MTN MoMo network partition")

    monkeypatch.setattr(
        mtn_momo.MTNMoMoAdapter, "initiate_withdrawal", exploding_withdrawal
    )

    response = await chaos_client.post(
        "/api/v1/wallet/withdraw",
        json={
            "channel": "MTN",
            "amount": 10000.00,
            "destination_phone": "+237677445566",
            "pin": PIN,
        },
        headers=user_auth_headers,
    )

    # The request must fail loudly rather than silently reporting success.
    assert response.status_code >= 400, (
        f"Telco partition returned {response.status_code}; a failure was expected"
    )

    after_balance, after_locked = await _balance(chaos_client, user_auth_headers)
    assert after_balance == before_balance, (
        f"Balance moved during a failed disbursement: {before_balance} -> {after_balance}"
    )
    assert after_locked == before_locked, (
        f"Funds left stranded in locked_balance: {before_locked} -> {after_locked}"
    )


@pytest.mark.asyncio
async def test_deposit_survives_telco_exception_without_crediting(
    chaos_client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A collection request that never reaches MTN must never credit the wallet."""
    before_balance, before_locked = await _balance(chaos_client, user_auth_headers)

    async def exploding_deposit(self, req):  # noqa: ANN001, ARG001
        raise httpx.ReadTimeout("Simulated MTN MoMo read timeout")

    monkeypatch.setattr(mtn_momo.MTNMoMoAdapter, "initiate_deposit", exploding_deposit)

    response = await chaos_client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 20000.00, "phone_number": "+237677001122"},
        headers=user_auth_headers,
    )
    assert response.status_code >= 400

    after_balance, after_locked = await _balance(chaos_client, user_auth_headers)
    assert after_balance == before_balance, "Wallet credited despite telco timeout"
    assert after_locked == before_locked


@pytest.mark.asyncio
async def test_graceful_provider_rejection_releases_hold(
    chaos_client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A well-formed provider rejection must release the hold, not strand it.

    This is the contrast case to the exception paths above: the adapter
    answers correctly with `success=False`, so the compensating release
    should run and the transaction should be recorded as FAILED.
    """
    await _set_pin(chaos_client, user_auth_headers)
    before_balance, before_locked = await _balance(chaos_client, user_auth_headers)

    async def rejecting_withdrawal(self, req):  # noqa: ANN001, ARG001
        return ChannelResponse(
            success=False,
            channel_ref=None,
            status="FAILED",
            message="Orange Money: payee account barred",
            error_code="PAYEE_BARRED",
        )

    monkeypatch.setattr(
        orange_money.OrangeMoneyAdapter, "initiate_withdrawal", rejecting_withdrawal
    )

    response = await chaos_client.post(
        "/api/v1/wallet/withdraw",
        json={
            "channel": "ORANGE",
            "amount": 15000.00,
            "destination_phone": "+237699887766",
            "pin": PIN,
        },
        headers=user_auth_headers,
    )
    assert response.status_code in (200, 202, 400, 402, 422), response.text

    after_balance, after_locked = await _balance(chaos_client, user_auth_headers)
    assert after_balance == before_balance, "Balance debited on a rejected payout"
    assert after_locked == before_locked, "Hold not released after provider rejection"


@pytest.mark.asyncio
async def test_repeated_partitions_do_not_accumulate_locked_funds(
    chaos_client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sustained outage: five consecutive failures must leak nothing.

    A per-attempt leak of held funds is the classic slow bleed — invisible on
    a single request, wallet-draining across a sustained provider outage.
    """
    await _set_pin(chaos_client, user_auth_headers)
    before_balance, before_locked = await _balance(chaos_client, user_auth_headers)

    async def exploding_withdrawal(self, req):  # noqa: ANN001, ARG001
        raise httpx.ConnectError("Simulated sustained MTN outage")

    monkeypatch.setattr(
        mtn_momo.MTNMoMoAdapter, "initiate_withdrawal", exploding_withdrawal
    )

    for _ in range(5):
        await chaos_client.post(
            "/api/v1/wallet/withdraw",
            json={
                "channel": "MTN",
                "amount": 5000.00,
                "destination_phone": "+237677445566",
                "pin": PIN,
            },
            headers=user_auth_headers,
        )

    after_balance, after_locked = await _balance(chaos_client, user_auth_headers)
    assert after_locked == before_locked, (
        f"Locked balance accumulated across 5 outages: {before_locked} -> {after_locked}"
    )
    assert after_balance == before_balance


@pytest.mark.asyncio
async def test_concurrent_withdrawals_during_partition_preserve_ledger(
    chaos_client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ten simultaneous withdrawals into a partition must conserve value.

    Combines concurrency with fault injection: the failure path runs while
    other requests hold the row lock, which is where compensating logic
    typically breaks.
    """
    await _set_pin(chaos_client, user_auth_headers)
    before_balance, before_locked = await _balance(chaos_client, user_auth_headers)

    async def exploding_withdrawal(self, req):  # noqa: ANN001, ARG001
        await asyncio.sleep(0)  # yield, so requests genuinely interleave
        raise httpx.ConnectError("Simulated partition under concurrency")

    monkeypatch.setattr(
        mtn_momo.MTNMoMoAdapter, "initiate_withdrawal", exploding_withdrawal
    )

    await asyncio.gather(
        *(
            chaos_client.post(
                "/api/v1/wallet/withdraw",
                json={
                    "channel": "MTN",
                    "amount": 1000.00,
                    "destination_phone": "+237677445566",
                    "pin": PIN,
                },
                headers=user_auth_headers,
            )
            for _ in range(10)
        ),
        return_exceptions=True,
    )

    after_balance, after_locked = await _balance(chaos_client, user_auth_headers)
    assert after_balance == before_balance, "Value lost or created under concurrent partition"
    assert after_locked == before_locked, "Held funds stranded under concurrent partition"


@pytest.mark.asyncio
async def test_recovery_after_partition_heals(
    chaos_client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Service must resume normally once the provider comes back.

    Guards against a tripped breaker or poisoned adapter state persisting
    after recovery — an outage that outlives the outage.
    """
    await _set_pin(chaos_client, user_auth_headers)

    async def exploding_deposit(self, req):  # noqa: ANN001, ARG001
        raise httpx.ConnectError("Simulated outage")

    monkeypatch.setattr(mtn_momo.MTNMoMoAdapter, "initiate_deposit", exploding_deposit)

    failed = await chaos_client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 5000.00, "phone_number": "+237677001122"},
        headers=user_auth_headers,
    )
    assert failed.status_code >= 400

    # Provider recovers.
    monkeypatch.undo()

    before_balance, _ = await _balance(chaos_client, user_auth_headers)

    recovered = await chaos_client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 5000.00, "phone_number": "+237677001122"},
        headers=user_auth_headers,
    )
    assert recovered.status_code == 202, (
        f"Service did not recover after the partition healed: {recovered.text}"
    )

    tx = recovered.json()["data"]
    settle = await chaos_client.post(
        "/api/v1/webhooks/mtn",
        json={
            "transaction_id": tx["transaction_id"],
            "external_ref": tx["external_ref"],
            "status": "SUCCESSFUL",
        },
    )
    assert settle.status_code == 200

    after_balance, _ = await _balance(chaos_client, user_auth_headers)
    assert after_balance > before_balance, "Post-recovery deposit did not credit"
