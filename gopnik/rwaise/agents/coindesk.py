"""Multi-source crypto market-data adapter.

iter-21 — switched primary source to **Coinbase Developer Platform**
(per the user's request). Fallback chain (in order):

  1. **Coinbase CDP** ( ``api.coinbase.com/api/v3/brokerage/...`` and
     ``api.exchange.coinbase.com/...``, public, no API key required;
     authenticated path enabled when ``CDP_API_KEY_NAME`` +
     ``CDP_API_KEY_PRIVATE_KEY`` are set). See ``cdp.py``.
  2. **CoinGecko** ( ``api.coingecko.com``, free, no key, broader
     symbol coverage — used for assets Coinbase doesn't list).
  3. **CoinDesk Data API v3** ( ``data-api.coindesk.com``, optional
     ``COINDESK_API_KEY``).
  4. **CoinDesk BPI** ( ``api.coindesk.com``, BTC-only legacy).
  5. **Mock** — only when ``RWAISE_COINDESK_MOCK=1`` is explicitly
     set or every real source has failed.

The agent therefore returns *real* prices in production with no
configuration. Adding a CDP API key unlocks higher rate limits.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Optional

import requests

log = logging.getLogger(__name__)


_COINGECKO_BASE  = "https://api.coingecko.com/api/v3"
_COINDESK_BPI    = "https://api.coindesk.com/v1/bpi"
_COINDESK_DATA   = "https://data-api.coindesk.com"
_FACILITATOR_DEFAULT = "/api/v1/coindesk-proxy"
_TIMEOUT_S       = 6.0
_HTTP_HEADERS    = {"User-Agent": "RWAiSE/1.0 (+https://wallet.gopnik.io)"}


# ─── Symbol resolution ───────────────────────────────────────────────


# Mapping from ticker → CoinGecko id. Add more here over time.
_SYMBOL_TO_CG_ID: dict[str, str] = {
    "BTC":   "bitcoin",
    "ETH":   "ethereum",
    "XRP":   "ripple",
    "SOL":   "solana",
    "USDC":  "usd-coin",
    "USDT":  "tether",
    "RLUSD": "ripple-usd",
    "ADA":   "cardano",
    "DOT":   "polkadot",
    "LINK":  "chainlink",
    "DOGE":  "dogecoin",
    "AVAX":  "avalanche-2",
    "MATIC": "polygon-pos",
    "ATOM":  "cosmos",
    "NEAR":  "near",
    "GOPNIK": "gopnik",                    # if listed; falls through gracefully if not
}


def _cg_id_for(symbol: str) -> Optional[str]:
    return _SYMBOL_TO_CG_ID.get(symbol.upper())


def _facilitator_url() -> str:
    return os.getenv("RWAISE_X402_FACILITATOR_URL", _FACILITATOR_DEFAULT).rstrip("/")


def _force_mock() -> bool:
    return os.getenv("RWAISE_COINDESK_MOCK", "").strip().lower() in ("1", "true", "yes")


# ─── Result types ────────────────────────────────────────────────────


@dataclass
class SpotPrice:
    symbol: str
    price_usd: float
    fetched_at: datetime
    source: str

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price_usd": self.price_usd,
            "fetched_at": self.fetched_at.isoformat(),
            "source": self.source,
        }


@dataclass
class HistoricalPoint:
    day: date
    close_usd: float

    def to_jsonable(self) -> dict[str, Any]:
        return {"day": self.day.isoformat(), "close_usd": self.close_usd}


@dataclass
class PremiumSignal:
    symbol: str
    direction: str           # "buy", "sell", "hold"
    confidence: float
    rationale: str
    cost_usd_paid: float
    paid_tx_hash: Optional[str]
    source: str

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "direction": self.direction,
            "confidence": self.confidence, "rationale": self.rationale,
            "cost_usd_paid": self.cost_usd_paid,
            "paid_tx_hash": self.paid_tx_hash,
            "source": self.source,
        }


# ─── Public API ──────────────────────────────────────────────────────


def spot_price(symbol: str = "BTC") -> SpotPrice:
    """Return the live USD spot price for ``symbol``.

    Tries, in order: Coinbase CDP → CoinGecko → CoinDesk Data v3 →
    CoinDesk BPI → deterministic mock.
    """
    sym = (symbol or "BTC").upper().strip()

    if _force_mock():
        return _mock_spot_price(sym)

    # 1. Coinbase Developer Platform (PRIMARY since iter-21).
    from . import cdp
    px = cdp.cdp_spot_price(sym)
    if px is not None:
        return SpotPrice(symbol=sym, price_usd=px,
                          fetched_at=datetime.utcnow(),
                          source="coinbase-cdp")

    # 2. CoinGecko (broader symbol coverage — RLUSD, USDT, GOPNIK, ...)
    px = _coingecko_spot(sym)
    if px is not None:
        return SpotPrice(symbol=sym, price_usd=px,
                          fetched_at=datetime.utcnow(),
                          source="coingecko")

    # 3. CoinDesk Data API v3 (requires key)
    px = _coindesk_data_spot(sym)
    if px is not None:
        return SpotPrice(symbol=sym, price_usd=px,
                          fetched_at=datetime.utcnow(),
                          source="coindesk")

    # 4. CoinDesk BPI (BTC only legacy)
    if sym == "BTC":
        px = _coindesk_bpi_spot()
        if px is not None:
            return SpotPrice(symbol=sym, price_usd=px,
                              fetched_at=datetime.utcnow(),
                              source="coindesk-bpi")

    log.warning("spot_price(%s): all real sources failed — falling back to mock", sym)
    return _mock_spot_price(sym)


def historical(symbol: str = "BTC", days: int = 7) -> list[HistoricalPoint]:
    """Daily close for the last ``days`` days.

    Coinbase CDP first; CoinGecko second; CoinDesk BPI for BTC.
    """
    sym = (symbol or "BTC").upper().strip()
    days = max(1, min(int(days), 90))

    if _force_mock():
        return _mock_historical(sym, days)

    # 1. Coinbase CDP (public Exchange candles)
    from . import cdp
    rows = cdp.cdp_candles(sym, days)
    if rows:
        return [HistoricalPoint(day=d, close_usd=c) for d, c in rows]

    # 2. CoinGecko
    pts = _coingecko_history(sym, days)
    if pts:
        return pts

    # 3. CoinDesk BPI (BTC only)
    if sym == "BTC":
        pts = _coindesk_bpi_history(days)
        if pts:
            return pts

    log.warning("historical(%s, %d): all real sources failed — using mock", sym, days)
    return _mock_historical(sym, days)


def premium_signal(symbol: str, *, payer, max_pay_usd: float = 0.10) -> PremiumSignal:
    """Premium buy/sell/hold signal — gated by x402.

    Two paths:

      a) **Real x402**: when ``RWAISE_X402_FACILITATOR_URL`` resolves
         to an external endpoint that actually returns 402, we pay it
         via ``payer`` and parse the response.
      b) **Synthetic** (default): we synthesize a signal from real
         CoinGecko price + 7-day momentum data. Demo-realistic, no
         payment needed. The cost field is 0.

    For the hackathon demo this gives judges *real numbers* (BTC at
    today's spot, signal derived from real momentum) without requiring
    a paid CoinDesk premium subscription.
    """
    sym = (symbol or "BTC").upper().strip()

    if _force_mock():
        return _synthetic_signal(sym, max_pay_usd=0.0, source="mock")

    # Attempt real x402 first if facilitator URL is absolute http(s).
    fac = _facilitator_url()
    if fac.startswith("http"):
        try:
            sig = _x402_signal(sym, payer, max_pay_usd, fac)
            if sig is not None:
                return sig
        except Exception as e:                                        # pragma: no cover
            log.warning("x402 premium_signal failed (%s) — using synthetic", e)

    # Synthesize from real CoinGecko data.
    return _synthetic_signal(sym, max_pay_usd=0.0, source="synthesized")


# ─── CoinGecko backends ──────────────────────────────────────────────


def _coingecko_spot(symbol: str) -> Optional[float]:
    cg_id = _cg_id_for(symbol)
    if cg_id is None:
        return None
    try:
        r = requests.get(f"{_COINGECKO_BASE}/simple/price",
                          params={"ids": cg_id, "vs_currencies": "usd"},
                          headers=_HTTP_HEADERS, timeout=_TIMEOUT_S)
        r.raise_for_status()
        body = r.json()
        usd = body.get(cg_id, {}).get("usd")
        if usd is None:
            return None
        return float(usd)
    except Exception as e:                                            # pragma: no cover
        log.info("CoinGecko spot %s failed: %s", symbol, e)
        return None


def _coingecko_history(symbol: str, days: int) -> list[HistoricalPoint]:
    cg_id = _cg_id_for(symbol)
    if cg_id is None:
        return []
    try:
        r = requests.get(f"{_COINGECKO_BASE}/coins/{cg_id}/market_chart",
                          params={"vs_currency": "usd", "days": days,
                                  "interval": "daily"},
                          headers=_HTTP_HEADERS, timeout=_TIMEOUT_S)
        r.raise_for_status()
        prices = r.json().get("prices", [])
        out: list[HistoricalPoint] = []
        for ts_ms, price in prices:
            d = datetime.utcfromtimestamp(ts_ms / 1000).date()
            out.append(HistoricalPoint(day=d, close_usd=float(price)))
        # Dedup by day (CoinGecko sometimes returns intra-day points).
        seen: dict[date, HistoricalPoint] = {}
        for p in out:
            seen[p.day] = p
        return sorted(seen.values(), key=lambda p: p.day)
    except Exception as e:                                            # pragma: no cover
        log.info("CoinGecko history %s failed: %s", symbol, e)
        return []


# ─── CoinDesk backends ───────────────────────────────────────────────


def _coindesk_data_spot(symbol: str) -> Optional[float]:
    key = os.getenv("COINDESK_API_KEY")
    if not key:
        return None
    try:                                                              # pragma: no cover
        r = requests.get(
            f"{_COINDESK_DATA}/index/cc/v1/latest/tick",
            params={"market": "cadli", "instruments": f"{symbol}-USD"},
            headers={**_HTTP_HEADERS, "x-api-key": key},
            timeout=_TIMEOUT_S,
        )
        r.raise_for_status()
        body = r.json()
        instruments = (body.get("Data") or {}).get(f"{symbol}-USD") or {}
        return float(instruments.get("VALUE"))
    except Exception as e:                                            # pragma: no cover
        log.info("CoinDesk Data v3 spot %s failed: %s", symbol, e)
        return None


def _coindesk_bpi_spot() -> Optional[float]:
    try:                                                              # pragma: no cover
        r = requests.get(f"{_COINDESK_BPI}/currentprice/USD.json",
                          headers=_HTTP_HEADERS, timeout=_TIMEOUT_S)
        r.raise_for_status()
        return float(r.json()["bpi"]["USD"]["rate_float"])
    except Exception as e:                                            # pragma: no cover
        log.info("CoinDesk BPI spot failed: %s", e)
        return None


def _coindesk_bpi_history(days: int) -> list[HistoricalPoint]:
    end = date.today()
    start = end - timedelta(days=days)
    try:                                                              # pragma: no cover
        r = requests.get(f"{_COINDESK_BPI}/historical/close.json",
                          params={"start": start.isoformat(),
                                  "end": end.isoformat()},
                          headers=_HTTP_HEADERS, timeout=_TIMEOUT_S)
        r.raise_for_status()
        body = r.json()
        return [
            HistoricalPoint(day=date.fromisoformat(d), close_usd=float(p))
            for d, p in sorted(body.get("bpi", {}).items())
        ]
    except Exception as e:                                            # pragma: no cover
        log.info("CoinDesk BPI history failed: %s", e)
        return []


# ─── x402 signal (real network round-trip) ───────────────────────────


def _x402_signal(symbol: str, payer, max_pay_usd: float, facilitator: str
                 ) -> Optional[PremiumSignal]:                        # pragma: no cover
    """402 → pay → 200 cycle against a real facilitator."""
    url = f"{facilitator}/premium/signals/{symbol}"
    r = requests.get(url, headers=_HTTP_HEADERS, timeout=_TIMEOUT_S)
    if r.status_code != 402:
        # Endpoint doesn't speak x402 — skip and let caller use the
        # synthetic path.
        return None

    pr = r.json().get("payment_requirements") or r.json().get("accepts", [{}])[0]
    if not pr:
        return None

    amount       = pr.get("amount") or pr.get("max_amount_required")
    currency     = pr.get("currency", "XRP")
    issuer       = pr.get("issuer")
    destination  = pr.get("destination")
    nonce        = pr.get("nonce") or pr.get("extra", {}).get("nonce", "")

    cost_usd = (float(amount) / 1_000_000 * 0.50) if currency == "XRP" else float(amount)
    if cost_usd > max_pay_usd:
        log.warning("x402 facilitator wants $%.4f, cap is $%.4f", cost_usd, max_pay_usd)
        return None

    tx_hash = payer.pay(
        destination=destination, amount=str(amount), currency=currency,
        issuer=issuer, memo=f"x402:coindesk:{nonce}",
    )

    import base64, json
    proof = base64.b64encode(json.dumps({
        "tx_hash": tx_hash, "nonce": nonce, "destination": destination,
    }).encode()).decode()
    r2 = requests.get(url, headers={**_HTTP_HEADERS, "X-Payment": proof},
                       timeout=_TIMEOUT_S)
    r2.raise_for_status()
    body = r2.json()
    return PremiumSignal(
        symbol=symbol,
        direction=str(body.get("direction", "hold")),
        confidence=float(body.get("confidence", 0.5)),
        rationale=str(body.get("rationale", "")),
        cost_usd_paid=cost_usd,
        paid_tx_hash=tx_hash,
        source="coindesk-premium-x402",
    )


# ─── Synthetic signal (real data, no facilitator) ────────────────────


def _synthetic_signal(symbol: str, *, max_pay_usd: float, source: str
                       ) -> PremiumSignal:
    """Build a buy/sell/hold from real CoinGecko 7-day momentum.

    Strategy:
      - 7-day return > +5%      → BUY  (confidence scales with return)
      - 7-day return < -5%      → SELL
      - otherwise               → HOLD
    Confidence = clamped abs(return) * 6, capped at 0.92.
    """
    # Try Coinbase first; CoinGecko second.
    pts: list[HistoricalPoint] = []
    if source != "mock":
        from . import cdp
        cdp_rows = cdp.cdp_candles(symbol, 7)
        if cdp_rows:
            pts = [HistoricalPoint(day=d, close_usd=c) for d, c in cdp_rows]
        else:
            pts = _coingecko_history(symbol, 7)
    if not pts or len(pts) < 2:
        return _canned_signal(symbol, max_pay_usd, source="mock")

    first = pts[0].close_usd
    last = pts[-1].close_usd
    change = (last - first) / first if first else 0.0
    confidence = min(0.92, max(0.50, abs(change) * 6.0))

    if change > 0.05:
        direction = "buy"
        rationale = (
            f"{symbol} is up {change*100:.1f}% over the last 7 days "
            f"(${first:,.4f} → ${last:,.4f}). Momentum favors a continuation; "
            f"watch for a re-test of the recent high before adding size."
        )
    elif change < -0.05:
        direction = "sell"
        rationale = (
            f"{symbol} is down {change*100:.1f}% over the last 7 days "
            f"(${first:,.4f} → ${last:,.4f}). Trend is broken; reduce risk "
            f"or wait for a reclaim of the prior range high."
        )
    else:
        direction = "hold"
        rationale = (
            f"{symbol} is range-bound — only {change*100:.1f}% over 7 days "
            f"(${first:,.4f} → ${last:,.4f}). Hold and wait for either a "
            f"breakout or a 5%+ move before committing."
        )

    return PremiumSignal(
        symbol=symbol,
        direction=direction,
        confidence=confidence,
        rationale=rationale,
        cost_usd_paid=0.0,
        paid_tx_hash=None,
        source=source,
    )


# ─── Mocks (offline fallbacks) ────────────────────────────────────────


_BASE_PRICES = {"BTC": 92_400.0, "ETH": 3_180.0, "XRP": 2.85,
                "SOL": 165.40, "RLUSD": 1.00, "USDC": 1.00,
                "USDT": 1.00, "ADA": 0.42, "DOT": 5.85, "LINK": 14.20,
                "DOGE": 0.31, "AVAX": 28.50, "MATIC": 0.78}


def _mock_spot_price(symbol: str) -> SpotPrice:
    base = _BASE_PRICES.get(symbol, 100.0)
    bias = ((datetime.utcnow().minute % 10) - 5) / 100.0
    return SpotPrice(
        symbol=symbol, price_usd=round(base * (1 + bias), 4),
        fetched_at=datetime.utcnow(),
        source="mock",
    )


def _mock_historical(symbol: str, days: int) -> list[HistoricalPoint]:
    base = _BASE_PRICES.get(symbol, 100.0)
    end = date.today()
    pts: list[HistoricalPoint] = []
    for i in range(days, -1, -1):
        d = end - timedelta(days=i)
        wave = ((i % 7) - 3) * 0.005
        pts.append(HistoricalPoint(day=d, close_usd=round(base * (1 + wave), 4)))
    return pts


def _canned_signal(symbol: str, max_pay_usd: float, *, source: str
                    ) -> PremiumSignal:
    canned = {
        "BTC": ("buy",  0.71, "Funding rates flipping positive after a 3-day cool-off; OI rebuilding without leverage froth."),
        "ETH": ("hold", 0.55, "Range-bound vs. BTC; Layer-2 fee compression argues for patience until Pectra exits feature freeze."),
        "XRP": ("buy",  0.78, "Stablecoin growth on the XRPL is up 32% MoM, RLUSD volumes hit ATH, and ETF flow expectations price into spot."),
        "SOL": ("sell", 0.62, "Meme-coin volume share above 70%; rotational outflow into majors is the consensus 4Q setup."),
    }
    direction, conf, why = canned.get(
        symbol, ("hold", 0.50, "Insufficient depth in the demo dataset for high-confidence calls."))
    return PremiumSignal(
        symbol=symbol, direction=direction, confidence=conf, rationale=why,
        cost_usd_paid=min(max_pay_usd, 0.02),
        paid_tx_hash=None,
        source=source,
    )
