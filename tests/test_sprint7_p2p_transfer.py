"""P2P transfers, and the controls that were declared but never wired.

What this file defends
---------------------
"Send money to anyone in Cameroon" is the frontend's headline action and was
blocked on the backend (docs/FRONTEND_INTEGRATION_GUIDE.md #19). Three things had
to be true before it could ship honestly:

* **LB-15** — `wallet.monthly_limit` was settable by an admin, returned to
  clients, and enforced **nowhere**. A customer could move unlimited money out
  through any path that was not a single large withdrawal.
* **LB-16** — `KycRequiredError` and `get_current_verified_user` existed and were
  never used: an unverified user could move money.
* **LB-17** — there was no ceiling on any credit, although the published site
  promises a 5,000,000 maximum balance.

And it defends the property that matters most about an internal transfer: it
moves money between two wallets **atomically**, leaving the sum unchanged. A
transfer that debits without crediting is the worst bug this system can have, so
conservation is asserted directly rather than inferred from balances.
"""
from __future__ import annotations

from collections import namedtuple
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from payday.core.config import settings
from payday.core.security import get_password_hash
from payday.models.transaction import (
    Transaction,
    TransactionChannel,
    TransactionDirection,
    TransactionStatus,
    TransactionType,
)
from payday.models.user import KycStatus, User, UserRole, UserStatus
from payday.models.wallet import Wallet, WalletStatus


