"""Operator callback endpoints (M1 / A8).

The rule this module implements: **a callback is a hint, the operator's status
API is the truth.** Neither MTN nor Orange signs its notifications — MTN signs
nothing at all, Orange echoes a per-order `notif_token` — so nothing in a
callback body is allowed to move the ledger. Each callback is used to identify a
transaction, and settlement happens only on the answer from the operator's own
status endpoint, fetched with credentials only we hold.

Two shapes are understood:

* **Operator-native** (`sandbox`/`live`): MTN's
  `{"externalId", "transactionStatus", "financialTransactionId", ...}`, and
  Orange's `{"status", "notif_token", "txnid"}`. Parsing lives in the adapters.
* **Normalised** (`mock` only): the shape the in-process simulator and the
  `mock-telco` dev tool send. It carries a status and an identifier with no
  authenticity evidence whatsoever, so it is refused whenever real money could
  be moving — accepting it there would be a money-printing endpoint.

HTTP status semantics, which matter because MTN retries any non-2xx response:

* `200` — settled, or already final (duplicate). Nothing more to do.
* `202` — accepted and recorded, but deliberately not settled (an amount
  mismatch, which retrying cannot fix and a human must).
* `503` — we could not establish the truth yet (the operator's status endpoint
  has no conclusive answer). A retry is genuinely wanted.
* `400/403` — the body is not an operator callback, or it does not belong to the
  transaction it claims. No retry will help; this is an attack or a misroute.
"""
from fastapi import APIRouter, Depends, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from payday.core.config import settings
from payday.core.database import get_db
from payday.core.exceptions import PayDayException
from payday.core.logging import logger
from payday.models.transaction import Transaction, TransactionChannel, TransactionStatus
from payday.schemas.common import APIResponse
from payday.schemas.transaction import WebhookCallbackPayload
from payday.services.transaction_manager import SettlementOutcome, transaction_manager
from payday.adapters.base import ProviderCallback
from payday.adapters.factory import adapter_factory

router = APIRouter(prefix="/webhooks", tags=["Telco Webhooks"])


def _unauthorized(provider: str, reason: str) -> PayDayException:
    return PayDayException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Callback rejected: {reason}",
        code="WEBHOOK_UNAUTHORIZED",
        title=f"Unauthorized {provider} Webhook",
    )


def _bad_callback(provider: str) -> PayDayException:
    return PayDayException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            f"Body is not a recognised {provider} callback. PayDay does not settle "
            "from callback bodies; send the operator's own notification shape."
        ),
        code="WEBHOOK_UNRECOGNISED",
        title=f"Unrecognised {provider} Webhook",
    )


def _provider_reported_amount(raw: dict) -> object:
    """The amount the operator reports in its status response, when it sends one.

    MTN's status response includes `amount`; Orange's documented response does
    not. Returning None means "cannot check", which is recorded rather than
    treated as a match.
    """
    return raw.get("amount") if isinstance(raw, dict) else None


async def _find_transaction_for_callback(
    db: AsyncSession, channel: TransactionChannel, callback: ProviderCallback
) -> Transaction:
    """Locate the transaction this callback is about.

    MTN echoes our own `transaction_id` back as `externalId`, so the lookup is by
    primary key. Orange sends neither an order id nor a reference — only the
    `notif_token` — so the transaction is found by the token we stored at
    initiation. In both cases the lookup is scoped to the channel, so an MTN
    callback can never settle an Orange transaction.
    """
    if callback.notif_token:
        query = select(Transaction).where(
            Transaction.provider_notif_token == callback.notif_token
        )
    elif callback.transaction_id:
        query = select(Transaction).where(
            Transaction.transaction_id == callback.transaction_id
        )
    else:  # pragma: no cover - parse_callback already rejects these
        raise _bad_callback(channel.value)

    result = await db.execute(query.where(Transaction.channel == channel))
    transaction = result.scalars().first()

    if transaction is None and callback.transaction_id and not callback.notif_token:
        # MTN defines `externalId` as the caller's own value, and this codebase
        # sends the transaction id there. If a merchant account is ever
        # configured to echo the `X-Reference-Id` instead, the callback is still
        # ours — but only accept it as a fallback, never as the primary key.
        fallback = await db.execute(
            select(Transaction).where(
                Transaction.external_ref == callback.transaction_id,
                Transaction.channel == channel,
            )
        )
        transaction = fallback.scalars().first()

    if transaction is None:
        logger.warning(
            f"[WEBHOOK] {channel.value} callback does not match any transaction "
            f"(notif_token={'set' if callback.notif_token else 'absent'}, "
            f"transaction_id={callback.transaction_id})"
        )
        raise _unauthorized(channel.value, "no matching transaction")
    return transaction


