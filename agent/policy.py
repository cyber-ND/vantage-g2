"""The decision layer: features, an online preference model, and a calibrator.

Two independent learning signals, deliberately kept separate:

  1. CALIBRATION  - "is my probability estimate any good?"
     Ground truth is free: every HIP-4 outcome settles to exactly 1 or 0.
     We bucket by quoted price, compare to realised win rate, and correct.
     Scored by Brier. This improves WHAT COUNTS AS A GOOD BET.

  2. PREFERENCE   - "does this user care about this card?"
     Ground truth is the tap: accept / dismiss / ignore on the glasses.
     Online logistic regression over the same feature vector.
     This improves WHAT GETS SHOWN.

Both are small, online, and inspectable - which matters, because the demo
has to *show* the thing learning on a 576x288 display.
"""
import json
import math
import os
from datetime import datetime, timezone

FEE_RATE = 0.0  # HIP-4 taker fee placeholder; set from deployerFeeScale per venue

FEATURES = [
    "bias",
    "price",          # quoted ask for the side considered
    "spread",         # ask - bid
    "log_depth",      # log1p(contracts available at the limit)
    "ttl_hours",      # hours to expiry (clipped)
    "is_sports",
    "is_price",
    "edge_raw",       # fair - price, before calibration
]


# ------------------------------------------------------------------ helpers
def sigmoid(z):
    if z < -30:
        return 0.0
    if z > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_digital_above(spot, strike, iv, years):
    """P(S_T > K) under lognormal. Ported from the polymarket research common.py."""
    if years <= 0 or not iv or not spot or not strike or spot <= 0 or strike <= 0:
        return None
    s = iv * math.sqrt(years)
    if s <= 0:
        return None
    d2 = (math.log(spot / strike) - 0.5 * s * s) / s
    return ncdf(d2)


def featurise(mkt, side_price, spread, depth, fair, now=None):
    """Feature vector for one (market, side) candidate."""
    now = now or datetime.now(timezone.utc)
    ttl = 0.0
    if mkt.get("expiry"):
        ttl = max(0.0, (mkt["expiry"] - now).total_seconds() / 3600.0)
    kind = mkt.get("kind") or ""
    return {
        "bias": 1.0,
        "price": side_price or 0.0,
        "spread": spread if spread is not None else 1.0,
        "log_depth": math.log1p(max(0.0, depth or 0.0)),
        "ttl_hours": min(ttl, 168.0) / 168.0,
        "is_sports": 1.0 if kind.startswith("sports") else 0.0,
        "is_price": 1.0 if kind.startswith("binaryPrice") else 0.0,
        "edge_raw": (fair - side_price) if (fair is not None and side_price is not None) else 0.0,
    }


# ------------------------------------------------------- preference learning
class OnlineLogistic:
    """SGD logistic regression. Tiny on purpose: it must be explainable on glass."""

    def __init__(self, lr=0.08, l2=1e-4, weights=None):
        self.lr = lr
        self.l2 = l2
        self.w = dict(weights or {k: 0.0 for k in FEATURES})
        self.n = 0

    def score(self, f):
        return sigmoid(sum(self.w.get(k, 0.0) * v for k, v in f.items()))

    def update(self, f, label):
        """label: 1 = user acted on the card, 0 = dismissed/ignored."""
        p = self.score(f)
        err = label - p
        for k, v in f.items():
            g = err * v - self.l2 * self.w.get(k, 0.0)
            self.w[k] = self.w.get(k, 0.0) + self.lr * g
        self.n += 1
        return abs(err)

    def top_drivers(self, f, k=2):
        """The k features pushing this card up - for the 'why' line on screen."""
        contrib = [(abs(self.w.get(n, 0.0) * v), n) for n, v in f.items() if n != "bias"]
        contrib.sort(reverse=True)
        return [n for _, n in contrib[:k]]


