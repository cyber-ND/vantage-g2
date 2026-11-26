"""Attention routing. The agent no longer claims to know better than the
market - it uses the market's price as truth and decides what is worth
interrupting you for.

Why this and not edge-finding: 13.6 hours of live collection showed the
model's picks losing 13.6c per $1 even at the mid, while the trades it
rejected made 2.4c. The market is better calibrated than we are. See the
"Forward result" section of the README.

What survives that finding is the harder and more interesting half. A G2
shows one card at a time and the wearer is walking, so the scarce resource is
not information, it is interruption. The agent's job is to spend a budget of
interruptions well, and to learn from taps which ones were worth it.

Event rates measured over the collected window (13.6h, 176 markets):

    any >=10pt move           53.0/hour   unusable - everything moves
    settles <15m, uncertain    8.1/hour   per market, first time only
    0.5 flip, confirmed        6.5/hour
    uncertain -> decided       0.6/hour   rare and high signal

So candidate events arrive at roughly 15/hour and the budget is a handful.
Choosing among them is the decision the agent actually has to make.
"""
import math
from datetime import datetime, timezone

from policy import OnlineLogistic, sigmoid  # noqa: F401

EVENT_FEATURES = [
    "bias",
    "is_resolving",     # settles very soon and is still live
    "is_flip",          # crossed even money, confirmed
    "is_decided",       # went from open question to answered
    "uncertainty",      # 1 - 2*|mid - 0.5|, peaks at a coin flip
    "urgency",          # 1 - ttl/window, peaks at settlement
    "move",             # absolute probability move since last look
    "is_sports",
    "is_price",
]

# Confirmation requirement: a single tick is noise at a median move of 6.4pt.
CONFIRM_TICKS = 1


class Event:
    __slots__ = ("kind", "index", "coin", "label", "mid", "prev", "ttl_h",
                 "features", "score", "why")

    def __init__(self, kind, snap, prev_mid):
        self.kind = kind
        self.index = snap["index"]
        self.coin = snap.get("coin") or ""
        self.label = snap.get("label") or self.coin or f"#{self.index}"
        self.mid = snap["mid"]
        self.prev = prev_mid
        self.ttl_h = snap.get("ttl_h") or 0.0
        self.features = featurise(kind, snap, prev_mid)
        self.score = 0.0
        self.why = []

    # ---- what the 576x288 display actually gets ----
    def headline(self):
        pct = int(round(self.mid * 100))
        if self.kind == "decided":
            return f"{self.label}  {'YES' if self.mid >= 0.5 else 'NO'}"
        return f"{self.label}  {pct}%"

    def detail(self):
        mins = max(0, int(round(self.ttl_h * 60)))
        if self.kind == "resolving":
            return f"settles in {mins}m - still open"
        if self.kind == "flip":
            d = int(round((self.mid - self.prev) * 100))
            return f"crossed even money ({d:+d} pts), {mins}m left"
        if self.kind == "decided":
            return f"question settled, {mins}m left"
        return f"{mins}m left"

    def __repr__(self):
        return f"<{self.kind} {self.label} {self.mid:.2f} ttl={self.ttl_h:.2f}h>"


def featurise(kind, snap, prev_mid):
    mid = snap["mid"]
    ttl = snap.get("ttl_h") or 0.0
    window = 1.0  # fair.MAX_TTL_HOURS
    k = (snap.get("kind") or "")
    coin = snap.get("coin") or ""
    # One sparse feature per underlying, created on first sight. Without this
    # the agent can learn what KIND of moment you care about but never WHICH
    # market - and "I hold BTC, I do not care about HYPE" is most of what
    # personal relevance actually means. OnlineLogistic is dict-backed, so
    # unseen keys start at zero weight and cost nothing until they appear.
    extra = {f"coin:{coin}": 1.0} if coin else {}
    return {
        **extra,
        "bias": 1.0,
        "is_resolving": 1.0 if kind == "resolving" else 0.0,
        "is_flip": 1.0 if kind == "flip" else 0.0,
        "is_decided": 1.0 if kind == "decided" else 0.0,
        "uncertainty": max(0.0, 1.0 - 2.0 * abs(mid - 0.5)),
        "urgency": max(0.0, min(1.0, 1.0 - ttl / window)),
        "move": min(1.0, abs(mid - prev_mid) * 2.0) if prev_mid is not None else 0.0,
        "is_sports": 1.0 if k.startswith("sports") else 0.0,
        "is_price": 1.0 if k.startswith(("binaryPrice", "priceTouch")) else 0.0,
    }


