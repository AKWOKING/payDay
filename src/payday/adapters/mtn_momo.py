"""MTN Mobile Money adapter (Cameroon) — Collections & Disbursements.

Rewritten in M1 after reconnaissance found that this adapter could not move real
money at all (`docs/plans/M1_LIVE_MONEY_PATH_PLAN.md` §2):

- the module singleton was hard-coded `use_mock=True` and no telco setting
  existed anywhere, so the live branch below was unreachable in production;
- the OAuth token cache never expired, while MTN tokens live ~1 hour;
- amounts were sent as `str(Decimal)` — `"1000.00"`, or `"1000.005"` for a
  sub-franc request — to an API that documents an integer-valued amount for a
  zero-decimal currency;
- Collections and Disbursements were driven by one credential set, though MTN
  issues separate API users, keys and subscription keys per product;
- nothing set `X-Callback-Url`, and no test ever inspected an outbound payload.

Design points:

- **Payloads are built by pure functions** (`build_requesttopay_payload`,
  `build_transfer_payload`) so tests assert the exact dictionary and headers
  without HTTP. This is the mechanism that keeps LB-9/LB-10 from returning.
- **Money is whole francs** via `core.money`; MTN documents the amount as a
  string, so it is serialised as the decimal string of an integer.
- **MSISDN keeps the country code** (`237XXXXXXXXX`) per `core.msisdn`.
- **Tokens are cached with their expiry** and refreshed before it lapses; a 401
  from a business call drops the cache and retries once.
- **Mock mode stays byte-identical** to the previous implementation so the
  existing suite continues to describe the simulator.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from payday.adapters.base import (
    ChannelDepositRequest,
    ChannelResponse,
    ChannelWithdrawalRequest,
    PaymentChannelAdapter,
)
from payday.core.config import Settings, settings
from payday.core.exceptions import PayDayException
from payday.core.logging import logger
from payday.core.money import to_wire_amount_string
from payday.core.msisdn import format_for_operator, operator_for

PROVIDER = "MTN"

#: MTN returns 202 Accepted when a request is queued for customer approval.
ACCEPTED = 202


@dataclass(frozen=True)
class MTNApiCredentials:
    """Credentials for one MTN product. Collections and disbursements differ."""

    api_user: str = ""
    api_key: str = ""
    subscription_key: str = ""

    @property
    def complete(self) -> bool:
        return bool(self.api_user and self.api_key and self.subscription_key)


@dataclass(frozen=True)
class MTNConfig:
    """Everything the adapter needs, resolved from settings once."""

    base_url: str = ""
    target_environment: str = ""
    collection: MTNApiCredentials = MTNApiCredentials()
    disbursement: MTNApiCredentials = MTNApiCredentials()
    callback_url: str = ""
    timeout_seconds: float = 15.0
    token_refresh_skew_seconds: int = 300
    strict_operator_prefix: bool = False

    @classmethod
    def from_settings(cls, s: Optional[Settings] = None) -> "MTNConfig":
        s = s or settings
        return cls(
            base_url=s.mtn_base_url,
            target_environment=s.mtn_target_environment,
            collection=MTNApiCredentials(
                api_user=s.MTN_COLLECTION_API_USER,
                api_key=s.MTN_COLLECTION_API_KEY,
                subscription_key=s.MTN_COLLECTION_SUBSCRIPTION_KEY,
            ),
            disbursement=MTNApiCredentials(
                api_user=s.MTN_DISBURSEMENT_API_USER,
                api_key=s.MTN_DISBURSEMENT_API_KEY,
                subscription_key=s.MTN_DISBURSEMENT_SUBSCRIPTION_KEY,
            ),
            callback_url=s.mtn_callback_url(),
            timeout_seconds=s.TELCO_HTTP_TIMEOUT_SECONDS,
            token_refresh_skew_seconds=s.TELCO_TOKEN_REFRESH_SKEW_SECONDS,
            strict_operator_prefix=s.TELCO_STRICT_OPERATOR_PREFIX,
        )


# --------------------------------------------------------------------------- #
# Pure helpers — asserted directly by the golden-payload tests
# --------------------------------------------------------------------------- #
def build_requesttopay_payload(
    *,
    amount: Any,
    currency: str,
    transaction_id: str,
    phone_number: str,
    description: str,
    payer_message: Optional[str] = None,
) -> Dict[str, Any]:
    """The exact body of `POST /collection/v1_0/requesttopay`.

    `amount` is a **string of whole francs**: MTN documents a string, and XAF has
    no minor unit, so `"1000"` is the only correct rendering of 1000 XAF.
    """
    return {
        "amount": to_wire_amount_string(amount),
        "currency": currency,
        "externalId": transaction_id,
        "payer": {
            "partyIdType": "MSISDN",
            "partyId": format_for_operator(phone_number, PROVIDER),
        },
        "payerMessage": payer_message or "PayDay Deposit",
        "payeeNote": description,
    }


def build_transfer_payload(
    *,
    amount: Any,
    currency: str,
    transaction_id: str,
    destination_phone: str,
    description: str,
    payee_note: Optional[str] = None,
) -> Dict[str, Any]:
    """The exact body of `POST /disbursement/v1_0/transfer`."""
    return {
        "amount": to_wire_amount_string(amount),
        "currency": currency,
        "externalId": transaction_id,
        "payee": {
            "partyIdType": "MSISDN",
            "partyId": format_for_operator(destination_phone, PROVIDER),
        },
        "payerMessage": payee_note or "PayDay Withdrawal",
        "payeeNote": description,
    }


def map_status(payload: Dict[str, Any]) -> str:
    """Map an MTN status value onto our four-state vocabulary."""
    status = str(payload.get("status") or "").upper()
    if status == "SUCCESSFUL":
        return "SUCCESS"
    if status in {"FAILED", "REJECTED", "EXPIRED", "TIMEOUT"}:
        return "FAILED"
    return "PROCESSING"


def warn_if_operator_mismatch(phone_number: str, strict: bool) -> None:
    """Warn (or, when strict, reject) a number belonging to another operator.

    Prefix tables are advisory: Cameroon has number portability and published
    tables disagree. A hard rejection by default would fail legitimate ported
    numbers, so this is a warning unless `TELCO_STRICT_OPERATOR_PREFIX` is set.
    """
    actual = operator_for(phone_number)
    if actual is None or actual == PROVIDER:
        return
    message = (
        f"{PROVIDER} request carries a number whose prefix is allocated to "
        f"{actual} ({phone_number}). Mobile money may still succeed if the number "
        "was ported."
    )
    if strict:
        raise PayDayException(
            status_code=400,
            detail=(
                f"This number appears to belong to {actual}, not {PROVIDER}. "
                f"Choose the {actual} channel, or disable TELCO_STRICT_OPERATOR_PREFIX."
            ),
            code="PHONE_OPERATOR_MISMATCH",
            title="Operator Mismatch",
        )
    logger.warning(f"[MTN MoMo] {message}")


class MTNMoMoAdapter(PaymentChannelAdapter):
    """MTN Mobile Money (Cameroon) — Collections (RequestToPay) & Disbursements."""

    def __init__(
        self,
        config: Optional[MTNConfig] = None,
        use_mock: Optional[bool] = None,
        clock=time.monotonic,
    ):
        self.config = config if config is not None else MTNConfig.from_settings()
        self.use_mock = (
            use_mock if use_mock is not None else settings.TELCO_MODE == "mock"
        )
        self._clock = clock
        #: product -> (token, monotonic expiry)
        self._tokens: Dict[str, tuple[str, float]] = {}
        # Backwards-compatible aliases for the old constructor signature.
        self.base_url = self.config.base_url
        self.target_env = self.config.target_environment
        self.subscription_key = self.config.collection.subscription_key

    # ------------------------------------------------------------------ #
    # Credentials & tokens
    # ------------------------------------------------------------------ #
    def _credentials(self, product: str) -> MTNApiCredentials:
        return (
            self.config.collection
            if product == "collection"
            else self.config.disbursement
        )

    async def _get_auth_token(self, product: str = "collection") -> str:
        """Return a valid OAuth2 token, refreshing before it expires.

        MTN tokens are valid for about an hour. The previous implementation
        cached the first token for the lifetime of the process, so every call
        after the first hour failed in production and nowhere else.
        """
        if self.use_mock:
            return f"mock-{PROVIDER.lower()}-oauth2-token-valid"

        cached = self._tokens.get(product)
        if cached and self._clock() < cached[1]:
            return cached[0]

        credentials = self._credentials(product)
        if not credentials.complete:
            raise PayDayException(
                status_code=503,
                detail=(
                    f"{PROVIDER} {product} credentials are not configured "
                    "(api user, api key and subscription key are all required)."
                ),
                code="TELCO_NOT_CONFIGURED",
                title="Payment Channel Unavailable",
            )

        headers = {
            "Ocp-Apim-Subscription-Key": credentials.subscription_key,
        }
        if self.config.target_environment:
            headers["X-Target-Environment"] = self.config.target_environment

        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            response = await client.post(
                f"{self.config.base_url}/{product}/token/",
                headers=headers,
                auth=(credentials.api_user, credentials.api_key),
            )
        if response.status_code != 200:
            raise PayDayException(
                status_code=502,
                detail=f"{PROVIDER} authentication failed ({response.status_code}).",
                code="TELCO_AUTH_FAILED",
                title="Payment Channel Error",
            )

        data = response.json()
        token = data.get("access_token")
        if not token:
            raise PayDayException(
                status_code=502,
                detail=f"{PROVIDER} authentication response contained no access_token.",
                code="TELCO_AUTH_FAILED",
                title="Payment Channel Error",
            )
        expires_in = int(data.get("expires_in") or 3600)
        expiry = self._clock() + max(
            1, expires_in - self.config.token_refresh_skew_seconds
        )
        self._tokens[product] = (token, expiry)
        return token

    async def _authorized_post(
        self, url: str, product: str, payload: Dict[str, Any], extra_headers: Dict[str, str]
    ) -> httpx.Response:
        """POST with the product's token, retrying once if the token was stale."""
        credentials = self._credentials(product)
        for attempt in (1, 2):
            token = await self._get_auth_token(product)
            headers = {
                "Authorization": f"Bearer {token}",
                "Ocp-Apim-Subscription-Key": credentials.subscription_key,
                "X-Target-Environment": self.config.target_environment,
                "Content-Type": "application/json",
                **extra_headers,
            }
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
            if response.status_code != 401 or attempt == 2:
                return response
            logger.warning(
                f"[MTN MoMo] 401 from {url}; discarding cached token and retrying once."
            )
            self._tokens.pop(product, None)
        raise AssertionError("unreachable")  # pragma: no cover

    # ------------------------------------------------------------------ #
    # Money movement
    # ------------------------------------------------------------------ #
    async def initiate_deposit(self, req: ChannelDepositRequest) -> ChannelResponse:
        """RequestToPay: the customer receives a USSD/app approval prompt."""
        reference_id = str(uuid.uuid4())
        warn_if_operator_mismatch(req.phone_number, self.config.strict_operator_prefix)

        logger.info(
            f"[MTN MoMo] Initiating Collection: {req.amount} XAF from "
            f"{req.phone_number} (Ref: {reference_id})"
        )

        if self.use_mock:
            channel_ref = f"MTN-MOMO-{reference_id[:8].upper()}"
            return ChannelResponse(
                success=True,
                channel_ref=channel_ref,
                status="PROCESSING",
                message="USSD prompt pushed to customer handset for PIN approval",
                raw_response={
                    "referenceId": reference_id,
                    "channel_ref": channel_ref,
                    "provider": "MTN",
                    "status": "PENDING_CUSTOMER_APPROVAL",
                },
            )

        payload = build_requesttopay_payload(
            amount=req.amount,
            currency=req.currency,
            transaction_id=req.transaction_id,
            phone_number=req.phone_number,
            description=req.description,
            payer_message=req.payer_message,
        )
        extra_headers = {"X-Reference-Id": reference_id}
        if self.config.callback_url:
            extra_headers["X-Callback-Url"] = self.config.callback_url

        try:
            response = await self._authorized_post(
                f"{self.config.base_url}/collection/v1_0/requesttopay",
                "collection",
                payload,
                extra_headers,
            )
        except PayDayException:
            raise
        except Exception as exc:
            logger.error(f"[MTN MoMo] Network error initiating collection: {exc}")
            return ChannelResponse(
                success=False,
                status="FAILED",
                message=f"Network error communicating with {PROVIDER}: {exc}",
                error_code="MTN_CONNECTION_ERROR",
            )

        if response.status_code == ACCEPTED:
            return ChannelResponse(
                success=True,
                channel_ref=reference_id,
                status="PROCESSING",
                message="Request accepted by MTN MoMo gateway",
                raw_response={"status_code": ACCEPTED, "reference_id": reference_id},
            )
        return ChannelResponse(
            success=False,
            status="FAILED",
            message=f"MTN API rejected request: {response.text}",
            raw_response={"status_code": response.status_code, "body": response.text},
            error_code="MTN_REJECTED",
        )

    async def initiate_withdrawal(self, req: ChannelWithdrawalRequest) -> ChannelResponse:
        """Transfer: credit the recipient's MTN Mobile Money account."""
        reference_id = str(uuid.uuid4())
        warn_if_operator_mismatch(
            req.destination_phone, self.config.strict_operator_prefix
        )

        logger.info(
            f"[MTN MoMo] Initiating Disbursement: {req.amount} XAF to "
            f"{req.destination_phone} (Ref: {reference_id})"
        )

        if self.use_mock:
            channel_ref = f"MTN-DISB-{reference_id[:8].upper()}"
            return ChannelResponse(
                success=True,
                channel_ref=channel_ref,
                status="PROCESSING",
                message="Disbursement queued by MTN MoMo network",
                raw_response={
                    "referenceId": reference_id,
                    "channel_ref": channel_ref,
                    "provider": "MTN",
                    "status": "PROCESSING",
                },
            )

        payload = build_transfer_payload(
            amount=req.amount,
            currency=req.currency,
            transaction_id=req.transaction_id,
            destination_phone=req.destination_phone,
            description=req.description,
            payee_note=req.payee_note,
        )
        extra_headers = {"X-Reference-Id": reference_id}
        if self.config.callback_url:
            extra_headers["X-Callback-Url"] = self.config.callback_url

        try:
            response = await self._authorized_post(
                f"{self.config.base_url}/disbursement/v1_0/transfer",
                "disbursement",
                payload,
                extra_headers,
            )
        except PayDayException:
            raise
        except Exception as exc:
            logger.error(f"[MTN MoMo] Network error initiating disbursement: {exc}")
            return ChannelResponse(
                success=False,
                status="FAILED",
                message=f"Network error communicating with {PROVIDER}: {exc}",
                error_code="MTN_CONNECTION_ERROR",
            )

        if response.status_code == ACCEPTED:
            return ChannelResponse(
                success=True,
                channel_ref=reference_id,
                status="PROCESSING",
                message="Disbursement accepted by MTN gateway",
                raw_response={"status_code": ACCEPTED, "reference_id": reference_id},
            )
        return ChannelResponse(
            success=False,
            status="FAILED",
            message=f"MTN Disbursement rejected: {response.text}",
            raw_response={"status_code": response.status_code, "body": response.text},
            error_code="MTN_DISBURSEMENT_REJECTED",
        )

    # ------------------------------------------------------------------ #
    # Status & callbacks
    # ------------------------------------------------------------------ #
    async def query_status(self, channel_ref: str, tx_type: str = "DEPOSIT") -> ChannelResponse:
        """Authoritative status lookup — the source of truth for reconciliation."""
        if self.use_mock:
            return ChannelResponse(
                success=True,
                channel_ref=channel_ref,
                status="SUCCESS",
                message="Mock transaction confirmed successful",
            )

        product = "collection" if tx_type == "DEPOSIT" else "disbursement"
        endpoint = (
            "collection/v1_0/requesttopay"
            if tx_type == "DEPOSIT"
            else "disbursement/v1_0/transfer"
        )
        credentials = self._credentials(product)
        token = await self._get_auth_token(product)
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Target-Environment": self.config.target_environment,
            "Ocp-Apim-Subscription-Key": credentials.subscription_key,
        }

        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            response = await client.get(
                f"{self.config.base_url}/{endpoint}/{channel_ref}", headers=headers
            )
        if response.status_code == 200:
            data = response.json()
            status = map_status(data)
            return ChannelResponse(
                success=(status == "SUCCESS"),
                channel_ref=channel_ref,
                status=status,
                raw_response=data,
            )
        return ChannelResponse(
            success=False,
            channel_ref=channel_ref,
            status="FAILED",
            message=f"Status query failed with code {response.status_code}",
        )

    async def verify_webhook_signature(self, headers: Dict[str, str], body: bytes) -> bool:
        """Whether an inbound callback may be trusted.

        MTN does **not** sign its callbacks, so there is no signature to verify.
        The correct control — recommended by MTN integrators and implemented in
        A8 — is to treat the callback as a *hint* and re-query the status
        endpoint before moving the ledger. Until that exists, live callbacks are
        **rejected rather than trusted**: an unauthenticated POST must never be
        able to credit a wallet. `validate_telco_configuration` additionally
        refuses to start in live mode until A8 lands, so this cannot be reached
        with real money.

        In mock mode the previous keyword behaviour is preserved, because the
        suite uses it to exercise the endpoint's rejection path.
        """
        normalized = {k.lower(): v for k, v in headers.items()}
        signature = (
            normalized.get("x-signature")
            or normalized.get("ocp-apim-subscription-key")
            or normalized.get("authorization")
        )

        if self.use_mock:
            if signature and any(
                token in signature.lower()
                for token in ("invalid", "spoofed", "bad", "forged")
            ):
                return False
            return True

        logger.error(
            "[MTN MoMo] Callback received but authoritative status verification "
            "(M1 task A8) is not implemented; rejecting to avoid crediting a "
            "wallet from an unauthenticated request."
        )
        return False


def build_mtn_adapter(use_mock: Optional[bool] = None) -> MTNMoMoAdapter:
    """Construct the adapter from current settings."""
    return MTNMoMoAdapter(config=MTNConfig.from_settings(), use_mock=use_mock)


mtn_momo_adapter = build_mtn_adapter()
