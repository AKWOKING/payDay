"""Sprint 4 — Pilot Business-Day Simulation.

An end-to-end rehearsal of one trading day across the Triangle Model: several
customers transacting on both MTN MoMo and Orange Money, an operations-led
reversal, and the settlement reconciliation sweep.

The controlling assertion is **conservation of value**. Every wallet movement
is reconstructed from the transaction ledger and checked against the observed
balance delta. Individual endpoints can each behave correctly while the
aggregate still drifts, and drift is exactly what a pilot is meant to surface
before real money is involved.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient

from payday.core.security import create_access_token, get_password_hash
from payday.models.user import KycStatus, User, UserRole, UserStatus
from payday.models.wallet import Wallet, WalletStatus

PASSWORD = "SecretP@ssword123"
OPENING_BALANCE = Decimal("50000.00")


async def _make_customer(db_session, name: str, phone: str) -> tuple[User, dict]:
    """Create a verified customer with a funded wallet; return user + headers."""
    user = User(
        full_name=name,
        phone_number=phone,
        email=f"{phone.strip('+')}@pilot.payday.cm",
        password_hash=get_password_hash(PASSWORD),
        kyc_status=KycStatus.VERIFIED,
        role=UserRole.CUSTOMER,
        status=UserStatus.ACTIVE,
    )
    db_session.add(user)
    await db_session.flush()

    db_session.add(
        Wallet(
            user_id=user.user_id,
            balance=OPENING_BALANCE,
            locked_balance=Decimal("0.00"),
            currency="XAF",
            status=WalletStatus.ACTIVE,
            daily_limit=Decimal("500000.00"),
            monthly_limit=Decimal("5000000.00"),
        )
    )
    await db_session.commit()
    await db_session.refresh(user)

    headers = {
        "Authorization": f"Bearer {create_access_token(subject=user.user_id, role=user.role.value)}"
    }
    return user, headers


async def _balance(client: AsyncClient, headers: dict) -> Decimal:
    response = await client.get("/api/v1/wallet/balance", headers=headers)
    assert response.status_code == 200, response.text
    return Decimal(str(response.json()["data"]["balance"]))


async def _settled_deposit(
    client: AsyncClient, headers: dict, channel: str, amount: Decimal, phone: str
) -> dict:
    """Initiate a deposit and settle it through the provider webhook."""
    initiated = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": channel, "amount": float(amount), "phone_number": phone},
        headers=headers,
    )
    assert initiated.status_code == 202, initiated.text
    tx = initiated.json()["data"]

    webhook = "mtn" if channel == "MTN" else "orange"
    settled = await client.post(
        f"/api/v1/webhooks/{webhook}",
        json={
            "transaction_id": tx["transaction_id"],
            "external_ref": tx["external_ref"],
            "status": "SUCCESSFUL",
        },
    )
    assert settled.status_code == 200, settled.text
    return tx


async def _settled_withdrawal(
    client: AsyncClient, headers: dict, channel: str, amount: Decimal, phone: str, pin: str
) -> dict:
    initiated = await client.post(
        "/api/v1/wallet/withdraw",
        json={
            "channel": channel,
            "amount": float(amount),
            "destination_phone": phone,
            "pin": pin,
        },
        headers=headers,
    )
    assert initiated.status_code == 202, initiated.text
    tx = initiated.json()["data"]

    webhook = "mtn" if channel == "MTN" else "orange"
    settled = await client.post(
        f"/api/v1/webhooks/{webhook}",
        json={
            "transaction_id": tx["transaction_id"],
            "external_ref": tx["external_ref"],
            "status": "SUCCESSFUL",
        },
    )
    assert settled.status_code == 200, settled.text
    return tx


@pytest.mark.asyncio
async def test_full_pilot_business_day_conserves_value(
    client: AsyncClient,
    db_session,
    test_admin: User,
    admin_auth_headers: dict,
) -> None:
    """Three customers trade across both channels for a day; value must balance.

    Reconstructs each wallet's expected closing balance from its own ledger and
    compares it to the reported balance. Any discrepancy is money invented or
    destroyed.
    """
    alice, alice_h = await _make_customer(db_session, "Alice Ngo", "+237677100001")
    brian, brian_h = await _make_customer(db_session, "Brian Tabi", "+237677100002")
    clarisse, clarisse_h = await _make_customer(db_session, "Clarisse Eyong", "+237677100003")

    for headers, pin in ((alice_h, "1111"), (brian_h, "2222"), (clarisse_h, "3333")):
        response = await client.post(
            "/api/v1/auth/set-pin",
            json={"pin": pin, "password": PASSWORD},
            headers=headers,
        )
        assert response.status_code in (200, 201), response.text

    # --- Morning: inbound collections ---------------------------------------
    await _settled_deposit(client, alice_h, "MTN", Decimal("40000.00"), "+237677100001")
    await _settled_deposit(client, brian_h, "ORANGE", Decimal("25000.00"), "+237699100002")
    await _settled_deposit(client, clarisse_h, "MTN", Decimal("15000.00"), "+237677100003")

    # --- Afternoon: cross-channel payouts (the Triangle bridge) -------------
    await _settled_withdrawal(
        client, alice_h, "ORANGE", Decimal("30000.00"), "+237699200001", "1111"
    )
    await _settled_withdrawal(
        client, brian_h, "MTN", Decimal("10000.00"), "+237677200002", "2222"
    )

    # --- Close of day: reconstruct each ledger ------------------------------
    for name, headers in (
        ("Alice", alice_h),
        ("Brian", brian_h),
        ("Clarisse", clarisse_h),
    ):
        history = await client.get(
            "/api/v1/wallet/transactions?page=1&page_size=100", headers=headers
        )
        assert history.status_code == 200, history.text
        items = history.json()["data"]["items"]

        expected = OPENING_BALANCE
        for item in items:
            if item["status"] != "SUCCESS":
                continue
            amount = Decimal(str(item["amount"]))
            fee = Decimal(str(item["fee"]))
            if item["type"] == "DEPOSIT":
                expected += amount - fee
            elif item["type"] == "WITHDRAW":
                expected -= amount + fee

        actual = await _balance(client, headers)
        assert actual == expected, (
            f"{name}: ledger says {expected} XAF but wallet reports {actual} XAF "
            f"(drift {actual - expected} XAF)"
        )

        # No funds may remain held once every transaction has settled.
        wallet = await client.get("/api/v1/wallet/balance", headers=headers)
        assert Decimal(str(wallet.json()["data"]["locked_balance"])) == Decimal("0.00"), (
            f"{name} still has funds locked at end of day"
        )


@pytest.mark.asyncio
async def test_operations_reversal_restores_exact_value(
    client: AsyncClient,
    db_session,
    test_admin: User,
    admin_auth_headers: dict,
) -> None:
    """A back-office reversal must restore the pre-transaction balance exactly.

    Reversal touches the ledger outside the normal customer flow, so a
    rounding or sign error here silently corrupts the books.
    """
    dora, dora_h = await _make_customer(db_session, "Dora Mbah", "+237677100004")

    before = await _balance(client, dora_h)

    tx = await _settled_deposit(client, dora_h, "MTN", Decimal("20000.00"), "+237677100004")
    after_deposit = await _balance(client, dora_h)
    assert after_deposit > before, "Deposit did not credit"

    reversal = await client.post(
        f"/api/v1/admin/transactions/{tx['transaction_id']}/reverse",
        json={"reason": "Pilot day: duplicate collection reported by MTN"},
        headers=admin_auth_headers,
    )
    assert reversal.status_code == 200, reversal.text

    after_reversal = await _balance(client, dora_h)
    assert after_reversal == before, (
        f"Reversal did not restore the original balance: "
        f"{before} -> {after_deposit} -> {after_reversal}"
    )


@pytest.mark.asyncio
async def test_double_reversal_is_refused(
    client: AsyncClient,
    db_session,
    test_admin: User,
    admin_auth_headers: dict,
) -> None:
    """Reversing an already-reversed transaction must be blocked.

    Two operators reacting to the same incident is an ordinary pilot-day
    occurrence; the second attempt must not double-debit the customer.
    """
    eric, eric_h = await _make_customer(db_session, "Eric Fon", "+237677100005")

    tx = await _settled_deposit(client, eric_h, "MTN", Decimal("12000.00"), "+237677100005")

    first = await client.post(
        f"/api/v1/admin/transactions/{tx['transaction_id']}/reverse",
        json={"reason": "First operator reversal"},
        headers=admin_auth_headers,
    )
    assert first.status_code == 200, first.text
    balance_after_first = await _balance(client, eric_h)

    second = await client.post(
        f"/api/v1/admin/transactions/{tx['transaction_id']}/reverse",
        json={"reason": "Second operator, same incident"},
        headers=admin_auth_headers,
    )
    assert second.status_code >= 400, "A transaction was reversed twice"

    assert await _balance(client, eric_h) == balance_after_first, (
        "Balance moved on a rejected second reversal"
    )


@pytest.mark.asyncio
async def test_end_of_day_reconciliation_matches_settled_ledger(
    client: AsyncClient,
    db_session,
    test_admin: User,
    admin_auth_headers: dict,
) -> None:
    """The midnight sweep must see the day's MTN volume and report no variance.

    Partner statement lines are built from the day's own settled transactions,
    so a clean run is the expected outcome; anything else means the sweep is
    miscounting.
    """
    fabrice, fabrice_h = await _make_customer(db_session, "Fabrice Nkeng", "+237677100006")

    deposits = [
        await _settled_deposit(client, fabrice_h, "MTN", Decimal(amount), "+237677100006")
        for amount in ("10000.00", "7500.00", "5000.00")
    ]

    history = await client.get(
        "/api/v1/wallet/transactions?page=1&page_size=100", headers=fabrice_h
    )
    settled = {
        item["external_ref"]: item
        for item in history.json()["data"]["items"]
        if item["status"] == "SUCCESS" and item.get("external_ref")
    }
    assert len(settled) >= len(deposits), "Not all pilot deposits settled"

    partner_records = [
        {
            "external_ref": ref,
            "amount": float(Decimal(str(item["amount"]))),
            "currency": "XAF",
            "status": "SUCCESS",
            "channel": "MTN",
        }
        for ref, item in settled.items()
    ]

    report = await client.post(
        "/api/v1/admin/reconcile",
        json={
            "channel": "MTN",
            "start_date": date.today().isoformat(),
            "end_date": date.today().isoformat(),
            "partner_records": partner_records,
        },
        headers=admin_auth_headers,
    )
    assert report.status_code == 200, report.text

    data = report.json()["data"]
    assert data["total_internal_transactions"] > 0, "Reconciliation saw no internal traffic"
    assert data["mismatches_count"] == 0, (
        f"Reconciliation reported {data['mismatches_count']} mismatches "
        f"against a self-consistent partner statement: {data.get('mismatches')}"
    )
    assert data["matched_count"] == data["total_partner_transactions"], (
        "Not every partner record was matched to the internal ledger"
    )


@pytest.mark.asyncio
async def test_reconciliation_detects_a_missing_partner_record(
    client: AsyncClient,
    db_session,
    test_admin: User,
    admin_auth_headers: dict,
) -> None:
    """The sweep must flag a settled transaction the partner never reported.

    This is the failure the reconciliation engine exists to catch — a clean
    report against a deliberately incomplete statement would make it useless.
    """
    gaelle, gaelle_h = await _make_customer(db_session, "Gaelle Awa", "+237677100007")

    await _settled_deposit(client, gaelle_h, "MTN", Decimal("9000.00"), "+237677100007")
    await _settled_deposit(client, gaelle_h, "MTN", Decimal("6000.00"), "+237677100007")

    # Partner statement omits every line.
    report = await client.post(
        "/api/v1/admin/reconcile",
        json={
            "channel": "MTN",
            "start_date": date.today().isoformat(),
            "end_date": date.today().isoformat(),
            "partner_records": [],
        },
        headers=admin_auth_headers,
    )
    assert report.status_code == 200, report.text

    data = report.json()["data"]
    assert data["mismatches_count"] > 0, (
        "Reconciliation reported no variance despite an empty partner statement"
    )
