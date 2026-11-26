"""Replay the collected window through the attention agent.

Two jobs:
  1. validation - is the card rate survivable on a device you wear?
  2. demo       - the learning curve, on real market data, in one command

The simulated wearer below holds BTC and ignores every other underlying.
Nothing in events.py names BTC; if coin:BTC separates from coin:ETH and
coin:HYPE, the agent learned it from taps alone.

Usage:
  python3 -I agent/replay.py [--budget 6] [--quiet]
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from events import EventAgent, Watcher  # noqa: E402

LOG = os.path.join(os.path.dirname(__file__), "observations.jsonl")
# The simulated wearer holds BTC and nothing else.
HOLDS = {"BTC"}


def snapshots():
    """One snapshot per market per observation minute, Yes side, by time."""
    rows = []
    for line in open(LOG):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("side") != "Yes":
            continue
        b, a = r.get("bid"), r.get("ask")
        if b is None or a is None:
            continue
        rows.append({
            "ts": r["ts"],
            "index": r["index"],
            "coin": r["coin"],
            "label": r["coin"],
            "kind": r["kind"],
            "mid": (b + a) / 2.0,
            "ttl_h": r["ttl_h"],
        })
    rows.sort(key=lambda r: r["ts"])
    return rows


def taps(event):
    """The simulated wearer: holds BTC, ignores everything else.

    Nothing in events.py names BTC. If coin:BTC rises and coin:ETH /
    coin:HYPE fall, the agent worked that out from taps alone.
    """
    return event.coin in HOLDS


def main():
    args = sys.argv[1:]
    budget = int(args[args.index("--budget") + 1]) if "--budget" in args else 6
    quiet = "--quiet" in args

    snaps = snapshots()
    if not snaps:
        print("no observations to replay")
        return 1
    t0 = datetime.fromisoformat(snaps[0]["ts"])
    t1 = datetime.fromisoformat(snaps[-1]["ts"])
    hours = (t1 - t0).total_seconds() / 3600.0

    def one_pass(agent, learn, show):
        """Run the window once. Returns (raw, shown, tapped)."""
        w = Watcher()
        agent.shown = []
        agent.suppressed = 0
        agent.recent = []
        raw = shown = acted = 0
        for s in snaps:
            now = datetime.fromisoformat(s["ts"])
            evs = w.update(s)
            raw += len(evs)
            card = agent.choose(evs, now=now, seen_per_hour=raw / max(hours, 1e-9))
            if not card:
                continue
            shown += 1
            hit = taps(card)
            acted += 1 if hit else 0
            if learn:
                agent.on_action(card, hit)
            if show and shown <= 8:
                print(f"  {now:%H:%M}  {card.headline():26} {card.detail()}")
                print(f"           score {card.score:.2f}  "
                      f"why: {', '.join(card.why)}   "
                      f"{'TAP' if hit else 'ignored'}")
        return raw, shown, acted

    # Baseline: hand-written priors, frozen. No learning at all. This is what
    # the agent would be if it never adapted - the control group.
    base = EventAgent(budget_per_hour=budget)
    raw0, shown0, act0 = one_pass(base, learn=False, show=False)

    agent = EventAgent(budget_per_hour=budget)

    # Pass 1: cold agent, learning from every tap.
    if not quiet:
        print("PASS 1 - cold agent, learning\n")
    raw1, shown1, act1 = one_pass(agent, learn=True, show=not quiet)

    # Pass 2: same window, weights carried over, no further learning. This is
    # "what the second day looks like" - the same events, judged by an agent
    # that has already seen you react once.
    if not quiet:
        print("\nPASS 2 - same window, trained agent\n")
    raw2, shown2, act2 = one_pass(agent, learn=False, show=not quiet)

    print()
    print("=" * 64)
    print(f"window              {hours:.1f}h, {len(snaps)} snapshots, "
          f"{len({s['index'] for s in snaps})} markets")
    print(f"candidate events    {raw1}  ({raw1/hours:.1f}/hour)")
    print()
    print(f"{'':20}{'cards':>8}{'/hour':>8}{'relevant':>11}")
    for lab, sh, ac in (("untrained (control)", shown0, act0),
                        ("learning", shown1, act1),
                        ("trained", shown2, act2)):
        rate = f"{ac/sh*100:.0f}%" if sh else "-"
        print(f"  {lab:18}{sh:>8}{sh/hours:>8.1f}{rate:>11}")
    if shown0 and shown2:
        d = (act2 / shown2 - act0 / shown0) * 100
        print(f"\nrelevance gain from learning   {d:+.0f} points"
              f"  ({act0/shown0*100:.0f}% -> {act2/shown2*100:.0f}%)")
        print(f"interruptions                  {shown0} -> {shown2}"
              f"  ({(shown2-shown0)/shown0*100:+.0f}%)")

    print()
    print("learned weights:")
    for k, v in sorted(agent.pref.w.items(), key=lambda kv: -abs(kv[1])):
        bar = "#" * min(26, int(abs(v) * 10))
        print(f"  {k:14} {v:+6.3f} {bar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
