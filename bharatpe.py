"""
BharatPe Transaction API — fetch and match payments.

All public functions accept a PaymentConfig (or any object with the fields:
merchant_id, api_token, api_cookie, bharatpe_api, user_agent) so that multiple
merchants / bots can share one process without credential collisions.
"""

import logging
import requests
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger(__name__)

# Live credentials keyed by merchant_id so multiple configs can coexist.
# Initialised lazily from cfg on first call; updated by update_credentials().
_live: dict[str, dict] = {}


class CredentialsExpiredError(Exception):
    """Raised when BharatPe API rejects the token/cookie (session expired)."""


def _get_live(cfg) -> dict:
    mid = cfg.merchant_id
    if mid not in _live:
        _live[mid] = {"token": cfg.api_token, "cookie": cfg.api_cookie}
    return _live[mid]


def update_credentials(token: str, cookie: str, cfg) -> None:
    """Replace the active BharatPe credentials at runtime (no restart needed)."""
    slot = _get_live(cfg)
    slot["token"] = token.strip()
    slot["cookie"] = cookie.strip()
    logger.info(f"BharatPe credentials updated for merchant {cfg.merchant_id}")


def _build_headers(cfg) -> dict:
    slot = _get_live(cfg)
    return {
        "token": slot["token"],
        "Cookie": slot["cookie"],
        "User-Agent": cfg.user_agent,
    }


def _parse_response(resp: requests.Response) -> list:
    """Parse a BharatPe API response, raising CredentialsExpiredError if rejected."""
    data = resp.json()

    if data.get("status") and data.get("message") == "SUCCESS":
        txns = data.get("data", {}).get("transactions", [])
        logger.debug(f"Fetched {len(txns)} transactions from BharatPe")
        return txns

    msg = data.get("message", "UNKNOWN")
    logger.warning(f"BharatPe API error: {msg}")

    if msg in ("UNAUTHORIZED", "UNAUTHENTICATED", "TOKEN_EXPIRED", "SESSION_EXPIRED") \
            or resp.status_code in (401, 403):
        raise CredentialsExpiredError(msg)

    return []


def fetch_transactions(cfg) -> list:
    """Fetch recent QR payment transactions from BharatPe.

    Raises:
        CredentialsExpiredError: if the API returns a non-SUCCESS status that
            indicates the token/cookie has expired or been rejected.
        requests.RequestException: on network-level failures.
    """
    now_ist = datetime.now(IST)
    from_date = (now_ist - timedelta(days=2)).strftime("%Y-%m-%d")
    to_date = (now_ist + timedelta(days=1)).strftime("%Y-%m-%d")

    resp = requests.get(
        cfg.bharatpe_api,
        params={
            "module": "PAYMENT_QR",
            "merchantId": cfg.merchant_id,
            "sDate": from_date,
            "eDate": to_date,
        },
        headers=_build_headers(cfg),
        timeout=15,
    )
    return _parse_response(resp)


def fetch_transactions_with(token: str, cookie: str, cfg) -> list:
    """Like fetch_transactions() but uses explicit credentials instead of the
    current in-memory ones.  Used to validate new credentials before committing.

    Raises:
        CredentialsExpiredError: credentials are invalid.
        requests.RequestException: on network-level failures.
    """
    now_ist = datetime.now(IST)
    from_date = (now_ist - timedelta(days=2)).strftime("%Y-%m-%d")
    to_date = (now_ist + timedelta(days=1)).strftime("%Y-%m-%d")

    headers = {
        "token": token,
        "Cookie": cookie,
        "User-Agent": cfg.user_agent,
    }
    resp = requests.get(
        cfg.bharatpe_api,
        params={
            "module": "PAYMENT_QR",
            "merchantId": cfg.merchant_id,
            "sDate": from_date,
            "eDate": to_date,
        },
        headers=headers,
        timeout=15,
    )
    return _parse_response(resp)


def find_payment(amount: float, created_at: datetime, expire_at: datetime, cfg) -> dict | None:
    """
    Find a matching BharatPe transaction by:
    - amount ± ₹0.001
    - type = PAYMENT_RECV, status = SUCCESS
    - timestamp within [created_at, expire_at] window

    Returns dict with amount, utr, timestamp, vpa, payer_name, payer_handle or None.

    Raises:
        CredentialsExpiredError: propagated from fetch_transactions().
    """
    for tx in fetch_transactions(cfg):
        if tx.get("type") != "PAYMENT_RECV" or tx.get("status") != "SUCCESS":
            continue

        tx_amount = float(tx.get("amount", 0))
        tx_ms = int(tx.get("paymentTimestamp", 0))
        tx_time = datetime.fromtimestamp(tx_ms / 1000)

        if abs(tx_amount - amount) < 0.001 and created_at <= tx_time <= expire_at:
            return {
                "amount": tx_amount,
                "utr": tx.get("bankReferenceNo", ""),
                "timestamp": tx_time.strftime("%Y-%m-%d %H:%M:%S"),
                "vpa": tx.get("payerVpa", ""),
                "payer_name": tx.get("payerName", ""),
                "payer_handle": tx.get("payerHandle", ""),
            }

    return None


def check_credentials(cfg) -> str:
    """
    Verify the BharatPe session is currently valid by making a lightweight
    API call.

    Returns:
        "ok"      — credentials are valid and the API is reachable
        "expired" — credentials are explicitly rejected by the API
        "unknown" — network or parsing error; credentials status is unclear
    """
    try:
        fetch_transactions(cfg)
        return "ok"
    except CredentialsExpiredError:
        return "expired"
    except Exception as e:
        logger.warning(f"check_credentials: could not reach BharatPe API: {e}")
        return "unknown"


def check_credentials_with(token: str, cookie: str, cfg) -> str:
    """
    Validate a new token/cookie pair against the BharatPe API without
    touching the current in-memory credentials.

    Returns:
        "ok"      — new credentials are valid
        "expired" — new credentials were explicitly rejected by the API
        "unknown" — network or parsing error; status is unclear
    """
    try:
        fetch_transactions_with(token, cookie, cfg)
        return "ok"
    except CredentialsExpiredError:
        return "expired"
    except Exception as e:
        logger.warning(f"check_credentials_with: could not reach BharatPe API: {e}")
        return "unknown"
