"""Independent fair-value model for HIP-4 price markets.

This is the brain the book-arithmetic scan was missing. Ported from
../polymarket-finance-test/ (snapshot.py + common.py), with the data source
swapped: Polymarket read option chains off CBOE, but every Hyperliquid
outcome market references a perp that Hyperliquid itself quotes, so spot and
volatility both come from the venue. One dependency instead of two.

Kept from the original:
  * bs_digital_above()      - lognormal terminal digital
  * the touch approximation - P(touch) ~ 2 x P(terminal beyond barrier)
  * the (lo, mid, hi) band  - never emit a point estimate you can't defend

Changed:
  * implied vol from an option chain  ->  realised vol from HL candles
    (Parkinson high-low estimator, ~5x more efficient than close-to-close)
  * the band now comes from vol uncertainty rather than bid/ask width
"""
import math
import time
from datetime import datetime, timezone

import hl

HOURS_PER_YEAR = 24 * 365
VOL_BAND = 0.18  # +/- fraction applied to sigma to build the lo/hi band

# A lognormal model is only honest near the middle of the distribution. Real
# assets jump, so out in the tail the model says "impossible" and the market
# says "6%" - and the market is right. Measured on live gold/silver markets:
# at +2.7 to +3.0 sigma the model claimed 0.2-0.4% where the book paid 6.5-9%.
# Acting on that would be a fat-tail short, which is exactly how you blow up.
# Beyond this many sigma the model declines to have an opinion.
MAX_SIGMA = 2.0

# The model only has predictive power close to settlement. Backtested over
# 1630 settled markets, Brier against a 0.475 base rate (0.249):
#   T-15m 0.179 | T-30m 0.196 | T-1h 0.232 | T-2h 0.267 | T-24h 0.297
# Beyond ~2 hours it is worse than guessing, because these markets are short
# lived and their threshold is set near spot at creation - so a long-horizon
# estimate prices a threshold that did not exist yet. Refuse to price beyond
# this, rather than emitting confident nonsense.
MAX_TTL_HOURS = 1.0


# ------------------------------------------------------------------ ported
def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_digital_above(spot, strike, iv, years):
    """P(S_T > K) under lognormal, zero drift. From common.py."""
    if years <= 0 or not iv or not spot or not strike or spot <= 0 or strike <= 0:
        return None
    s = iv * math.sqrt(years)
    if s <= 0:
        return None
    d2 = (math.log(spot / strike) - 0.5 * s * s) / s
    return ncdf(d2)


# --------------------------------------------------------------- market IO
def underlying_of(mkt):
    f = mkt.get("fields") or {}
    u = f.get("perp") or f.get("instrument") or f.get("shortName")
    if not u:
        return None
    return u.split("|")[0].strip()


def strike_of(mkt):
    """binaryPrice uses 'threshold'; priceTouch uses 'target'."""
    f = mkt.get("fields") or {}
    for k in ("threshold", "target"):
        if f.get(k) is not None:
            try:
                return float(f[k])
            except (TypeError, ValueError):
                pass
    return None


def candles(coin, url, interval="1h", days=14):
    now = int(time.time() * 1000)
    req = {"coin": coin, "interval": interval,
           "startTime": now - days * 86400000, "endTime": now}
    r = hl.info({"type": "candleSnapshot", "req": req}, url)
    return r if isinstance(r, list) else []


def parkinson_vol(rows, periods_per_year=HOURS_PER_YEAR):
    """Annualised vol from high/low ranges. Needs ~30+ rows to be stable."""
    acc = n = 0
    for c in rows:
        try:
            h, l = float(c["h"]), float(c["l"])
        except (KeyError, TypeError, ValueError):
            continue
        if h > 0 and l > 0 and h >= l:
            r = math.log(h / l)
            acc += r * r
            n += 1
    if n < 30:
        return None
    var = acc / (4.0 * math.log(2.0) * n)
    return math.sqrt(max(var, 1e-12) * periods_per_year)


def close_vol(rows, periods_per_year=HOURS_PER_YEAR):
    """Close-to-close vol, as a sanity check against Parkinson."""
    cl = []
    for c in rows:
        try:
            cl.append(float(c["c"]))
        except (KeyError, TypeError, ValueError):
            pass
    if len(cl) < 31:
        return None
    rets = [math.log(b / a) for a, b in zip(cl, cl[1:]) if a > 0 and b > 0]
    if len(rets) < 30:
        return None
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(max(var, 1e-12) * periods_per_year)


class Market:
    """Spot + vol for one underlying, fetched once and reused."""

    __slots__ = ("coin", "spot", "vol", "vol_cc", "n")

    def __init__(self, coin, url, days=14):
        rows = candles(coin, url, days=days)
        self.coin = coin
        self.n = len(rows)
        self.spot = float(rows[-1]["c"]) if rows else None
        self.vol = parkinson_vol(rows)
        self.vol_cc = close_vol(rows)

    def ok(self):
        return bool(self.spot and self.vol)


class Pricer:
    """Caches one Market per underlying across a scan."""

    def __init__(self, url=hl.MAINNET, days=14):
        self.url = url
        self.days = days
        self._cache = {}

    @staticmethod
    def sigmas(spot, strike, vol, years):
        """How many standard deviations the strike sits from spot."""
        denom = vol * math.sqrt(max(years, 1e-12))
        if denom <= 0 or not spot or spot <= 0 or not strike or strike <= 0:
            return 0.0
        return math.log(strike / spot) / denom

    def market(self, coin):
        if coin not in self._cache:
            try:
                self._cache[coin] = Market(coin, self.url, self.days)
            except RuntimeError:
                self._cache[coin] = None
        return self._cache[coin]

    def fair(self, mkt, now=None):
        """(lo, mid, hi, method) that the YES side settles at 1, or None."""
        kind = mkt.get("kind") or ""
        if not kind.startswith(("binaryPrice", "priceTouch")):
            return None  # sports etc. need a different model entirely
        K = strike_of(mkt)
        exp = mkt.get("expiry")
        coin = underlying_of(mkt)
        if not (K and exp and coin):
            return None

        now = now or datetime.now(timezone.utc)
        years = (exp - now).total_seconds() / (365.0 * 86400.0)
        if years <= 0:
            return None

        m = self.market(coin)
        if not m or not m.ok():
            return None

        if years * 365.0 * 24.0 > MAX_TTL_HOURS:
            return None  # too far from settlement to be meaningful - see MAX_TTL_HOURS
        if abs(self.sigmas(m.spot, K, m.vol, years)) > MAX_SIGMA:
            return None  # out past where lognormal can be trusted - see MAX_SIGMA

        def digital(sigma):
            return bs_digital_above(m.spot, K, sigma, years)

        lo_s, hi_s = m.vol * (1 - VOL_BAND), m.vol * (1 + VOL_BAND)

        if kind.startswith("binaryPrice"):
            mid = digital(m.vol)
            if mid is None:
                return None
            a, b = digital(lo_s), digital(hi_s)
            band = sorted(x for x in (a, b, mid) if x is not None)
            return band[0], mid, band[-1], "rv-digital"

        # priceTouch: barrier either side of spot; reflection approximation
        up = K > m.spot
        def touch(sigma):
            p = digital(sigma)
            if p is None:
                return None
            p = p if up else (1.0 - p)
            return min(1.0, 2.0 * p)

        mid = touch(m.vol)
        if mid is None:
            return None
        a, b = touch(lo_s), touch(hi_s)
        band = sorted(x for x in (a, b, mid) if x is not None)
        return band[0], mid, band[-1], "rv-touch"
