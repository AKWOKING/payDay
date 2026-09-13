"""Orange Money adapter (Cameroon) — Web Payment (collection) & Payout.

Rewritten in M1. Reconnaissance (`docs/plans/M1_LIVE_MONEY_PATH_PLAN.md` §2)
found four defects that would each have broken every live call:

- the OAuth request went to `{base_url}/oauth/token`, but Orange's token service
  lives on a **different host** (`https://api.orange.com/oauth/v3/token`) than
  the Web Payment API;
- the `Authorization` header was assembled as `Basic {client_id}:{client_secret}`
  — not base64-encoded, therefore not a valid Basic credential. Orange also
  supports (and often issues) a ready-made Authorization value, which is now
  preferred when configured;
- `notif_url` was `f"{settings.API_V1_STR}/webhooks/orange"` — a *relative path*.
  Orange cannot call `/api/v1/webhooks/orange`; the absolute URL now comes from
  `PUBLIC_BASE_URL`;
- amounts were sent as `int(amount)`, **truncating** sub-franc requests
  (`Decimal("1000.99")` → `1000`) while the ledger credited `995.99` net.

Money is whole francs (`core.money`), the amount is sent as the JSON number its
documented examples use, and the MSISDN is the 9-digit national form Orange
Money's merchant documentation describes for `subscriber_msisdn`.

**Unverified contracts** (flagged rather than guessed): Orange's `amount` type
appears both as a number and as a string across published versions, and its
status path differs between API generations — the API is mid-migration. Both are
pinned below by golden tests and must be confirmed against the live sandbox in
task A5. The payout endpoint is likewise unverified pending operator
documentation.
"""
from __future__ import annotations

import base64
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
from payday.core.money import to_wire_amount
from payday.core.msisdn import format_for_operator, operator_for

PROVIDER = "ORANGE"

#: Orange acknowledges a payment initiation with 201 Created (200/202 tolerated).
ACCEPTED = (200, 201, 202)


@dataclass(frozen=True)
class OrangeConfig:
    """Resolved Orange configuration."""

    base_url: str = ""
    token_url: str = "https://api.orange.com/oauth/v3/token"
    auth_header: str = ""
    client_id: str = ""
    client_secret: str = ""
    merchant_key: str = ""
    notification_url: str = ""
    status_path: str = "transactionstatus"
    payout_path: str = "payout"
    timeout_seconds: float = 15.0
    token_refresh_skew_seconds: int = 300
    strict_operator_prefix: bool = False

    @classmethod
    def from_settings(cls, s: Optional[Settings] = None) -> "OrangeConfig":
        s = s or settings
        return cls(
            base_url=s.orange_base_url,
            token_url=s.ORANGE_TOKEN_URL,
            auth_header=s.ORANGE_AUTH_HEADER,
            client_id=s.ORANGE_CLIENT_ID,
            client_secret=s.ORANGE_CLIENT_SECRET,
            merchant_key=s.ORANGE_MERCHANT_KEY,
            notification_url=s.orange_notification_url(),
            status_path=s.ORANGE_STATUS_PATH,
            payout_path=s.ORANGE_PAYOUT_PATH,
            timeout_seconds=s.TELCO_HTTP_TIMEOUT_SECONDS,
            token_refresh_skew_seconds=s.TELCO_TOKEN_REFRESH_SKEW_SECONDS,
            strict_operator_prefix=s.TELCO_STRICT_OPERATOR_PREFIX,
        )

    def basic_auth_header(self) -> str:
        """The Authorization value for the token request.

        Orange may hand over a complete value (typically `Basic <base64>`), in
        which case it is used verbatim. Otherwise the credential pair is
        base64-encoded properly — the previous implementation sent the raw
        `id:secret` pair, which is not valid Basic auth.
        """
        if self.auth_header:
            return self.auth_header
        raw = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")


# --------------------------------------------------------------------------- #
# Pure helpers — asserted directly by the golden-payload tests
# --------------------------------------------------------------------------- #
def build_webpayment_payload(
    *,
    config: OrangeConfig,
    amount: Any,
    currency: str,
    order_id: str,
    reference: str,
) -> Dict[str, Any]:
    """The exact body of `POST {base}/webpayment` (collection).

    Only fields documented across Orange's own merchant documentation are sent;
    `notif_url` is absolute. Orange's documented `amount` type is ambiguous
    (number vs string) — numeric is pinned here and confirmed in sandbox (A5).
    """
    return {
        "merchant_key": config.merchant_key,
        "currency": currency,
        "order_id": order_id,
        "amount": to_wire_amount(amount),
        "reference": reference,
        "notif_url": config.notification_url,
        "lang": "fr",
    }


def build_payout_payload(
    *,
    config: OrangeConfig,
    amount: Any,
    payout_id: str,
    destination_phone: str,
    currency: str,
) -> Dict[str, Any]:
    """Body of the merchant payout (disbursement) request.

    The payout contract is **not verified** against Orange documentation in this
    step — see the module docstring and task A5.
    """
    return {
        "merchant_key": config.merchant_key,
        "currency": currency,
        "payout_id": payout_id,
        "amount": to_wire_amount(amount),
        "recipient_msisdn": format_for_operator(destination_phone, PROVIDER),
    }


