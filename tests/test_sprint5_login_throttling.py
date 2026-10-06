"""WS-3 / LB-6 — authentication throttling and constant-time login.

Previously `POST /auth/login` was completely unthrottled (unbounded password
guessing against one replica). The suite now pins:

- 5 failed logins per phone / 15 min, then 429 + Retry-After;
- a successful login resets the per-account counter;
- a separate per-IP budget, so one host cannot spray many accounts;
- X-Forwarded-For is only honoured from a trusted proxy (otherwise spoofable);
- the not-found path costs the same bcrypt as a wrong password (no user
  enumeration by timing).
"""

import time
from statistics import median

import pytest
from httpx import AsyncClient

from payday.core.config import settings
from payday.core.ratelimit import get_client_ip
from payday.schemas.auth import LoginRequest
from payday.services.auth_service import auth_service
from payday.models.user import User
from starlette.requests import Request

REGISTER_PAYLOAD = {
    "full_name": "Rate Limit Customer",
    "phone_number": "+237699123321",
    "email": "rate@example.cm",
    "password": "RateLimitPassword2026!",
    "id_document_no": "1122334455",
}


def _bad_login(phone: str, password: str = "WrongPassword!") -> dict:
    return {"phone_number": phone, "password": password}


def _make_request(peer_ip: str, xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/auth/login",
        "raw_path": b"/api/v1/auth/login",
        "query_string": b"",
        "client": (peer_ip, 12345),
        "server": ("test", 80),
        "headers": headers,
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_login_throttled_after_five_failures(client: AsyncClient):
    # Register the victim account once (registration is IP-limited, not per-phone).
    reg = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
    assert reg.status_code == 201

    phone = REGISTER_PAYLOAD["phone_number"]
    for attempt in range(1, 6):
        res = await client.post("/api/v1/auth/login", json=_bad_login(phone))
        assert res.status_code == 401, f"attempt {attempt} should be 401"
        assert res.json()["code"] == "AUTHENTICATION_FAILED"

    # 6th attempt in the window is throttled.
    throttled = await client.post("/api/v1/auth/login", json=_bad_login(phone))
    assert throttled.status_code == 429
    body = throttled.json()
    assert body["code"] == "RATE_LIMITED"
    assert body["status"] == 429
    assert body["title"] == "Rate Limit Exceeded"
    # RFC 7807 envelope is intact.
    for field in ("type", "title", "status", "detail", "instance", "code", "extra"):
        assert field in body
    assert body["extra"]["limit"] == settings.LOGIN_RATE_LIMIT_PHONE
    assert int(throttled.headers["retry-after"]) > 0


@pytest.mark.asyncio
async def test_successful_login_resets_phone_counter(client: AsyncClient):
    reg = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
    assert reg.status_code == 201

    phone = REGISTER_PAYLOAD["phone_number"]
    for _ in range(4):
        res = await client.post("/api/v1/auth/login", json=_bad_login(phone))
        assert res.status_code == 401

    # 5th attempt is successful and resets the per-account counter.
    ok = await client.post(
        "/api/v1/auth/login",
        json={"phone_number": phone, "password": REGISTER_PAYLOAD["password"]},
    )
    assert ok.status_code == 200

    # Counter is back at zero: five fresh failures are all 401 again.
    for _ in range(5):
        res = await client.post("/api/v1/auth/login", json=_bad_login(phone))
        assert res.status_code == 401, "counter was not reset on success"
    throttled = await client.post("/api/v1/auth/login", json=_bad_login(phone))
    assert throttled.status_code == 429


@pytest.mark.asyncio
async def test_login_ip_limit_is_independent_of_account(
    client: AsyncClient, monkeypatch
):
    # Tighten the IP bucket so the test is fast; keep the per-account bucket high.
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_IP", 3)
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_IP_WINDOW", 900)
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_PHONE", 100)

    # Four DIFFERENT (non-existent) accounts from the one source IP.
    statuses = []
    for i in range(4):
        res = await client.post(
            "/api/v1/auth/login",
            json=_bad_login(f"+23767700000{i}"),
        )
        statuses.append(res.status_code)

    assert statuses[:3] == [401, 401, 401]
    assert statuses[3] == 429
    assert (await client.post(
        "/api/v1/auth/login", json=_bad_login("+237677000099")
    )).status_code == 429


@pytest.mark.asyncio
async def test_register_throttled_per_ip(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "REGISTER_RATE_LIMIT_IP", 2)
    monkeypatch.setattr(settings, "REGISTER_RATE_LIMIT_IP_WINDOW", 3600)

    payloads = []
    for i in range(3):
        payload = dict(REGISTER_PAYLOAD)
        payload["phone_number"] = f"+23769912300{i}"
        payload["email"] = f"rate{i}@example.cm"
        payloads.append(payload)

    first = await client.post("/api/v1/auth/register", json=payloads[0])
    assert first.status_code == 201
    second = await client.post("/api/v1/auth/register", json=payloads[1])
    assert second.status_code == 201
    third = await client.post("/api/v1/auth/register", json=payloads[2])
    assert third.status_code == 429
    assert third.json()["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_refresh_throttled_per_ip(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "REFRESH_RATE_LIMIT_IP", 2)
    monkeypatch.setattr(settings, "REFRESH_RATE_LIMIT_IP_WINDOW", 900)

    for _ in range(2):
        res = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": "not.a.real.token"}
        )
        assert res.status_code == 401

    throttled = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "not.a.real.token"}
    )
    assert throttled.status_code == 429
    assert throttled.json()["code"] == "RATE_LIMITED"