async def _settle_from_callback(
    *,
    db: AsyncSession,
    channel: TransactionChannel,
    callback: ProviderCallback,
    adapter,
) -> tuple[APIResponse, int]:
    """Identify, verify, re-query, settle. Used by both operator endpoints."""
    transaction = await _find_transaction_for_callback(db, channel, callback)

    if not adapter.verify_callback_authenticity(
        callback, transaction.provider_notif_token
    ):
        # MTN has nothing to check locally and returns True here; Orange's
        # notif_token comparison is the control that lands in this branch.
        logger.warning(
            f"[WEBHOOK] {channel.value} callback for transaction "
            f"{transaction.transaction_id} failed authenticity check"
        )
        raise _unauthorized(channel.value, "authenticity could not be established")

    if transaction.status not in (TransactionStatus.PROCESSING, TransactionStatus.PENDING):
        logger.info(
            f"[WEBHOOK] {channel.value} callback for transaction "
            f"{transaction.transaction_id} which is already {transaction.status.value}"
        )
        return (
            APIResponse(
                success=True,
                message=f"Duplicate callback; transaction already {transaction.status.value}",
                data={
                    "transaction_id": transaction.transaction_id,
                    "status": transaction.status.value,
                    "settled": False,
                },
            ),
            status.HTTP_200_OK,
        )

    requery = await adapter.query_status(
        channel_ref=transaction.external_ref,
        tx_type=transaction.type.value,
        order_id=transaction.provider_order_id,
        amount=transaction.amount,
    )

    outcome, transaction = await transaction_manager.settle_from_provider(
        db=db,
        transaction=transaction,
        provider_status=requery.status,
        provider_txn_id=requery.provider_txn_id or callback.provider_txn_id,
        provider_order_id=requery.provider_order_id,
        provider_amount=_provider_reported_amount(requery.raw_response),
    )

    if outcome is SettlementOutcome.INCONCLUSIVE:
        # Not "unprocessable" and not an error on our side either: the operator
        # simply has no verdict yet. A retry is wanted, so it must not be 2xx.
        raise PayDayException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"{channel.value} has no conclusive status for this transaction yet "
                f"({requery.message}). Not settled; the callback may be retried."
            ),
            code="PROVIDER_STATUS_PENDING",
            title="Provider Status Pending",
            headers={"Retry-After": "60"},
        )

    if outcome is SettlementOutcome.AMOUNT_MISMATCH:
        logger.error(
            f"[WEBHOOK] {channel.value} amount mismatch on "
            f"{transaction.transaction_id}; acknowledged but NOT settled."
        )
        return (
            APIResponse(
                success=True,
                message=(
                    "Callback recorded but not settled: the operator reports a "
                    "different amount. Flagged for manual review."
                ),
                data={
                    "transaction_id": transaction.transaction_id,
                    "status": transaction.status.value,
                    "settled": False,
                    "reason": transaction.failure_reason,
                },
            ),
            status.HTTP_202_ACCEPTED,
        )

    return (
        APIResponse(
            success=True,
            message=f"{channel.value} callback settled: {transaction.status.value}",
            data={
                "transaction_id": transaction.transaction_id,
                "status": transaction.status.value,
                "settled": outcome is SettlementOutcome.SETTLED,
                "provider_status": requery.status,
            },
        ),
        status.HTTP_200_OK,
    )