# ------------------------------------------------------------- calibration
class Calibrator:
    """Reliability table over quoted price. Learns the market's own bias."""

    def __init__(self, nbins=10, table=None):
        self.nbins = nbins
        # each bin: [sum_price, sum_outcome, count]
        self.table = table or [[0.0, 0.0, 0] for _ in range(nbins)]

    def _bin(self, p):
        return min(self.nbins - 1, max(0, int(p * self.nbins)))

    def observe(self, price, outcome):
        """outcome: 1 if that side won, 0 if it lost."""
        b = self.table[self._bin(price)]
        b[0] += price
        b[1] += outcome
        b[2] += 1

    def adjust(self, price, min_n=8):
        """Calibrated probability for a quoted price; falls back to the price.

        The correction is shrunk toward zero by its own standard error, so a
        bucket that merely looks biased because it is small gets ignored.
        Without this the calibrator happily "corrects" well-priced buckets and
        makes the Brier score worse - it did, before this was added.
        """
        b = self.table[self._bin(price)]
        if b[2] < min_n:
            return price
        n = b[2]
        realised = b[1] / n
        avg_px = b[0] / n
        delta = realised - avg_px
        # standard error of the realised rate; floored so n=min_n is not trusted
        var = max(realised * (1.0 - realised), 0.02)
        se2 = var / n
        # James-Stein style shrinkage: only believe deltas that beat their noise
        shrink = (delta * delta) / (delta * delta + se2) if (delta or se2) else 0.0
        return max(0.0, min(1.0, price + shrink * delta))

    def brier(self):
        """Brier score of the raw quote against realised outcomes."""
        num = den = 0.0
        for sp, so, n in self.table:
            if n:
                avg_px, realised = sp / n, so / n
                num += n * (avg_px - realised) ** 2
                den += n
        return (num / den) if den else None

    def coverage(self):
        return sum(n for _, _, n in self.table)


# ------------------------------------------------------------------- agent
class Agent:
    """Generates candidates, ranks them, surfaces at most one card."""

    def __init__(self, state_path=None):
        self.state_path = state_path
        self.pref = OnlineLogistic()
        self.cal = Calibrator()
        self.min_edge = 0.03        # 3 cents after calibration
        self.min_depth = 10.0       # contracts
        self.max_spread = 0.25
        if state_path and os.path.exists(state_path):
            self.load()

    # -- persistence ----------------------------------------------------
    def save(self):
        if not self.state_path:
            return
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        with open(self.state_path, "w") as fh:
            json.dump(
                {
                    "weights": self.pref.w,
                    "n": self.pref.n,
                    "table": self.cal.table,
                    "thresholds": {
                        "min_edge": self.min_edge,
                        "min_depth": self.min_depth,
                        "max_spread": self.max_spread,
                    },
                },
                fh,
                indent=2,
            )

    def load(self):
        with open(self.state_path) as fh:
            s = json.load(fh)
        self.pref = OnlineLogistic(weights=s.get("weights"))
        self.pref.n = s.get("n", 0)
        self.cal = Calibrator(table=s.get("table"))
        t = s.get("thresholds") or {}
        self.min_edge = t.get("min_edge", self.min_edge)
        self.min_depth = t.get("min_depth", self.min_depth)
        self.max_spread = t.get("max_spread", self.max_spread)

    # -- decisions ------------------------------------------------------
    def consider(self, mkt, side, price, bid, depth, fair):
        """Build one scored candidate, or None if it fails the hard gates."""
        if price is None or fair is None:
            return None
        spread = (price - bid) if (bid is not None) else None
        if spread is not None and spread > self.max_spread:
            return None
        if (depth or 0) < self.min_depth:
            return None
        cal_fair = self.cal.adjust(fair)
        edge = cal_fair - price - FEE_RATE
        if edge < self.min_edge:
            return None
        f = featurise(mkt, price, spread, depth, cal_fair)
        return {
            "index": mkt["index"],
            "label": mkt.get("label") or f"#{mkt['index']}",
            "kind": mkt.get("kind"),
            "side": side,
            "coin": mkt["yes_coin"] if side == "Yes" else mkt["no_coin"],
            "price": price,
            "fair": cal_fair,
            "edge": edge,
            "depth": depth,
            "expiry": mkt["expiry"].isoformat() if mkt.get("expiry") else None,
            "features": f,
            "pref": self.pref.score(f),
            "why": self.pref.top_drivers(f),
        }

    def rank(self, candidates):
        """Edge decides if it's worth showing; preference decides what's shown first."""
        return sorted(candidates, key=lambda c: c["edge"] * (0.5 + c["pref"]), reverse=True)

    # -- learning -------------------------------------------------------
    def on_user_action(self, card, acted):
        self.pref.update(card["features"], 1.0 if acted else 0.0)
        self.save()

    def on_settlement(self, price, won):
        self.cal.observe(price, 1.0 if won else 0.0)
        self.save()

    def report(self):
        return {
            "preference_updates": self.pref.n,
            "settlements_seen": self.cal.coverage(),
            "brier": self.cal.brier(),
            "weights": {k: round(v, 4) for k, v in self.pref.w.items()},
        }
