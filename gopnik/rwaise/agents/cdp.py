"""Coinbase Developer Platform (CDP) market-data adapter.

Endpoints
=========

Public (no API key required):

  * ``GET https://api.coinbase.com/api/v3/brokerage/market/products/{id}/ticker``
       latest tick (price, 24h volume, best bid/ask)
  * ``GET https://api.exchange.coinbase.com/products/{id}/candles``
       OHLCV candles. ``granularity`` in seconds:
       60, 300, 900, 3600, 21600, 86400. Returns a list of
       ``[timestamp, low, high, open, close, volume]`` tuples,
       newest-first. Max 300 candles per call.

Authenticated (CDP JWT, ES256, EC P-256 key):

  * Anything under ``/api/v3/brokerage/`` that's not the public
    /market/ subtree. Required for placing trades, querying
    accounts, etc. Optional for market data — purely about
    higher rate limits.

The agent only needs public endpoints for the demo, so no key
is required out of the box. ``CDP_API_KEY_NAME`` and
``CDP_API_KEY_PRIVATE_KEY`` enable the authenticated path when
set.

Rate limits
===========
~ 10 RPS for public, much higher for authenticated. We cache
spot prices for 30 s in memory to be a good citizen.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Any, Optional

import requests

log = logging.getLogger(__name__)


_BROKERAGE_BASE = "https://api.coinbase.com/api/v3/brokerage"
_EXCHANGE_BASE  = "https://api.exchange.coinbase.com"
_TIMEOUT_S      = 6.0
_HTTP_HEADERS   = {"User-Agent": "RWAiSE/1.0 (+https://wallet.gopnik.io)"}


# ─── Symbol → Coinbase product id ────────────────────────────────────


_SYMBOL_TO_CDP_ID: dict[str, str] = {
    "BTC":   "BTC-USD",
    "ETH":   "ETH-USD",
    "XRP":   "XRP-USD",
    "SOL":   "SOL-USD",
    "USDC":  "USDC-USD",
    "ADA":   "ADA-USD",
    "DOT":   "DOT-USD",
    "LINK":  "LINK-USD",
    "DOGE":  "DOGE-USD",
    "AVAX":  "AVAX-USD",
    "MATIC": "MATIC-USD",
    "ATOM":  "ATOM-USD",
    "NEAR":  "NEAR-USD",
    # Coinbase doesn't list these as of 2026 — caller should fall through:
    # "RLUSD": (not listed)
    # "USDT":  (not listed on Advanced)
    # "GOPNIK":(not listed)
}


def cdp_product_id(symbol: str) -> Optional[str]:
    return _SYMBOL_TO_CDP_ID.get(symbol.upper())


# ─── Tiny TTL cache (in-process, per-worker) ────────────────────────


class _Cache:
    """Single-key TTL cache; one instance per ``(endpoint, kwargs)``."""
    def __init__(self, ttl_s: float):
        self._ttl = ttl_s
        self._stamp = 0.0
        self._value: Any = None

    def get(self) -> Any:
        if (time.time() - self._stamp) <= self._ttl:
            return self._value
        return None

    def put(self, v: Any) -> None:
        self._value = v
        self._stamp = time.time()


_spot_cache: dict[str, _Cache] = {}


# ─── Public endpoints ────────────────────────────────────────────────


def cdp_spot_price(symbol: str) -> Optional[float]:
    """Latest USD spot price from Coinbase Advanced Trade.

    Cached for 30 seconds per symbol.
    """
    pid = cdp_product_id(symbol)
    if pid is None:
        return None

    c = _spot_cache.setdefault(pid, _Cache(ttl_s=30.0))
    cached = c.get()
    if cached is not None:
        return cached

    try:
        url = f"{_BROKERAGE_BASE}/market/products/{pid}/ticker"
        r = requests.get(url, headers=_HTTP_HEADERS, timeout=_TIMEOUT_S)
        r.raise_for_status()
        body = r.json()
        # Two response shapes seen in the wild:
        # 1) {"trades": [{"price": "...", ...}], "best_bid": "...", ...}
        # 2) {"price": "...", ...}     (fallback for some products)
        price_str = None
        trades = body.get("trades") or []
        if trades and isinstance(trades, list):
            price_str = trades[0].get("price")
        if price_str is None:
            price_str = body.get("price")
        if price_str is None:
            # Last resort: midpoint of bid/ask
            bid = body.get("best_bid")
            ask = body.get("best_ask")
            if bid and ask:
                price_str = (float(bid) + float(ask)) / 2
        if price_str is None:
            return None
        px = float(price_str)
        c.put(px)
        return px
    except Exception as e:                                            # pragma: no cover
        log.info("CDP spot %s failed: %s", symbol, e)
        return None


def cdp_candles(symbol: str, days: int = 7) -> list[tuple[date, float]]:
    """Daily-close candles via the public Exchange API.

    Returns ``[(day, close_usd), ...]`` ascending by date.
    """
    pid = cdp_product_id(symbol)
    if pid is None:
        return []
    days = max(1, min(int(days), 90))
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    try:
        url = f"{_EXCHANGE_BASE}/products/{pid}/candles"
        r = requests.get(
            url,
            params={
                "start": start.isoformat() + "Z",
                "end":   end.isoformat() + "Z",
                "granularity": 86400,           # 1 day
            },
            headers=_HTTP_HEADERS,
            timeout=_TIMEOUT_S,
        )
        r.raise_for_status()
        rows = r.json()  # [[ts, low, high, open, close, volume], ...]
        out: list[tuple[date, float]] = []
        for row in rows:
            ts = int(row[0])
            close = float(row[4])
            d = datetime.utcfromtimestamp(ts).date()
            out.append((d, close))
        # Coinbase returns newest-first; we want ascending and de-duped.
        seen: dict[date, float] = {}
        for d, c in out:
            seen[d] = c
        return sorted(seen.items())
    except Exception as e:                                            # pragma: no cover
        log.info("CDP candles %s failed: %s", symbol, e)
        return []


# ─── Optional authenticated path (CDP JWT) ──────────────────────────


def _have_cdp_jwt_key() -> bool:
    """JWT path — new CDP key format (portal.cdp.coinbase.com).

    ``CDP_API_KEY_NAME``      = ``organizations/<org>/apiKeys/<key>``
    ``CDP_API_KEY_PRIVATE_KEY`` = PEM-encoded EC P-256 private key
    """
    return bool(os.getenv("CDP_API_KEY_NAME") and os.getenv("CDP_API_KEY_PRIVATE_KEY"))


def _have_cdp_hmac_key() -> bool:
    """HMAC path — legacy Coinbase Cloud API key.

    ``COINBASE_API_KEY``    = the API key id
    ``COINBASE_API_SECRET`` = the base64 HMAC secret
    """
    return bool(os.getenv("COINBASE_API_KEY") and os.getenv("COINBASE_API_SECRET"))


def _have_cdp_key() -> bool:
    return _have_cdp_jwt_key() or _have_cdp_hmac_key()


def cdp_jwt(uri: str) -> Optional[str]:                               # pragma: no cover
    """Build a one-shot JWT for an authenticated CDP request.

    ``uri`` is the full request line, e.g.
    ``"GET api.coinbase.com/api/v3/brokerage/accounts"``.
    Returns ``None`` when no CDP JWT key is configured or the
    cryptography / PyJWT libraries aren't installed.
    """
    if not _have_cdp_jwt_key():
        return None
    try:
        import secrets, jwt as _jwt
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        log.warning("CDP JWT path needs PyJWT + cryptography — skipping auth")
        return None

    name = os.environ["CDP_API_KEY_NAME"]
    pem  = os.environ["CDP_API_KEY_PRIVATE_KEY"].replace("\\n", "\n")
    key  = serialization.load_pem_private_key(pem.encode(), password=None)
    now  = int(time.time())
    payload = {
        "sub": name,
        "iss": "cdp",
        "nbf": now,
        "exp": now + 120,
        "uri": uri,
    }
    return _jwt.encode(payload, key, algorithm="ES256",
                        headers={"kid": name, "nonce": secrets.token_hex(16)})


def _cdp_hmac_headers(method: str, path: str, body: str = "") -> dict:    # pragma: no cover
    """Legacy HMAC headers (CB-ACCESS-*).

    Coinbase has two legacy HMAC schemes; we pick by whether
    ``COINBASE_API_PASSPHRASE`` is set:

    1. **Cloud API / Advanced Trade legacy** (no passphrase):
       - secret used as raw UTF-8 bytes
       - signature is **hex**-encoded
       - no ``CB-ACCESS-PASSPHRASE`` header
    2. **Coinbase Pro / Exchange** (passphrase required):
       - secret is **base64-decoded** before use
       - signature is **base64**-encoded
       - includes the ``CB-ACCESS-PASSPHRASE`` header

    Set ``COINBASE_API_KEY`` + ``COINBASE_API_SECRET`` for #1, plus
    ``COINBASE_API_PASSPHRASE`` for #2.
    """
    import hmac, hashlib, base64
    api_key       = os.environ["COINBASE_API_KEY"]
    api_secret    = os.environ["COINBASE_API_SECRET"]
    passphrase    = os.getenv("COINBASE_API_PASSPHRASE", "")
    timestamp     = str(int(time.time()))
    message       = timestamp + method.upper() + path + body

    if passphrase:
        # Pro / Exchange scheme: base64-decode the secret, base64 sign.
        key_bytes = base64.b64decode(api_secret)
        sig_bytes = hmac.new(key_bytes, message.encode(), hashlib.sha256).digest()
        sig       = base64.b64encode(sig_bytes).decode()
        return {
            "CB-ACCESS-KEY":        api_key,
            "CB-ACCESS-SIGN":       sig,
            "CB-ACCESS-TIMESTAMP":  timestamp,
            "CB-ACCESS-PASSPHRASE": passphrase,
            "Content-Type":         "application/json",
        }

    # Cloud API / Advanced Trade legacy: raw secret bytes, hex sign.
    sig = hmac.new(api_secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return {
        "CB-ACCESS-KEY":       api_key,
        "CB-ACCESS-SIGN":      sig,
        "CB-ACCESS-TIMESTAMP": timestamp,
        "Content-Type":        "application/json",
    }


def cdp_authenticated_get(path: str) -> Optional[dict]:               # pragma: no cover
    """GET an authenticated CDP endpoint.

    ``path`` starts with ``/api/v3/brokerage/...``. Tries the JWT
    path first (modern CDP key); falls back to HMAC (legacy Cloud
    API key) if that's what the operator configured.
    Returns parsed JSON body, or ``None`` if no key configured /
    request failed.
    """
    host = "api.coinbase.com"
    url  = f"https://{host}{path}"

    if _have_cdp_jwt_key():
        token = cdp_jwt(f"GET {host}{path}")
        if token:
            try:
                r = requests.get(
                    url,
                    headers={**_HTTP_HEADERS,
                              "Authorization": f"Bearer {token}"},
                    timeout=_TIMEOUT_S,
                )
                r.raise_for_status()
                return r.json()
            except Exception as e:
                log.info("CDP JWT GET %s failed: %s", path, e)
                return None

    if _have_cdp_hmac_key():
        try:
            r = requests.get(
                url,
                headers={**_HTTP_HEADERS, **_cdp_hmac_headers("GET", path)},
                timeout=_TIMEOUT_S,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.info("CDP HMAC GET %s failed: %s", path, e)
            return None

    return None


def cdp_supported_symbols() -> list[str]:
    """Return the list of tickers we know we can query on Coinbase."""
    return sorted(_SYMBOL_TO_CDP_ID.keys())
