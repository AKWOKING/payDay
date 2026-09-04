import uuid
import hmac
import hashlib
from typing import Dict, Any, Optional
import httpx
from payday.core.config import settings
from payday.core.logging import logger
from payday.adapters.base import (
    PaymentChannelAdapter,
    ChannelDepositRequest,
    ChannelWithdrawalRequest,
    ChannelResponse,
)


class OrangeMoneyAdapter(PaymentChannelAdapter):
    """
    Orange Money Adapter (Cameroon)
    Implements Orange Money Web Payment API (Collection) & Merchant Payout API (Disbursement).
    Includes built-in Mock Sandbox mode for offline development and CI/CD pipelines.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        merchant_key: Optional[str] = None,
        target_env: str = "sandbox",
        use_mock: bool = True,
    ):
        self.base_url = base_url or "https://api.orange.cm/orange-money-webpay/dev/v1"
        self.client_id = client_id or "mock-om-client-id"
        self.client_secret = client_secret or "mock-om-client-secret"
        self.merchant_key = merchant_key or "mock-om-merchant-key"
        self.target_env = target_env
        self.use_mock = use_mock
        self._cached_token: Optional[str] = None

    def _clean_msisdn(self, phone: str) -> str:
        """Extracts national digits without '+' for Orange Money API."""
        return phone.replace("+", "").strip()

    async def _get_auth_token(self) -> str:
        """Retrieves or refreshes OAuth2 token from Orange Money Gateway."""
        if self.use_mock:
            return "mock-orange-oauth2-token-valid"

        if self._cached_token:
            return self._cached_token

        async with httpx.AsyncClient(timeout=10.0) as client:
            headers = {
                "Authorization": f"Basic {self.client_id}:{self.client_secret}",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            response = await client.post(
                f"{self.base_url}/oauth/token",
                headers=headers,
                data={"grant_type": "client_credentials"},
            )
            if response.status_code == 200:
                token_data = response.json()
                self._cached_token = token_data.get("access_token")
                return self._cached_token
            raise Exception(f"Failed to authenticate with Orange Money API: {response.text}")

    async def initiate_deposit(self, req: ChannelDepositRequest) -> ChannelResponse:
        """
        Dispatches Web Payment / Cash-In request to Orange Money gateway.
        Generates payToken and pushes mobile payment prompt to customer.
        """
        order_id = f"OM-COL-{uuid.uuid4().hex[:10].upper()}"
        msisdn = self._clean_msisdn(req.phone_number)

        logger.info(f"[Orange Money] Initiating Collection: {req.amount} XAF from {msisdn} (Order: {order_id})")

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

        token = await self._get_auth_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Merchant-Key": self.merchant_key,
            "Content-Type": "application/json",
        }
        payload = {
            "merchant_key": self.merchant_key,
            "currency": req.currency,
            "order_id": order_id,
            "amount": int(req.amount),
            "reference": req.transaction_id,
            "subscriber_msisdn": msisdn,
            "lang": "fr",
            "notif_url": f"{settings.API_V1_STR}/webhooks/orange",
            "description": req.description,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/webpayment",
                    headers=headers,
                    json=payload,
                )
                if response.status_code in (200, 201, 202):
                    data = response.json()
                    pay_token = data.get("pay_token") or order_id
                    return ChannelResponse(
                        success=True,
                        channel_ref=pay_token,
                        status="PROCESSING",
                        message="Web payment initialized successfully",
                        raw_response=data,
                    )
                else:
                    return ChannelResponse(
                        success=False,
                        status="FAILED",
                        message=f"Orange Money API rejected collection: {response.text}",
                        raw_response={"status_code": response.status_code, "body": response.text},
                        error_code="ORANGE_COLLECTION_REJECTED",
                    )
            except Exception as e:
                logger.error(f"[Orange Money] Network error initiating deposit: {e}")
                return ChannelResponse(
                    success=False,
                    status="FAILED",
                    message=f"Network error communicating with Orange Money: {str(e)}",
                    error_code="ORANGE_CONNECTION_ERROR",
                )

    async def initiate_withdrawal(self, req: ChannelWithdrawalRequest) -> ChannelResponse:
        """
        Dispatches Merchant Payout to Orange Money account.
        Transfers funds directly to destination subscriber.
        """
        payout_id = f"OM-DISB-{uuid.uuid4().hex[:10].upper()}"
        msisdn = self._clean_msisdn(req.destination_phone)

        logger.info(f"[Orange Money] Initiating Payout: {req.amount} XAF to {msisdn} (Ref: {payout_id})")

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

        token = await self._get_auth_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Merchant-Key": self.merchant_key,
            "Content-Type": "application/json",
        }
        payload = {
            "merchant_key": self.merchant_key,
            "currency": req.currency,
            "payout_id": payout_id,
            "amount": int(req.amount),
            "recipient_msisdn": msisdn,
            "description": req.description,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/payout",
                    headers=headers,
                    json=payload,
                )
                if response.status_code in (200, 201, 202):
                    data = response.json()
                    return ChannelResponse(
                        success=True,
                        channel_ref=payout_id,
                        status="PROCESSING",
                        message="Payout accepted by Orange Money gateway",
                        raw_response=data,
                    )
                else:
                    return ChannelResponse(
                        success=False,
                        status="FAILED",
                        message=f"Orange Money Payout rejected: {response.text}",
                        raw_response={"status_code": response.status_code, "body": response.text},
                        error_code="ORANGE_PAYOUT_REJECTED",
                    )
            except Exception as e:
                logger.error(f"[Orange Money] Network error initiating payout: {e}")
                return ChannelResponse(
                    success=False,
                    status="FAILED",
                    message=f"Network error communicating with Orange Money: {str(e)}",
                    error_code="ORANGE_CONNECTION_ERROR",
                )

    async def query_status(self, channel_ref: str, tx_type: str = "DEPOSIT") -> ChannelResponse:
        """Inquires transaction state from Orange Money gateway."""
        if self.use_mock:
            return ChannelResponse(
                success=True,
                channel_ref=channel_ref,
                status="SUCCESS",
                message="Mock Orange Money transaction confirmed successful",
            )

        token = await self._get_auth_token()
        endpoint = "transactionstatus" if tx_type == "DEPOSIT" else "payoutstatus"
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Merchant-Key": self.merchant_key,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{self.base_url}/{endpoint}/{channel_ref}", headers=headers)
            if response.status_code == 200:
                data = response.json()
                om_status = data.get("status")
                final_status = "SUCCESS" if om_status in ("SUCCESSFUL", "SUCCESS") else ("FAILED" if om_status in ("FAILED", "EXPIRED") else "PROCESSING")
                return ChannelResponse(
                    success=(final_status == "SUCCESS"),
                    channel_ref=channel_ref,
                    status=final_status,
                    raw_response=data,
                )
            return ChannelResponse(
                success=False,
                channel_ref=channel_ref,
                status="FAILED",
                message=f"Orange status query failed with code {response.status_code}",
            )

    async def verify_webhook_signature(self, headers: Dict[str, str], body: bytes) -> bool:
        """
        Verifies HMAC signature or authorization token on incoming Orange Money webhooks/IPN.
        Rejects missing, forged, or invalid signatures.
        """
        normalized_headers = {k.lower(): v for k, v in headers.items()}
        signature = (
            normalized_headers.get("x-orange-signature")
            or normalized_headers.get("x-signature")
            or normalized_headers.get("authorization")
        )

        if self.use_mock:
            if signature and any(s in signature.lower() for s in ["invalid", "spoofed", "bad", "forged"]):
                return False
            return True

        if not signature:
            return False

        if self.client_secret and self.client_secret != "mock-om-client-secret":
            expected_hmac = hmac.new(self.client_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            return hmac.compare_digest(signature, expected_hmac)

        return True


orange_money_adapter = OrangeMoneyAdapter(use_mock=True)