async def _handle_mock_callback(
    *,
    db: AsyncSession,
    channel: TransactionChannel,
    channel_key: str,
    raw_body: dict,
    raw_bytes: bytes,
    headers: dict,
) -> APIResponse:
    """The mock simulator path: the normalised shape, mock-only.

    Kept byte-compatible with the behaviour the suite and the Flutter dev tool
    rely on, so the simulator keeps working while real callbacks take the
    verified path above.
    """
    adapter = adapter_factory.get_adapter(channel_key)
    is_valid = await adapter.verify_webhook_signature(headers, raw_bytes)
    if not is_valid:
        logger.warning(
            f"[WEBHOOK] Unauthorized {channel_key} webhook callback signature / token rejected"
        )
        raise _unauthorized(channel_key, "invalid or missing webhook signature credentials")

    try:
        payload = WebhookCallbackPayload.model_validate(raw_body)
    except ValidationError as exc:
        # Render exactly as FastAPI would have, so the RFC 7807 contract holds.
        raise RequestValidationError(exc.errors()) from exc

    tx = await transaction_manager.process_webhook(
        db=db, channel=channel, payload=payload
    )
    return APIResponse(
        success=True,
        message=f"{channel_key} Webhook processed: Transaction status {tx.status.value}",
        data={"transaction_id": tx.transaction_id, "status": tx.status.value},
    )


@router.post(
    "/mtn",
    response_model=APIResponse[dict],
    summary="MTN MoMo Asynchronous Callback Listener",
    description=(
        "Consumed by the MTN MoMo gateway. The callback identifies the transaction; "
        "settlement is decided by MTN's own RequestToPay status endpoint, never by "
        "this body (MTN does not sign callbacks)."
    ),
)
async def mtn_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    raw_bytes = await request.body()
    raw_body = await request.json()
    if settings.TELCO_MODE == "mock":
        return await _handle_mock_callback(
            db=db,
            channel=TransactionChannel.MTN,
            channel_key="MTN",
            raw_body=raw_body,
            raw_bytes=raw_bytes,
            headers=dict(request.headers),
        )

    adapter = adapter_factory.get_adapter("MTN")
    callback = adapter.parse_callback(raw_body)
    if callback is None:
        raise _bad_callback("MTN")

    response, http_status = await _settle_from_callback(
        db=db, channel=TransactionChannel.MTN, callback=callback, adapter=adapter
    )
    return JSONResponse(status_code=http_status, content=response.model_dump(mode="json"))


@router.post(
    "/orange",
    response_model=APIResponse[dict],
    summary="Orange Money IPN Callback Listener",
    description=(
        "Consumed by Orange Money. Orange sends only {status, notif_token, txnid}: "
        "the notif_token stored at initiation identifies the order, and Orange's "
        "transactionstatus endpoint decides the outcome."
    ),
)
async def orange_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    raw_bytes = await request.body()
    raw_body = await request.json()
    if settings.TELCO_MODE == "mock":
        return await _handle_mock_callback(
            db=db,
            channel=TransactionChannel.ORANGE,
            channel_key="ORANGE",
            raw_body=raw_body,
            raw_bytes=raw_bytes,
            headers=dict(request.headers),
        )

    adapter = adapter_factory.get_adapter("ORANGE")
    callback = adapter.parse_callback(raw_body)
    if callback is None:
        raise _bad_callback("ORANGE")

    response, http_status = await _settle_from_callback(
        db=db, channel=TransactionChannel.ORANGE, callback=callback, adapter=adapter
    )
    return JSONResponse(status_code=http_status, content=response.model_dump(mode="json"))
