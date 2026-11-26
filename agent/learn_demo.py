"""Proof that the two learning loops converge. No network, deterministic seed.

Loop 1 (calibration): we simulate the favourite-longshot bias - the thing the
polymarket-finance-test/hist.py calibration bucket test was built to detect.
Long shots are quoted RICH (they win less often than their price implies).
The Calibrator should discover that from settlements alone and correct it.

Loop 2 (preference): a synthetic user who only ever taps sports cards. The
OnlineLogistic should drive is_sports up and is_price down, with no rules
written anywhere about sports.

Usage: python3 -I agent/learn_demo.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))

from policy import Agent, Calibrator, OnlineLogistic, featurise  # noqa: E402


def true_prob(quote):
    """Favourite-longshot bias: quotes below 0.25 are rich by up to 6 cents."""
    if quote < 0.25:
        return max(0.0, quote - 0.06 * (0.25 - quote) / 0.25)
    return quote


def calibration_loop(n=4000, seed=7):
    rng = random.Random(seed)
    cal = Calibrator()
    raw_err = cal_err = 0.0
    scored = 0
    raw_b = cal_b = 0.0
    scored_b = 0
    for i in range(n):
        q = rng.choice([0.03, 0.08, 0.15, 0.22, 0.35, 0.5, 0.65, 0.8, 0.93])
        won = 1.0 if rng.random() < true_prob(q) else 0.0
        # score the agent's CURRENT belief before learning from this one
        if i > n // 4:
            raw_err += (q - won) ** 2
            cal_err += (cal.adjust(q) - won) ** 2
            scored += 1
            if q < 0.25:  # the region that is actually mispriced
                raw_b += (q - won) ** 2
                cal_b += (cal.adjust(q) - won) ** 2
                scored_b += 1
        cal.observe(q, won)
    return cal, raw_err / scored, cal_err / scored, raw_b / scored_b, cal_b / scored_b


def preference_loop(n=400, seed=11):
    rng = random.Random(seed)
    pref = OnlineLogistic()
    mkt_sport = {"index": 1, "kind": "sportsContestWinner", "label": "x", "expiry": None}
    mkt_price = {"index": 2, "kind": "binaryPrice", "label": "y", "expiry": None}
    hits = 0
    for i in range(n):
        is_sport = rng.random() < 0.5
        m = mkt_sport if is_sport else mkt_price
        f = featurise(m, 0.4, 0.02, 120.0, 0.46)
        pred = pref.score(f)
        label = 1.0 if is_sport else 0.0  # the user taps sports, ignores price
        if (pred > 0.5) == (label > 0.5):
            hits += 1
        pref.update(f, label)
    return pref, hits / n


def main():
    print("=" * 62)
    print("LOOP 1  calibration from settlements (ground truth is free)")
    print("=" * 62)
    cal, raw, adj, raw_b, adj_b = calibration_loop()
    print(f"  settlements observed : {cal.coverage()}")
    print(f"  Brier, raw quote     : {raw:.5f}")
    print(f"  Brier, calibrated    : {adj:.5f}   ({(raw - adj) / raw * 100:+.1f}%)")
    print("  restricted to the mispriced long-shot region (quote < 0.25):")
    print(f"    Brier, raw quote   : {raw_b:.5f}")
    print(f"    Brier, calibrated  : {adj_b:.5f}   ({(raw_b - adj_b) / raw_b * 100:+.1f}%)")
    print("\n  learned correction at the long-shot end:")
    for q in (0.03, 0.08, 0.15, 0.22, 0.5, 0.8):
        print(f"    quote {q:.2f} -> fair {cal.adjust(q):.3f}   (true {true_prob(q):.3f})")

    print()
    print("=" * 62)
    print("LOOP 2  preference from taps (no rule mentions 'sports')")
    print("=" * 62)
    pref, acc = preference_loop()
    print(f"  updates              : {pref.n}")
    print(f"  online accuracy      : {acc * 100:.1f}%")
    print("  learned weights:")
    for k, v in sorted(pref.w.items(), key=lambda kv: -abs(kv[1])):
        bar = "#" * min(28, int(abs(v) * 6))
        print(f"    {k:11} {v:+7.3f} {bar}")

    print()
    ok = (adj < raw) and (pref.w["is_sports"] > pref.w["is_price"])
    print("RESULT:", "both loops converged" if ok else "NOT CONVERGED - investigate")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