class Watcher:
    """Holds last-seen state per market and emits confirmed events."""

    def __init__(self):
        self.last = {}      # index -> (mid, ttl_h)
        self.pending = {}   # index -> (kind, mid) awaiting confirmation
        self.announced = set()  # (index, kind) already surfaced once

    def update(self, snap):
        """snap: {index, coin, label, kind, mid, ttl_h}. Returns [Event]."""
        i = snap["index"]
        mid = snap["mid"]
        prev = self.last.get(i)
        self.last[i] = (mid, snap.get("ttl_h") or 0.0)
        if prev is None:
            return []
        prev_mid = prev[0]
        out = []

        # 1. resolving: settles soon and the answer is still genuinely open.
        #    Once per market - repeating it every minute is what makes a
        #    notification system hated.
        if (snap.get("ttl_h") or 0.0) <= 0.25 and 0.3 <= mid <= 0.7:
            if (i, "resolving") not in self.announced:
                self.announced.add((i, "resolving"))
                out.append(Event("resolving", snap, prev_mid))

        # 2. flip across even money, confirmed on the following tick
        crossed = (prev_mid < 0.5 <= mid) or (prev_mid >= 0.5 > mid)
        pend = self.pending.get(i)
        if pend and pend[0] == "flip":
            still = (mid >= 0.5) == (pend[1] >= 0.5)
            self.pending.pop(i, None)
            if still and (i, "flip") not in self.announced:
                self.announced.add((i, "flip"))
                out.append(Event("flip", snap, prev_mid))
        elif crossed:
            self.pending[i] = ("flip", mid)

        # 3. decided: an open question became near-certain
        if 0.2 < prev_mid < 0.8 and (mid >= 0.9 or mid <= 0.1):
            if (i, "decided") not in self.announced:
                self.announced.add((i, "decided"))
                out.append(Event("decided", snap, prev_mid))

        return out


class EventAgent:
    """Ranks events and spends a limited interruption budget on the best."""

    def __init__(self, weights=None, budget_per_hour=6, floor=0.15, memory=240):
        self.pref = OnlineLogistic(weights=weights or _priors())
        self.budget_per_hour = budget_per_hour
        self.floor = floor          # never interrupt below this, budget or not
        self.memory = memory        # how many recent candidate scores to keep
        self.recent = []            # rolling score distribution
        self.shown = []             # timestamps of surfaced cards
        self.suppressed = 0

    def _budget_ok(self, now):
        cutoff = now.timestamp() - 3600
        self.shown = [t for t in self.shown if t > cutoff]
        return len(self.shown) < self.budget_per_hour

    def threshold(self, seen_per_hour=None):
        """Self-tuning bar: show roughly `budget` of every `seen` candidates.

        A fixed cutoff cannot work here. The preference model happily learns
        that you ignore ETH, but if the bar sits at a constant then a slightly
        less interesting ETH card still clears it and still interrupts you.
        The agent has to decide how picky to be, not just what it likes - so
        the bar is the quantile of recent scores that matches the budget.
        """
        if len(self.recent) < 12:
            return self.floor
        rate = seen_per_hour or (len(self.recent) or 1)
        keep = max(0.02, min(1.0, self.budget_per_hour / max(rate, 1e-9)))
        ranked = sorted(self.recent, reverse=True)
        idx = min(len(ranked) - 1, max(0, int(len(ranked) * keep) - 1))
        return max(self.floor, ranked[idx])

    def choose(self, events, now=None, seen_per_hour=None):
        """The single card to show, or None. This is the decision."""
        now = now or datetime.now(timezone.utc)
        if not events:
            return None
        for e in events:
            e.score = self.pref.score(e.features)
            e.why = self.pref.top_drivers(e.features, 2)
            self.recent.append(e.score)
        if len(self.recent) > self.memory:
            self.recent = self.recent[-self.memory:]

        best = max(events, key=lambda e: e.score)
        if best.score < self.threshold(seen_per_hour):
            self.suppressed += len(events)
            return None
        if not self._budget_ok(now):
            self.suppressed += len(events)
            return None
        self.shown.append(now.timestamp())
        return best

    def on_action(self, event, acted):
        """The tap is the label. This is the whole learning signal."""
        self.pref.update(event.features, 1.0 if acted else 0.0)

    def report(self):
        return {
            "updates": self.pref.n,
            "cards_last_hour": len(self.shown),
            "suppressed": self.suppressed,
            "weights": {k: round(v, 3) for k, v in self.pref.w.items()},
        }


def _priors():
    """Start opinionated rather than at zero - a cold agent that shows
    nothing teaches the user nothing, and one that shows everything gets
    switched off. These are starting points the taps then move."""
    return {
        "bias": -0.6,
        "is_resolving": 0.4,
        "is_flip": 0.2,
        "is_decided": 0.9,   # rarest and most informative
        "uncertainty": 0.8,
        "urgency": 0.7,
        "move": 0.3,
        "is_sports": 0.0,
        "is_price": 0.0,
    }
