"""Forward collection: log what the agent would have done, at prices it could
really have taken, then grade it when the market settles.

The backtest graded the model's probabilities. It could not grade profit,
because settled outcome coins expose no price history - so the only way to
learn whether the edge survives spread and fees is to write down the book in
real time and wait.

Two modes:

  collect   poll markets inside the model's one-hour window, append one JSONL
            row per (market, side) with the live ask, the model's fair value,
            the calibrated fair value and the depth available. Records EVERY
            modelled candidate, not only the ones that pass the gates, so the
            gates themselves can be evaluated afterwards.

  grade     look up settleFraction for everything recorded, compute realised
            P&L per $1 staked at the price actually quoted, and split it by
            whether the agent would have taken the trade.

Usage:
  python3 -I agent/collect.py collect [--interval 60] [--once]
  python3 -I agent/collect.py grade
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

import fair  # noqa: E402
import hl  # noqa: E402
from policy import Agent  # noqa: E402

HERE = os.path.dirname(__file__)
LOG = os.path.join(HERE, "observations.jsonl")
STATE = os.path.join(HERE, "state.json")
META_TTL = 300  # re-pull outcomeMeta at most every 5 minutes


class MetaCache:
    def __init__(self, url):
        self.url = url
        self.at = 0
        self.markets = []

    def get(self):
        if time.time() - self.at > META_TTL:
            self.markets = hl.load_markets(self.url)
            self.at = time.time()
        return self.markets


def books(args):
    m, url = args
    try:
        yb, ya, _, yas = hl.l2(m["yes_coin"], url)
        nb, na, _, nas = hl.l2(m["no_coin"], url)
    except RuntimeError:
        return None
    return m, yb, ya, yas, nb, na, nas


def in_window(markets, now):
    """Markets inside the model's validity horizon, not yet expired."""
    out = []
    for m in markets:
        exp = m.get("expiry")
        if not exp or not (m.get("kind") or "").startswith(("binaryPrice", "priceTouch")):
            continue
        ttl_h = (exp - now).total_seconds() / 3600.0
        if 0 < ttl_h <= fair.MAX_TTL_HOURS:
            out.append((m, ttl_h))
    return out


def tick(url, pricer, agent, cache, seen):
    now = datetime.now(timezone.utc)
    cands = in_window(cache.get(), now)
    if not cands:
        return 0, 0

    rows = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(books, [(m, url) for m, _ in cands]):
            if res:
                rows.append(res)

    ttl_by_idx = {m["index"]: t for m, t in cands}
    written = gated = 0
    with open(LOG, "a") as fh:
        for m, yb, ya, yas, nb, na, nas in rows:
            model = pricer.fair(m, now=now)
            if not model:
                continue
            lo, mid, hi, meth = model
            und = fair.underlying_of(m)
            um = pricer.market(und)
            base = {
                "ts": now.isoformat(),
                "index": m["index"],
                "coin": und,
                "kind": m["kind"],
                "K": fair.strike_of(m),
                "expiry": m["expiry"].isoformat(),
                "ttl_h": round(ttl_by_idx.get(m["index"], 0.0), 4),
                "spot": um.spot if um else None,
                "vol": um.vol if um else None,
                "model_lo": lo, "model_mid": mid, "model_hi": hi, "method": meth,
            }
            for side, px, bid, depth, f_raw in (
                ("Yes", ya, yb, yas, lo),
                ("No", na, nb, nas, 1.0 - hi),
            ):
                if px is None:
                    continue
                # dedupe: one row per (market, side, minute)
                key = (m["index"], side, now.strftime("%Y%m%d%H%M"))
                if key in seen:
                    continue
                seen.add(key)
                cal = agent.cal.adjust(f_raw)
                take = agent.consider(m, side, px, bid, depth, f_raw)
                row = dict(base)
                row.update({
                    "side": side,
                    "ask": px,
                    "bid": bid,
                    "depth": depth,
                    "fair_raw": f_raw,
                    "fair_cal": cal,
                    "edge_cal": cal - px,
                    "would_take": bool(take),
                })
                fh.write(json.dumps(row) + "\n")
                written += 1
                if take:
                    gated += 1
    return written, gated