def test_x_forwarded_for_only_trusted_from_configured_proxy(monkeypatch):
    # Default: no trusted proxies → the header is ignored, socket peer used.
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", [])
    request = _make_request("1.2.3.4", xff="6.6.6.6")
    assert get_client_ip(request) == "1.2.3.4"

    # Trusted proxy: first entry of the header chain is used.
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", ["1.2.3.4"])
    request = _make_request("1.2.3.4", xff="6.6.6.6, 10.0.0.9")
    assert get_client_ip(request) == "6.6.6.6"

    # Attacker cannot claim it is the proxy.
    request = _make_request("9.9.9.9", xff="6.6.6.6")
    assert get_client_ip(request) == "9.9.9.9"


@pytest.mark.asyncio
async def test_unknown_user_and_wrong_password_take_similar_time(
    db_session, test_user: User
):
    """The not-found path must cost one bcrypt check, like a wrong password.

    Otherwise the response time reveals whether a phone number is registered.
    Median of several runs; the assertion is deliberately an order-of-magnitude
    bound (bcrypt timing jitter is large) plus a floor proving the dummy bcrypt
    actually ran.
    """
    known = LoginRequest(phone_number=test_user.phone_number, password="WrongPassword!")
    unknown = LoginRequest(phone_number="+237699000000", password="WrongPassword!")

    # Warm up bcrypt + DB caches so the first call does not skew the medians.
    for req in (known, unknown):
        with pytest.raises(Exception):
            await auth_service.login_user(db_session, req)

    known_times, unknown_times = [], []
    for _ in range(4):
        start = time.perf_counter()
        with pytest.raises(Exception):
            await auth_service.login_user(db_session, known)
        known_times.append(time.perf_counter() - start)

        start = time.perf_counter()
        with pytest.raises(Exception):
            await auth_service.login_user(db_session, unknown)
        unknown_times.append(time.perf_counter() - start)

    known_median = median(known_times)
    unknown_median = median(unknown_times)

    # The dummy path must actually run bcrypt (not return instantly).
    assert unknown_median > 0.01, (
        f"unknown-phone path returned too fast ({unknown_median:.4f}s) — "
        "the constant-time dummy hash is not in place"
    )

    ratio = max(known_median, 1e-9) / max(unknown_median, 1e-9)
    inverse = max(unknown_median, 1e-9) / max(known_median, 1e-9)
    assert ratio < 10, f"known-user path is much slower: {known_median:.4f}s vs {unknown_median:.4f}s"
    assert inverse < 10, f"unknown-user path is much slower: {unknown_median:.4f}s vs {known_median:.4f}s"
