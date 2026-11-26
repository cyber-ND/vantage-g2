"""One pass of the agent loop against Hyperliquid testnet.

PERCEIVE  pull outcomeMeta + allMids, keep markets with a live two-sided book
DECIDE    derive a no-arbitrage fair from the complementary side, score, rank
ACT       print the single card the glasses would show (placing orders is
          a separate, signed step - this module never writes)
LEARN     persisted in agent/state.json by policy.Agent

Fair value, for the spike:
  A binary pair must satisfy  Yes + No = 1  at settlement. So buying No at
  no_ask is economically selling Yes at (1 - no_ask). If yes_ask is below
  that, the two books disagree and the cheaper side carries real edge.
  No external model needed - the venue prices both legs itself.

Usage:
  python3 -I agent/scan.py [--limit 60] [--mainnet] [--json]
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, __import__("os").path.dirname(__file__))

import hl  # noqa: E402
from fair import Pricer  # noqa: E402
from policy import Agent  # noqa: E402

STATE = __import__("os").path.join(__import__("os").path.dirname(__file__), "state.json")


def live_pool(markets, live, limit):
    """Markets with a genuine quote on both legs.

    allMids returns a 0.5 placeholder for every listed outcome, quoted or not,
    so 'mid == 0.5 exactly' is the tell for an empty book. Filtering on it is
    the difference between scanning real markets and scanning dead ones.
    """
    pool = [
        m
        for m in markets
        if m["yes_coin"] in live
        and m["no_coin"] in live
        and abs(live[m["yes_coin"]] - 0.5) > 1e-9
    ]
    # Then prefer markets NEAR the money. The volatility model is only valid
    # within MAX_SIGMA of spot, which in price terms is the middle of the
    # book - so scanning the extremes first wastes the whole budget on
    # markets the model will refuse to price.
    pool.sort(key=lambda m: abs(live[m["yes_coin"]] - 0.5))
    return pool[:limit]


def _books(args):
    m, url = args
    try:
        ybid, yask, _, yasz = hl.l2(m["yes_coin"], url)
        nbid, nask, _, nasz = hl.l2(m["no_coin"], url)
    except RuntimeError:
        return None
    return m, ybid, yask, yasz, nbid, nask, nasz


def run(limit=60, url=hl.MAINNET, workers=8, use_model=True):
    """PERCEIVE on `url` (mainnet by default - that is where the liquidity is).

    Fair value comes from two sources, in order of preference:
      1. the independent volatility model (fair.Pricer) for price markets
      2. the no-arbitrage complement of the opposite book, for everything else

    Only (1) can actually find mispricing. (2) only ever detects a crossed
    book, which on this venue does not happen.
    """
    agent = Agent(state_path=STATE)
    markets = hl.load_markets(url)
    live = hl.mids(url)
    pool = live_pool(markets, live, limit)
    pricer = Pricer(url) if use_model else None

    candidates, looked, modelled = [], 0, 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(_books, [(m, url) for m in pool]):
            if not res:
                continue
            m, ybid, yask, yasz, nbid, nask, nasz = res
            looked += 1

            model = pricer.fair(m) if pricer else None
            if model:
                modelled += 1
                lo, mid, hi, meth = model
                # conservative: buying Yes is judged against the LOW end of the
                # band, buying No against the high end. An edge has to survive
                # the model's own uncertainty before it is shown to anyone.
                fair_yes, fair_no, src = lo, 1.0 - hi, meth
            else:
                fair_yes = (1.0 - nask) if nask is not None else None
                fair_no = (1.0 - yask) if yask is not None else None
                src = "book-complement"

            if fair_yes is not None:
                c = agent.consider(m, "Yes", yask, ybid, yasz, fair_yes)
                if c:
                    c["src"] = src
                    candidates.append(c)
            if fair_no is not None:
                c = agent.consider(m, "No", nask, nbid, nasz, fair_no)
                if c:
                    c["src"] = src
                    candidates.append(c)

    return agent, agent.rank(candidates), looked, len(pool), modelled


def card_text(c):
    """What the 576x288 display actually gets: one line, a few words."""
    lab = (c["label"] or f"#{c['index']}")[:22]
    return (
        f"{lab}  {c['side']} {c['price']:.2f}",
        f"fair {c['fair']:.2f}  edge +{c['edge']*100:.1f}c  [{c.get('src','?')}]",
        f"depth {c['depth']:.0f}  why: {', '.join(c['why'])}",
    )


def main():
    args = sys.argv[1:]
    url = hl.TESTNET if "--testnet" in args else hl.MAINNET
    limit = 60
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])

    t0 = time.time()
    agent, ranked, looked, pool, modelled = run(limit=limit, url=url)
    dt = time.time() - t0

    if "--json" in args:
        print(json.dumps({"ranked": ranked[:10], "looked": looked, "secs": round(dt, 1)},
                         indent=2, default=str))
        return

    net = "MAINNET" if url == hl.MAINNET else "TESTNET"
    print(f"[{net}] scanned {looked}/{pool} two-sided markets in {dt:.1f}s"
          f"  ({modelled} priced by the vol model)")
    print(f"candidates past the gates: {len(ranked)}\n")
    for c in ranked[:5]:
        a, b, d = card_text(c)
        print(f"  {a}\n  {b}\n  {d}\n")
    if not ranked:
        print("  (nothing cleared the edge/depth gates - the agent shows nothing)\n")
    print("agent state:", json.dumps(agent.report(), indent=2))


if __name__ == "__main__":
    main()
