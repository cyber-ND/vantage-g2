"""Is the fair-value model actually calibrated? Same question hist.py asked
of Polymarket, asked of our own model instead of the market's price.

Outcome coins carry no candle history, so market prices for settled markets
cannot be replayed. What CAN be replayed is the model: every settled market
still exposes its full spec (underlying perp, threshold, settlement time) and
its settleFraction, and the underlying's candles go back to April. So for each
settled market we rebuild exactly what the model would have said H hours
before expiry, and check it against what actually happened.

That is the decisive test. If the model says 0.70 and such markets settle Yes
70% of the time, disagreement with the book is real edge. If they settle Yes
55% of the time, the model is overconfident and the "edge" was our own error.

Horizon matters enormously, and not for a subtle reason: these markets are
short-lived and their threshold is set near spot at creation. Replaying one
at T-24h prices a threshold chosen later against a spot from a day earlier,
which is incoherent. Measured over 1630 settled markets:

    T-15m  Brier 0.179   T-1h  Brier 0.232   T-24h  Brier 0.297
    base rate (always 0.475) Brier 0.249

So the model beats the base rate inside ~1 hour of expiry and is worse than
guessing beyond ~2 hours. Default horizon is 30 minutes for that reason.

Usage:
  python3 -I agent/backtest.py [--horizon 0.5] [--probes 2500] [--refresh]
"""
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

import fair  # noqa: E402
import hl  # noqa: E402

CACHE = os.path.join(os.path.dirname(__file__), "backtest_cache.json")
HOUR_MS = 3600_000


# --------------------------------------------------------------- collection
def probe(args):
    idx, url = args
    try:
        r = hl.info({"type": "settledOutcome", "outcome": idx}, url)
    except RuntimeError:
        return None
    if not r or not isinstance(r, dict):
        return None
    spec = r.get("spec") or {}
    name = spec.get("name") or ""
    if "binaryPrice" not in name:
        return None
    try:
        frac = float(r.get("settleFraction"))
    except (TypeError, ValueError):
        return None
    if frac not in (0.0, 1.0):
        return None  # partial / void settlements are not a clean label
    d = hl.parse_desc(spec.get("description"))
    exp = hl.parse_time(d.get("time"))
    coin = (d.get("perp") or "").split("|")[0].strip()
    try:
        K = float(d.get("threshold"))
    except (TypeError, ValueError):
        return None
    if not (exp and coin and K):
        return None
    return {"index": idx, "coin": coin, "K": K,
            "expiry": exp.isoformat(), "won": frac}


def collect(url, lo, hi, probes, workers=12):
    step = max(1, (hi - lo) // probes)
    idxs = list(range(lo, hi, step))
    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(probe, [(i, url) for i in idxs]):
            if r:
                out.append(r)
    return out, len(idxs)


# ------------------------------------------------------------------ replay
def series(coin, url, days=200):
    now = int(time.time() * 1000)
    req = {"coin": coin, "interval": "1h",
           "startTime": now - days * 86400000, "endTime": now}
    rows = hl.info({"type": "candleSnapshot", "req": req}, url)
    return rows if isinstance(rows, list) else []


def replay(rec, rows, horizon_h, vol_days=14):
    """What the model would have said `horizon_h` before expiry."""
    exp = datetime.fromisoformat(rec["expiry"])
    t_eval = int(exp.timestamp() * 1000) - horizon_h * HOUR_MS
    window = [c for c in rows if c["t"] <= t_eval]
    if len(window) < vol_days * 24:
        return None
    spot = float(window[-1]["c"])
    vol = fair.parkinson_vol(window[-vol_days * 24:])
    if not vol or not spot:
        return None
    years = horizon_h / (365.0 * 24.0)
    sig = fair.Pricer.sigmas(spot, rec["K"], vol, years)
    if abs(sig) > fair.MAX_SIGMA:
        return None  # the model declines out here, so do not score it here
    p = fair.bs_digital_above(spot, rec["K"], vol, years)
    if p is None:
        return None
    return {"p": p, "won": rec["won"], "sigma": sig, "coin": rec["coin"]}


# ------------------------------------------------------------------ scoring
def buckets(scored, n=5):
    tab = [[0.0, 0.0, 0] for _ in range(n)]
    for s in scored:
        b = tab[min(n - 1, max(0, int(s["p"] * n)))]
        b[0] += s["p"]
        b[1] += s["won"]
        b[2] += 1
    return tab


def brier(scored):
    return sum((s["p"] - s["won"]) ** 2 for s in scored) / len(scored) if scored else None


def main():
    args = sys.argv[1:]
    url = hl.MAINNET
    horizon = float(args[args.index("--horizon") + 1]) if "--horizon" in args else 0.5
    probes = int(args[args.index("--probes") + 1]) if "--probes" in args else 2500

    if os.path.exists(CACHE) and "--refresh" not in args:
        recs = json.load(open(CACHE))
        print(f"loaded {len(recs)} settled binaryPrice markets from cache")
    else:
        live = hl.load_markets(url)
        hi = max(m["index"] for m in live)
        print(f"probing settled outcomes 1000..{hi} ({probes} probes)...")
        recs, n = collect(url, 1000, hi, probes)
        json.dump(recs, open(CACHE, "w"))
        print(f"  {len(recs)} settled binaryPrice markets from {n} probes")

    if not recs:
        print("no settled markets found")
        return 1

    coins = sorted({r["coin"] for r in recs})
    print(f"underlyings: {len(coins)} -> {', '.join(coins[:10])}")
    rows_by_coin = {}
    for c in coins:
        try:
            rows_by_coin[c] = series(c, url)
        except RuntimeError:
            rows_by_coin[c] = []

    scored = []
    for r in recs:
        rows = rows_by_coin.get(r["coin"]) or []
        s = replay(r, rows, horizon)
        if s:
            scored.append(s)

    print(f"\nreplayed at T-{horizon}h, inside +/-{fair.MAX_SIGMA} sigma: "
          f"{len(scored)} of {len(recs)}")
    if not scored:
        print("nothing scorable")
        return 1

    base = sum(s["won"] for s in scored) / len(scored)
    print(f"base rate (Yes): {base:.3f}")
    print(f"model Brier    : {brier(scored):.4f}")
    print(f"always-base    : {sum((base - s['won'])**2 for s in scored)/len(scored):.4f}")

    print("\ncalibration - model says vs actually happened")
    print(f"  {'bucket':12}{'n':>6}{'model':>9}{'actual':>9}{'gap':>9}")
    worst = 0.0
    for i, (sp, sw, n) in enumerate(buckets(scored)):
        if not n:
            continue
        pm, pa = sp / n, sw / n
        worst = max(worst, abs(pm - pa))
        lo, hi_ = i * 0.2, (i + 1) * 0.2
        print(f"  {lo:.1f}-{hi_:.1f}{'':4}{n:>6}{pm:>9.3f}{pa:>9.3f}{pa-pm:>+9.3f}")

    print(f"\nlargest bucket gap: {worst:.3f}")
    if worst < 0.05:
        print("VERDICT: model is well calibrated - disagreement with the book is "
              "evidence of real edge.")
    elif worst < 0.12:
        print("VERDICT: mildly miscalibrated - usable, but the Calibrator layer "
              "has to carry the correction.")
    else:
        print("VERDICT: badly miscalibrated - the 'edges' are most likely our own "
              "model error. Do not trade this.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