def map_status(payload: Dict[str, Any]) -> str:
    """Map an Orange status value onto our four-state vocabulary."""
    status = str(payload.get("status") or "").upper()
    if status in {"SUCCESS", "SUCCESSFUL", "COMPLETED"}:
        return "SUCCESS"
    if status in {"FAILED", "EXPIRED", "REJECTED", "CANCELLED", "CANCELED"}:
        return "FAILED"
    return "PROCESSING"


def warn_if_operator_mismatch(phone_number: str, strict: bool) -> None:
    """See `mtn_momo.warn_if_operator_mismatch` — advisory by default."""
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
                f"This number appears to belong to {actual}, not Orange Money. "
                f"Choose the {actual} channel, or disable TELCO_STRICT_OPERATOR_PREFIX."
            ),
            code="PHONE_OPERATOR_MISMATCH",
            title="Operator Mismatch",
        )
    logger.warning(f"[Orange Money] {message}")


class OrangeMoneyAdapter(PaymentChannelAdapter):
    """Orange Money (Cameroon) — Web Payment & merchant payout."""

    def __init__(
        self,
        config: Optional[OrangeConfig] = None,
        use_mock: Optional[bool] = None,
        clock=time.monotonic,
    ):
        self.config = config if config is not None else OrangeConfig.from_settings()
        self.use_mock = (
            use_mock if use_mock is not None else settings.TELCO_MODE == "mock"
        )
        self._clock = clock
        self._token: Optional[tuple[str, float]] = None
        # Backwards-compatible aliases.
        self.base_url = self.config.base_url
        self.merchant_key = self.config.merchant_key
        self.client_id = self.config.client_id
        self.client_secret = self.config.client_secret

    async def _get_auth_token(self) -> str:
        """OAuth2 client-credentials token, refreshed before it expires."""
        if self.use_mock:
            return f"mock-{PROVIDER.lower()}-oauth2-token-valid"

        if self._token and self._clock() < self._token[1]:
            return self._token[0]

        if not (self.config.auth_header or (self.config.client_id and self.config.client_secret)):
            raise PayDayException(
                status_code=503,
                detail=(
                    "Orange Money credentials are not configured "
                    "(ORANGE_AUTH_HEADER, or ORANGE_CLIENT_ID + ORANGE_CLIENT_SECRET)."
                ),
                code="TELCO_NOT_CONFIGURED",
                title="Payment Channel Unavailable",
            )

        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            response = await client.post(
                self.config.token_url,
                headers={
                    "Authorization": self.config.basic_auth_header(),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"grant_type": "client_credentials"},
            )
        if response.status_code != 200:
            raise PayDayException(
                status_code=502,
                detail=f"Orange Money authentication failed ({response.status_code}).",
                code="TELCO_AUTH_FAILED",
                title="Payment Channel Error",
            )

        data = response.json()
        token = data.get("access_token")
        if not token:
            raise PayDayException(
                status_code=502,
                detail="Orange Money authentication response contained no access_token.",
                code="TELCO_AUTH_FAILED",
                title="Payment Channel Error",
            )
        expires_in = int(data.get("expires_in") or 3600)
        self._token = (
            token,
            self._clock() + max(1, expires_in - self.config.token_refresh_skew_seconds),
        )
        return token

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        """Send an authenticated request, retrying once if the token was stale."""
        for attempt in (1, 2):
            token = await self._get_auth_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "X-Merchant-Key": self.config.merchant_key,
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                response = await client.request(
                    method, url, headers=headers, json=json_body, params=params
                )
            if response.status_code != 401 or attempt == 2:
                return response
            logger.warning(
                f"[Orange Money] 401 from {url}; discarding cached token and retrying once."
            )
            self._token = None
        raise AssertionError("unreachable")  # pragma: no cover

    async def initiate_deposit(self, req: ChannelDepositRequest) -> ChannelResponse:
        """Web Payment: returns a pay_token (and payment_url where provided)."""
        order_id = f"OM-COL-{uuid.uuid4().hex[:10].upper()}"
        warn_if_operator_mismatch(req.phone_number, self.config.strict_operator_prefix)

        logger.info(
            f"[Orange Money] Initiating Collection: {req.amount} XAF from "
            f"{req.phone_number} (Order: {order_id})"
        )

        if self.use_mock:
            channel_ref = f"OM-COL-{order_id[-8:]}"
            return ChannelResponse(
                success=True,
                channel_ref=channel_ref,
                status="PROCESSING",
                message="Orange Money payment prompt pushed to customer mobile",
                raw_response={
                    "order_id": order_id,
                    "channel_ref": channel_ref,
                    "provider": "ORANGE",
                    "pay_token": f"PAY-TOKEN-{uuid.uuid4().hex[:12].upper()}",
                    "payment_url": f"https://mock-orange.cm/pay/{order_id}",
                    "status": "PENDING_CUSTOMER_PIN",
                },
            )

        payload = build_webpayment_payload(
            config=self.config,
            amount=req.amount,
            currency=req.currency,
            order_id=order_id,
            reference=req.transaction_id,
        )

        try:
            response = await self._request(
                "POST", f"{self.config.base_url}/webpayment", json_body=payload
            )
        except PayDayException:
            raise
        except Exception as exc:
            logger.error(f"[Orange Money] Network error initiating deposit: {exc}")
            return ChannelResponse(
                success=False,
                status="FAILED",
                message=f"Network error communicating with {PROVIDER}: {exc}",
                error_code="ORANGE_CONNECTION_ERROR",
            )

        if response.status_code in ACCEPTED:
            data = response.json()
            pay_token = data.get("pay_token") or order_id
            return ChannelResponse(
                success=True,
                channel_ref=pay_token,
                status="PROCESSING",
                message="Web payment initialized successfully",
                raw_response=data,
            )
        return ChannelResponse(
            success=False,
            status="FAILED",
            message=f"Orange Money API rejected collection: {response.text}",
            raw_response={"status_code": response.status_code, "body": response.text},
            error_code="ORANGE_COLLECTION_REJECTED",
        )

    async def initiate_withdrawal(self, req: ChannelWithdrawalRequest) -> ChannelResponse:
        """Merchant payout to an Orange Money subscriber."""
        payout_id = f"OM-DISB-{uuid.uuid4().hex[:10].upper()}"
        warn_if_operator_mismatch(
            req.destination_phone, self.config.strict_operator_prefix
        )

        logger.info(
            f"[Orange Money] Initiating Payout: {req.amount} XAF to "
            f"{req.destination_phone} (Ref: {payout_id})"
        )

        if self.use_mock:
            channel_ref = f"OM-DISB-{payout_id[-8:]}"
            return ChannelResponse(
                success=True,
                channel_ref=channel_ref,
                status="PROCESSING",
                message="Disbursement queued by Orange Money network",
                raw_response={
                    "payout_id": payout_id,
                    "channel_ref": channel_ref,
                    "provider": "ORANGE",
                    "status": "PROCESSING",
                },
            )

        payload = build_payout_payload(
            config=self.config,
            amount=req.amount,
            payout_id=payout_id,
            destination_phone=req.destination_phone,
            currency=req.currency,
        )

        try:
            response = await self._request(
                "POST",
                f"{self.config.base_url}/{self.config.payout_path}",
                json_body=payload,
            )
        except PayDayException:
            raise
        except Exception as exc:
            logger.error(f"[Orange Money] Network error initiating payout: {exc}")
            return ChannelResponse(
                success=False,
                status="FAILED",
                message=f"Network error communicating with {PROVIDER}: {exc}",
                error_code="ORANGE_CONNECTION_ERROR",
            )

        if response.status_code in ACCEPTED:
            return ChannelResponse(
                success=True,
                channel_ref=payout_id,
                status="PROCESSING",
                message="Payout accepted by Orange Money gateway",
                raw_response=response.json() if response.content else {},
            )
        return ChannelResponse(
            success=False,
            status="FAILED",
            message=f"Orange Money Payout rejected: {response.text}",
            raw_response={"status_code": response.status_code, "body": response.text},
            error_code="ORANGE_PAYOUT_REJECTED",
        )

    async def query_status(self, channel_ref: str, tx_type: str = "DEPOSIT") -> ChannelResponse:
        """Authoritative status lookup.

        The path suffix is configurable: Orange's status endpoint differs between
        API generations and the API is mid-migration, so hard-coding one would be
        a guess. Confirmed in sandbox by task A5.
        """
        if self.use_mock:
            return ChannelResponse(
                success=True,
                channel_ref=channel_ref,
                status="SUCCESS",
                message="Mock Orange Money transaction confirmed successful",
            )

        endpoint = (
            self.config.status_path if tx_type == "DEPOSIT" else f"{self.config.payout_path}status"
        )
        response = await self._request(
            "GET", f"{self.config.base_url}/{endpoint}/{channel_ref}"
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
            message=f"Orange status query failed with code {response.status_code}",
        )

    async def verify_webhook_signature(self, headers: Dict[str, str], body: bytes) -> bool:
        """Whether an inbound Orange callback may be trusted.

        Orange's newer API returns a `notif_token` at initiation and echoes it in
        the notification; verifying that is task A8 (it needs per-order token
        storage). Until then, live callbacks are **rejected rather than trusted**
        — an unauthenticated POST must never be able to credit a wallet — and
        `validate_telco_configuration` refuses to start in live mode.

        Mock-mode keyword behaviour is preserved for the existing suite.
        """
        normalized = {k.lower(): v for k, v in headers.items()}
        signature = (
            normalized.get("x-orange-signature")
            or normalized.get("x-signature")
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
            "[Orange Money] Callback received but notif_token verification "
            "(M1 task A8) is not implemented; rejecting to avoid crediting a "
            "wallet from an unauthenticated request."
        )
        return False


def build_orange_adapter(use_mock: Optional[bool] = None) -> OrangeMoneyAdapter:
    """Construct the adapter from current settings."""
    return OrangeMoneyAdapter(config=OrangeConfig.from_settings(), use_mock=use_mock)


orange_money_adapter = build_orange_adapter()