def collect(url, interval, once=False):
    pricer = fair.Pricer(url)
    agent = Agent(state_path=STATE)
    cache = MetaCache(url)
    seen = set()
    total = total_gated = 0
    print(f"collecting to {LOG} every {interval}s "
          f"(window <= {fair.MAX_TTL_HOURS}h to expiry). ctrl-c to stop.",
          flush=True)
    while True:
        try:
            # a fresh Pricer every loop so spot/vol are not stale
            pricer = fair.Pricer(url)
            w, g = tick(url, pricer, agent, cache, seen)
            total += w
            total_gated += g
            if w:
                print(f"{datetime.now(timezone.utc):%H:%M:%S}  +{w} rows "
                      f"({g} would-take)  total={total}/{total_gated}", flush=True)
        except Exception as e:  # noqa: BLE001 - a daemon must not die on one bad poll
            print(f"{datetime.now(timezone.utc):%H:%M:%S}  poll failed: {e}", flush=True)
        if once:
            return total, total_gated
        if len(seen) > 200_000:
            seen.clear()
        time.sleep(interval)


# -------------------------------------------------------------------- grade
def grade(url):
    if not os.path.exists(LOG):
        print("no observations yet")
        return 1
    rows = [json.loads(l) for l in open(LOG) if l.strip()]
    print(f"{len(rows)} observations")
    idxs = sorted({r["index"] for r in rows})

    def settle(i):
        try:
            r = hl.info({"type": "settledOutcome", "outcome": i}, url)
        except RuntimeError:
            return i, None
        if not r:
            return i, None
        try:
            f = float(r.get("settleFraction"))
        except (TypeError, ValueError):
            return i, None
        return i, (f if f in (0.0, 1.0) else None)

    res = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for i, f in ex.map(settle, idxs):
            if f is not None:
                res[i] = f
    print(f"{len(res)} of {len(idxs)} markets settled so far")
    if not res:
        print("nothing settled yet - let it run longer")
        return 0

    def pnl(r):
        f = res.get(r["index"])
        if f is None:
            return None
        won = f if r["side"] == "Yes" else (1.0 - f)
        return won - r["ask"]  # per $1 staked, taking the ask

    scored = [(r, pnl(r)) for r in rows]
    scored = [(r, p) for r, p in scored if p is not None]
    if not scored:
        print("no scorable rows yet")
        return 0

    def summarise(label, sub):
        if not sub:
            print(f"  {label:22} n=0")
            return
        ps = [p for _, p in sub]
        avg = sum(ps) / len(ps)
        wins = sum(1 for p in ps if p > 0)
        print(f"  {label:22} n={len(ps):<6} avg P&L/$1 {avg:+.4f}   win rate {wins/len(ps):.3f}")

    print("\nrealised P&L, per $1 staked at the quoted ask:")
    summarise("all observations", scored)
    summarise("agent would take", [(r, p) for r, p in scored if r["would_take"]])
    summarise("agent would skip", [(r, p) for r, p in scored if not r["would_take"]])
    print()
    for lo, hi_ in ((0.03, 0.06), (0.06, 0.10), (0.10, 1.0)):
        sub = [(r, p) for r, p in scored if lo <= r["edge_cal"] < hi_]
        summarise(f"calibrated edge {lo:.2f}+", sub)
    return 0


def main():
    args = sys.argv[1:]
    mode = args[0] if args else "collect"
    url = hl.MAINNET
    if mode == "grade":
        return grade(url)
    interval = int(args[args.index("--interval") + 1]) if "--interval" in args else 60
    collect(url, interval, once="--once" in args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
