"""Hyperliquid HIP-4 outcome-market data layer (testnet by default).

Read-only public /info endpoints. No API key, no wallet needed to read.
Stdlib only, same house style as the polymarket-* research scripts.

Coin naming: an outcome market with index N exposes two books,
    Yes -> "#<N*10 + 0>"      No -> "#<N*10 + 1>"
verified empirically by verify_coin_mapping().
"""
import json
import time
import urllib.request
from datetime import datetime, timezone

TESTNET = "https://api.hyperliquid-testnet.xyz/info"
MAINNET = "https://api.hyperliquid.xyz/info"

UA = {"User-Agent": "research-script/1.0", "Content-Type": "application/json"}


def info(body, url=TESTNET, retries=3, timeout=30):
    """POST one /info request."""
    data = json.dumps(body).encode()
    err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=UA, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(1 + 2 * i)
    raise RuntimeError(f"info {body.get('type')} failed: {err}")


# ------------------------------------------------------------------ coins
def coins_for(outcome_index):
    """(yes_coin, no_coin) for an outcome index."""
    return f"#{outcome_index * 10}", f"#{outcome_index * 10 + 1}"


def verify_coin_mapping(url=TESTNET, sample=5):
    """Check the *10/+1 rule against live allMids. Returns (ok, checked, misses)."""
    mids = info({"type": "allMids"}, url)
    meta = info({"type": "outcomeMeta"}, url)
    checked = misses = 0
    for o in meta["outcomes"][: sample * 40]:
        y, n = coins_for(o["outcome"])
        if y in mids or n in mids:
            checked += 1
            if not (y in mids and n in mids):
                misses += 1
        if checked >= sample * 8:
            break
    return (checked > 0 and misses == 0), checked, misses


# ------------------------------------------------------------- descriptions
def parse_desc(desc):
    """'a:1|b:2' -> {'a':'1','b':'2'}; tolerates values containing ':'."""
    out = {}
    for part in (desc or "").split("|"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def parse_time(s):
    """'20261120-2100' -> aware UTC datetime, or None."""
    try:
        return datetime.strptime(s, "%Y%m%d-%H%M").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def kind_of(name):
    """'template:binaryPrice6' -> 'binaryPrice' (strip template: and trailing digits)."""
    n = (name or "").replace("template:", "")
    return n.rstrip("0123456789") or n


def load_markets(url=TESTNET):
    """All outcome markets as plain dicts, newest metadata from the venue."""
    meta = info({"type": "outcomeMeta"}, url)
    out = []
    for o in meta["outcomes"]:
        d = parse_desc(o.get("description"))
        yes_coin, no_coin = coins_for(o["outcome"])
        out.append(
            {
                "index": o["outcome"],
                "kind": kind_of(o.get("name")),
                "venue": o.get("venue"),
                "quote": o.get("quoteToken"),
                "yes_coin": yes_coin,
                "no_coin": no_coin,
                "label": d.get("shortName") or d.get("participant") or d.get("perp") or "",
                "threshold": _num(d.get("threshold")),
                "expiry": parse_time(d.get("time")),
                "window_s": _num(d.get("seconds")),
                "desc": o.get("description") or "",
                "fields": d,
            }
        )
    return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# -------------------------------------------------------------------- books
def l2(coin, url=TESTNET):
    """Top of book: (best_bid, best_ask, bid_sz, ask_sz) as floats, Nones if empty."""
    b = info({"type": "l2Book", "coin": coin}, url)
    levels = b.get("levels") or [[], []]
    bids, asks = (levels + [[], []])[:2]
    bb = float(bids[0]["px"]) if bids else None
    ba = float(asks[0]["px"]) if asks else None
    bs = float(bids[0]["sz"]) if bids else 0.0
    as_ = float(asks[0]["sz"]) if asks else 0.0
    return bb, ba, bs, as_


def depth_within(coin, limit_px, side, url=TESTNET):
    """Contracts available at or better than limit_px. side='buy' walks asks."""
    b = info({"type": "l2Book", "coin": coin}, url)
    levels = b.get("levels") or [[], []]
    bids, asks = (levels + [[], []])[:2]
    book = asks if side == "buy" else bids
    total = 0.0
    for lv in book:
        px = float(lv["px"])
        if (side == "buy" and px <= limit_px) or (side == "sell" and px >= limit_px):
            total += float(lv["sz"])
        else:
            break
    return total


def mids(url=TESTNET):
    """allMids filtered to outcome books."""
    return {k: float(v) for k, v in info({"type": "allMids"}, url).items() if k.startswith("#")}


def settled(outcome_index, url=TESTNET):
    """Settlement record for one outcome, or None if not settled / unsupported."""
    try:
        return info({"type": "settledOutcome", "outcome": outcome_index}, url)
    except RuntimeError:
        return None