async def _make_user(db, phone: str, *, kyc: KycStatus = KycStatus.VERIFIED,
                     balance: str = "100000", pin: str = "1234") -> User:
    user = User(
        full_name="Test Counterparty",
        phone_number=phone,
        password_hash=get_password_hash("SecretP@ssword123"),
        kyc_status=kyc,
        role=UserRole.CUSTOMER,
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    await db.flush()
    db.add(
        Wallet(
            user_id=user.user_id,
            balance=Decimal(balance),
            locked_balance=Decimal("0.00"),
            currency="XAF",
            status=WalletStatus.ACTIVE,
            daily_limit=Decimal("500000.00"),
            monthly_limit=Decimal("5000000.00"),
        )
    )
    await db.commit()
    await db.refresh(user)
    return user


def _headers(user: User) -> dict:
    """Bearer token for any user, so tests can drive both sides of a transfer."""
    from payday.core.security import create_access_token

    token = create_access_token(
        subject=user.user_id, role=user.role.value, token_version=user.token_version
    )
    return {"Authorization": f"Bearer {token}"}


#: A read-only snapshot. Deliberately NOT an ORM instance: the API commits
#: through its own session, so an identity-mapped `Wallet` in the test session can
#: hold pre-transfer values -- which would make a moved balance look unchanged and
#: hide a real bug. Selecting columns returns the database's current answer and
#: never touches the identity map.
WalletSnapshot = namedtuple("WalletSnapshot", "wallet_id balance locked_balance")


async def _wallet_row(db, user_id: str) -> Wallet:
    """The ORM row, for tests that *change* a wallet (limits, status)."""
    return (await db.execute(select(Wallet).where(Wallet.user_id == user_id))).scalars().one()


async def _wallet(db, user_id: str) -> WalletSnapshot:
    result = await db.execute(
        select(Wallet.wallet_id, Wallet.balance, Wallet.locked_balance).where(
            Wallet.user_id == user_id
        )
    )
    row = result.one()
    return WalletSnapshot(row[0], Decimal(str(row[1])), Decimal(str(row[2])))


async def _balance(client: AsyncClient, headers: dict) -> Decimal:
    res = await client.get("/api/v1/wallet/balance", headers=headers)
    return Decimal(str(res.json()["data"]["balance"]))


async def _set_pin(client: AsyncClient, headers: dict, pin: str = "1234") -> None:
    res = await client.post(
        "/api/v1/auth/set-pin",
        json={"password": "SecretP@ssword123", "pin": pin},
        headers=headers,
    )
    assert res.status_code == 200, res.text


async def _transfer(client: AsyncClient, headers: dict, recipient: str, amount: int,
                    pin: str = "1234", **kwargs) -> dict:
    payload = {"recipient_phone": recipient, "amount": amount, "pin": pin, **kwargs}
    res = await client.post("/api/v1/wallet/transfer", json=payload, headers=headers)
    return {"status_code": res.status_code, "body": res.json()}


# --------------------------------------------------------------------------- #
# The happy path, and what it proves
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_transfer_between_two_payday_wallets_is_instant_and_free(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    await _set_pin(client, user_auth_headers)
    recipient = await _make_user(db_session, "+237677998877", balance="1000")
    sender_before = await _balance(client, user_auth_headers)
    recipient_before = await _wallet(db_session, recipient.user_id)

    result = await _transfer(client, user_auth_headers, "+237677998877", 5000)

    assert result["status_code"] == 202, result
    data = result["body"]["data"]
    assert data["status"] == "SUCCESS", "an internal transfer is final immediately"
    assert data["type"] == "TRANSFER"
    assert data["channel"] == "PAYDAY"
    assert data["internal"] is True
    assert data["direction"] == "DEBIT"
    assert Decimal(str(data["fee"])) == Decimal("0.00"), "P2P is free"
    assert data["counterparty_msisdn_masked"] == "+23767•••877"

    # Money left one side and arrived at the other, exactly.
    assert await _balance(client, user_auth_headers) == sender_before - Decimal("5000")
    recipient_after = await _wallet(db_session, recipient.user_id)
    assert recipient_after.balance == recipient_before.balance + Decimal("5000")


@pytest.mark.asyncio
async def test_both_legs_exist_and_the_transfer_group_is_conserved(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    """The invariant that makes an internal transfer safe: Σ legs = 0."""
    await _set_pin(client, user_auth_headers)
    recipient = await _make_user(db_session, "+237677998877")
    sender_wallet_id = (await _wallet(db_session, test_user.user_id)).wallet_id
    sender_before = (await _wallet(db_session, test_user.user_id)).balance
    recipient_before = (await _wallet(db_session, recipient.user_id)).balance

    result = await _transfer(client, user_auth_headers, "+237677998877", 7500)
    group_id = result["body"]["data"]["transfer_group_id"]
    assert group_id

    legs = (
        await db_session.execute(
            select(Transaction).where(Transaction.transfer_group_id == group_id)
        )
    ).scalars().all()

    assert len(legs) == 2, "one debit and one credit, always"
    debits = sum(l.amount for l in legs if l.direction == TransactionDirection.DEBIT)
    credits = sum(l.amount for l in legs if l.direction == TransactionDirection.CREDIT)
    assert debits == credits == Decimal("7500.00")

    # Records can agree while the balances do not, so check the balances too:
    # the sender lost exactly what the recipient gained, and the pair nets to
    # zero. (A mutation that credits the sender instead was caught by this.)
    sender_after = (await _wallet(db_session, test_user.user_id)).balance
    recipient_after = (await _wallet(db_session, recipient.user_id)).balance
    assert sender_before - sender_after == recipient_after - recipient_before == Decimal("7500.00")

    by_direction = {l.direction: l for l in legs}
    assert sum(1 for l in legs if l.direction == TransactionDirection.DEBIT) == 1
    assert by_direction[TransactionDirection.DEBIT].wallet_id == sender_wallet_id
    assert by_direction[TransactionDirection.DEBIT].status == TransactionStatus.SUCCESS
    assert by_direction[TransactionDirection.CREDIT].wallet_id == (
        await _wallet(db_session, recipient.user_id)
    ).wallet_id
    # The recipient's leg is a credit of the same amount with no fee.
    assert by_direction[TransactionDirection.CREDIT].fee == Decimal("0.00")


@pytest.mark.asyncio
async def test_recipient_receives_a_credit_notification_naming_the_sender(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    """Both parties get their own message; neither is told "withdrawal"."""
    from payday.models.notification import Notification

    await _set_pin(client, user_auth_headers)
    recipient = await _make_user(db_session, "+237677998877")
    await _transfer(client, user_auth_headers, "+237677998877", 2000)

    notes = (
        await db_session.execute(
            select(Notification).where(Notification.user_id == recipient.user_id)
        )
    ).scalars().all()
    assert notes, "the recipient must be told they were paid"
    assert any("You received 2,000.00 XAF" in n.message for n in notes), [
        n.message for n in notes
    ]
    assert all("withdrawal" not in n.message.lower() for n in notes)


# --------------------------------------------------------------------------- #
# Refusals — none of which may move money
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_transfer_to_a_non_payday_number_asks_for_a_channel(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    await _set_pin(client, user_auth_headers)
    before = await _balance(client, user_auth_headers)

    result = await _transfer(client, user_auth_headers, "+237691234567", 3000)

    assert result["status_code"] == 400
    assert result["body"]["code"] == "RECIPIENT_NOT_ON_PAYDAY"
    # The prefix hint is offered as a hint, because numbers can be ported.
    assert result["body"]["extra"]["suggested_channel"] == "ORANGE"
    assert await _balance(client, user_auth_headers) == before


@pytest.mark.asyncio
async def test_transfer_to_a_non_payday_number_with_a_channel_delegates_to_the_operator(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    """Anyone not on PayDay is paid out through the existing withdrawal path."""
    await _set_pin(client, user_auth_headers)
    before = await _balance(client, user_auth_headers)

    result = await _transfer(
        client, user_auth_headers, "+237691234567", 4000, channel="ORANGE"
    )

    assert result["status_code"] == 202, result
    data = result["body"]["data"]
    assert data["type"] == "WITHDRAW", "external sends are operator payouts"
    assert data["channel"] == "ORANGE"
    assert data["internal"] is False
    assert data["status"] == "PROCESSING"
    # 1% withdrawal fee on 4000, held rather than spent.
    assert Decimal(str(data["fee"])) == Decimal("40")
    sender_wallet = await _wallet(db_session, test_user.user_id)
    assert sender_wallet.locked_balance == Decimal("4040.00")
    assert await _balance(client, user_auth_headers) == before


@pytest.mark.asyncio
async def test_cannot_send_to_your_own_wallet(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    await _set_pin(client, user_auth_headers)
    before = await _balance(client, user_auth_headers)
    result = await _transfer(client, user_auth_headers, "+237699112233", 1000)
    assert result["status_code"] == 400
    assert result["body"]["code"] == "SELF_TRANSFER"
    assert await _balance(client, user_auth_headers) == before


@pytest.mark.asyncio
async def test_transfer_requires_the_correct_pin(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    await _set_pin(client, user_auth_headers)
    recipient = await _make_user(db_session, "+237677998877")
    before = await _balance(client, user_auth_headers)

    result = await _transfer(client, user_auth_headers, "+237677998877", 1000, pin="9999")

    assert result["status_code"] == 400
    assert result["body"]["code"] == "INVALID_PIN"
    assert "Attempt 1 of 5" in result["body"]["detail"]
    assert await _balance(client, user_auth_headers) == before
    assert (await _wallet(db_session, recipient.user_id)).balance == Decimal("100000.00")


@pytest.mark.asyncio
async def test_transfer_requires_a_pin_to_be_set(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    await _make_user(db_session, "+237677998877")
    result = await _transfer(client, user_auth_headers, "+237677998877", 1000)
    assert result["status_code"] == 400
    assert result["body"]["code"] == "PIN_NOT_SET"


@pytest.mark.asyncio
async def test_insufficient_funds_is_refused_without_partial_movement(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    await _set_pin(client, user_auth_headers)
    recipient = await _make_user(db_session, "+237677998877")
    before = await _balance(client, user_auth_headers)

    result = await _transfer(client, user_auth_headers, "+237677998877", 999999)

    assert result["status_code"] == 400
    assert result["body"]["code"] == "INSUFFICIENT_FUNDS"
    assert await _balance(client, user_auth_headers) == before
    assert (await _wallet(db_session, recipient.user_id)).balance == Decimal("100000.00")


@pytest.mark.asyncio
async def test_a_full_recipient_wallet_stops_the_transfer_before_anything_moves(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    """A credit ceiling must fail the whole transfer, not half of it."""
    await _set_pin(client, user_auth_headers)
    # Recipient is one franc short of the published ceiling.
    recipient = await _make_user(
        db_session, "+237677998877", balance=str(settings.MAX_WALLET_BALANCE - 1)
    )
    sender_before = await _balance(client, user_auth_headers)
    recipient_before = (await _wallet(db_session, recipient.user_id)).balance

    result = await _transfer(client, user_auth_headers, "+237677998877", 100)

    assert result["status_code"] == 400
    assert result["body"]["code"] == "BALANCE_CEILING_EXCEEDED"
    assert "5,000,000" in result["body"]["detail"]
    # Neither side moved.
    assert await _balance(client, user_auth_headers) == sender_before
    assert (await _wallet(db_session, recipient.user_id)).balance == recipient_before


@pytest.mark.asyncio
async def test_inactive_recipient_is_refused(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    await _set_pin(client, user_auth_headers)
    recipient = await _make_user(db_session, "+237677998877")
    recipient.status = UserStatus.SUSPENDED
    await db_session.commit()
    before = await _balance(client, user_auth_headers)

    result = await _transfer(client, user_auth_headers, "+237677998877", 1000)

    assert result["status_code"] == 400
    assert result["body"]["code"] == "RECIPIENT_NOT_ACTIVE"
    assert await _balance(client, user_auth_headers) == before


@pytest.mark.asyncio
async def test_replayed_transfer_does_not_move_money_twice(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    await _set_pin(client, user_auth_headers)
    recipient = await _make_user(db_session, "+237677998877")
    before = await _balance(client, user_auth_headers)

    first = await _transfer(
        client, user_auth_headers, "+237677998877", 2500, idempotency_key="p2p-replay-1"
    )
    after_first = await _balance(client, user_auth_headers)
    second = await _transfer(
        client, user_auth_headers, "+237677998877", 2500, idempotency_key="p2p-replay-1"
    )

    assert first["body"]["data"]["transaction_id"] == second["body"]["data"]["transaction_id"]
    assert after_first == before - Decimal("2500")
    assert await _balance(client, user_auth_headers) == after_first
    assert (await _wallet(db_session, recipient.user_id)).balance == Decimal("102500.00")


# --------------------------------------------------------------------------- #
# LB-15 — the monthly limit is finally enforced
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_monthly_limit_blocks_outgoing_money(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    await _set_pin(client, user_auth_headers)
    recipient = await _make_user(db_session, "+237677998877")
    sender_wallet = await _wallet_row(db_session, test_user.user_id)
    sender_wallet.monthly_limit = Decimal("10000.00")
    sender_wallet.daily_limit = Decimal("500000.00")
    await db_session.commit()
    before = await _balance(client, user_auth_headers)

    result = await _transfer(client, user_auth_headers, "+237677998877", 10001)

    assert result["status_code"] == 400
    assert result["body"]["code"] == "MONTHLY_LIMIT_EXCEEDED"
    assert await _balance(client, user_auth_headers) == before
    assert (await _wallet(db_session, recipient.user_id)).balance == Decimal("100000.00")


@pytest.mark.asyncio
async def test_monthly_limit_counts_every_kind_of_outgoing_money(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    """A limit that only counted withdrawals would be trivially bypassable."""
    await _set_pin(client, user_auth_headers)
    recipient = await _make_user(db_session, "+237677998877")
    sender_wallet = await _wallet_row(db_session, test_user.user_id)
    sender_wallet.monthly_limit = Decimal("6000.00")
    await db_session.commit()

    first = await _transfer(client, user_auth_headers, "+237677998877", 4000)
    assert first["status_code"] == 202
    # The month's budget is now 2000; a second 4000 must fail even though the
    # balance is plenty.
    second = await _transfer(client, user_auth_headers, "+237677998877", 4000)
    assert second["status_code"] == 400
    assert second["body"]["code"] == "MONTHLY_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_daily_limit_counts_transfers_too(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    await _set_pin(client, user_auth_headers)
    await _make_user(db_session, "+237677998877")
    sender_wallet = await _wallet_row(db_session, test_user.user_id)
    sender_wallet.daily_limit = Decimal("3000.00")
    await db_session.commit()

    first = await _transfer(client, user_auth_headers, "+237677998877", 2000)
    assert first["status_code"] == 202
    second = await _transfer(client, user_auth_headers, "+237677998877", 2000)
    assert second["status_code"] == 400
    assert second["body"]["code"] == "DAILY_LIMIT_EXCEEDED"


# --------------------------------------------------------------------------- #
# LB-16 — KYC is required to move money out
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_unverified_user_cannot_send_money(
    client: AsyncClient, db_session, test_user: User
):
    """The control existed in code and was wired to nothing."""
    pending = await _make_user(
        db_session, "+237699555111", kyc=KycStatus.PENDING, balance="50000"
    )
    headers = _headers(pending)
    recipient = await _make_user(db_session, "+237677998877")

    result = await _transfer(client, headers, "+237677998877", 1000)

    assert result["status_code"] == 403
    assert result["body"]["code"] == "KYC_REQUIRED"
    assert (await _wallet(db_session, pending.user_id)).balance == Decimal("50000.00")
    assert (await _wallet(db_session, recipient.user_id)).balance == Decimal("100000.00")


@pytest.mark.asyncio
async def test_unverified_user_cannot_withdraw_to_mobile_money(
    client: AsyncClient, db_session, test_user: User
):
    pending = await _make_user(db_session, "+237699555222", kyc=KycStatus.PENDING)
    headers = _headers(pending)

    res = await client.post(
        "/api/v1/wallet/withdraw",
        json={
            "channel": "MTN",
            "amount": 1000,
            "destination_phone": "+237677998877",
            "pin": "1234",
        },
        headers=headers,
    )

    assert res.status_code == 403
    assert res.json()["code"] == "KYC_REQUIRED"


@pytest.mark.asyncio
async def test_unverified_user_can_still_receive_money(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    """Receiving is not an outgoing risk, and blocking it would strand funds."""
    await _set_pin(client, user_auth_headers)
    recipient = await _make_user(
        db_session, "+237677998877", kyc=KycStatus.PENDING, balance="1000"
    )

    result = await _transfer(client, user_auth_headers, "+237677998877", 2000)

    assert result["status_code"] == 202, result
    assert (await _wallet(db_session, recipient.user_id)).balance == Decimal("3000.00")


@pytest.mark.asyncio
async def test_unverified_user_can_still_deposit(
    client: AsyncClient, db_session, test_user: User
):
    """Funding your own wallet before verification must keep working."""
    pending = await _make_user(db_session, "+237699555333", kyc=KycStatus.PENDING)
    headers = _headers(pending)

    res = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 5000, "phone_number": "677112233"},
        headers=headers,
    )

    assert res.status_code == 202, res.text
    assert res.json()["data"]["type"] == "DEPOSIT"


# --------------------------------------------------------------------------- #
# History and receipts describe transfers correctly
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_history_carries_direction_and_counterparty(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    await _set_pin(client, user_auth_headers)
    await _make_user(db_session, "+237677998877")
    await _transfer(client, user_auth_headers, "+237677998877", 1500)

    res = await client.get("/api/v1/wallet/transactions", headers=user_auth_headers)
    assert res.status_code == 200
    row = res.json()["data"]["items"][0]
    assert row["type"] == "TRANSFER"
    assert row["direction"] == "DEBIT"
    assert row["internal"] is True
    assert row["counterparty_msisdn_masked"] == "+23767•••877"


@pytest.mark.asyncio
async def test_receipt_for_a_transfer_names_the_recipient_and_charges_nothing_extra(
    client: AsyncClient, db_session, test_user: User, user_auth_headers: dict
):
    await _set_pin(client, user_auth_headers)
    await _make_user(db_session, "+237677998877")
    tx_id = (await _transfer(client, user_auth_headers, "+237677998877", 3000))["body"]["data"]["transaction_id"]

    res = await client.get(f"/api/v1/wallet/transactions/{tx_id}", headers=user_auth_headers)
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["counterparty_msisdn_masked"] == "+23767•••877"
    assert Decimal(str(data["total_charged"])) == Decimal("3000")
    assert Decimal(str(data["fee"])) == Decimal("0.00")


@pytest.mark.asyncio
async def test_recipient_sees_the_credit_in_their_own_history(
    client: AsyncClient, db_session, test_user: User
):
    sender = await _make_user(db_session, "+237699666111", balance="50000")
    recipient = await _make_user(db_session, "+237699666222", balance="0")
    sender_headers = _headers(sender)
    await _set_pin(client, sender_headers)

    assert (await _transfer(client, sender_headers, "+237699666222", 4000))["status_code"] == 202

    recipient_headers = _headers(recipient)
    res = await client.get("/api/v1/wallet/transactions", headers=recipient_headers)
    row = res.json()["data"]["items"][0]
    assert row["direction"] == "CREDIT"
    assert row["counterparty_msisdn_masked"] == "+23769•••111"
    assert (await _wallet(db_session, recipient.user_id)).balance == Decimal("4000.00")


# --------------------------------------------------------------------------- #
# Fee schedule: "free" must actually be free
# --------------------------------------------------------------------------- #
def test_transfer_fee_is_zero_and_not_floored_by_the_minimum():
    """MIN_FEE_AMOUNT is 25 XAF; applying it to P2P would contradict the website."""
    from payday.services.wallet_engine import wallet_engine

    for amount in (Decimal("1"), Decimal("500"), Decimal("12345")):
        assert wallet_engine.calculate_fee(TransactionType.TRANSFER, amount) == Decimal("0")


def test_deposit_and_withdrawal_fees_are_unchanged():
    """The regression net for the fee refactor."""
    from payday.services.wallet_engine import wallet_engine

    assert wallet_engine.calculate_fee(TransactionType.DEPOSIT, Decimal("10000")) == Decimal("50")
    assert wallet_engine.calculate_fee(TransactionType.WITHDRAW, Decimal("10000")) == Decimal("100")
    # The floor still applies where it was meant to.
    assert wallet_engine.calculate_fee(TransactionType.DEPOSIT, Decimal("1000")) == Decimal("25")
