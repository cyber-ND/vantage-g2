"""The backend the glasses talk to.

One process: a poller thread that watches Hyperliquid and decides, plus a
tiny HTTP server that hands the current card to the WebView and takes the
tap back.

Why a backend at all - the Even Hub rules require it. External calls must be
whitelisted and pass CORS, and API keys must never ship inside the app. The
glasses app here is deliberately dumb: it renders one card and reports one
tap. Every decision lives on this side.

Endpoints
  GET  /            the glasses app itself (so one process serves everything)
  GET  /card        {"card": {...}} or {"card": null}
  POST /act         {"action": "accept" | "dismiss"}  -> learns, clears card
  GET  /state       agent weights and counters, for the demo overlay

Usage:
  python3 -I agent/server.py [--port 8787] [--interval 60] [--budget 6]
"""
import base64
import binascii
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(__file__))

import fair  # noqa: E402
import hl  # noqa: E402
import voice as voicemod  # noqa: E402
from events import EventAgent, Watcher  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WEBDIR = os.path.join(os.path.dirname(HERE), "web")
WEB = os.path.join(WEBDIR, "index.html")
WEIGHTS = os.path.join(HERE, "event_weights.json")

STATE = {
    "card": None,         # the one card on screen
    "event": None,        # the Event object behind it (not serialised)
    "polls": 0,
    "events_seen": 0,
    "cards": 0,
    "last_poll": None,
    "error": None,
}
LOCK = threading.Lock()


def load_weights():
    if os.path.exists(WEIGHTS):
        try:
            return json.load(open(WEIGHTS))
        except (ValueError, OSError):
            pass
    return None


def save_weights(agent):
    try:
        json.dump(agent.pref.w, open(WEIGHTS, "w"), indent=2)
    except OSError:
        pass


def snapshot_of(m, yb, ya):
    return {
        "index": m["index"],
        "coin": fair.underlying_of(m) or "",
        "label": (fair.underlying_of(m) or f"#{m['index']}"),
        "kind": m["kind"],
        "mid": (yb + ya) / 2.0,
        "ttl_h": None,  # filled by caller
    }


def publish(evs, agent, now, seen_rate):
    """DECIDE: hand the agent the events and put at most one card on glass."""
    with LOCK:
        STATE["polls"] += 1
        STATE["events_seen"] += len(evs)
        STATE["last_poll"] = now.isoformat()
        STATE["error"] = None
        # Do not replace a card the wearer has not answered yet - swapping it
        # out mid-glance is how you make a display impossible to trust.
        if STATE["card"] is not None:
            return
        card = agent.choose(evs, now=now, seen_per_hour=seen_rate)
        if not card:
            return
        STATE["event"] = card
        STATE["card"] = {
            "label": card.label,
            "mid": card.mid,
            "detail": card.detail(),
            "kind": card.kind,
            "why": card.why,
            "score": round(card.score, 3),
            "index": card.index,
        }
        STATE["cards"] += 1
        print(f"{now:%H:%M:%S}  CARD  {card.headline()}  {card.detail()}  "
              f"(score {card.score:.2f})", flush=True)


def replayer(agent, watcher, speed=600.0):
    """Feed recorded market snapshots through the live decision path.

    Same Watcher, same EventAgent, same publish() as the mainnet poller - the
    only difference is where the ticks come from and how fast the clock runs.

    This exists because the agent is *supposed* to be quiet: in one recent
    24h stretch it judged 465 polls worth of market activity and raised a
    single card. That is the product working, and it is also impossible to
    film. Replay compresses the recorded window so a demo can show the whole
    perceive -> decide -> learn cycle in a couple of minutes.

    Nothing here is synthetic. Every snapshot is a real quote collected from
    Hyperliquid mainnet by agent/collect.py, replayed in its original order.
    """
    import replay as replaymod

    snaps = replaymod.snapshots()
    if not snaps:
        print("replay: no observations to replay", flush=True)
        return
    t0 = datetime.fromisoformat(snaps[0]["ts"])
    span = (datetime.fromisoformat(snaps[-1]["ts"]) - t0).total_seconds()
    print(f"replay: {len(snaps)} recorded snapshots over {span/3600:.1f}h, "
          f"{speed:.0f}x", flush=True)

    raw = 0
    started = time.time()
    for s in snaps:
        now = datetime.fromisoformat(s["ts"])
        # keep the compressed clock honest rather than sleeping a fixed step
        due = (now - t0).total_seconds() / speed
        behind = due - (time.time() - started)
        if behind > 0:
            time.sleep(min(behind, 5.0))
        # hold the window while a card is unanswered, so the wearer is never
        # racing the replay to tap something
        while True:
            with LOCK:
                if STATE["card"] is None:
                    break
            time.sleep(0.25)
            started += 0.25
        evs = watcher.update(s)
        raw += len(evs)
        hours = max((now - t0).total_seconds() / 3600.0, 1e-9)
        publish(evs, agent, now, raw / hours)
    print("replay: window exhausted", flush=True)


def poller(url, interval, agent, watcher):
    """PERCEIVE + DECIDE, forever."""
    meta_at, markets = 0.0, []
    while True:
        try:
            now = datetime.now(timezone.utc)
            if time.time() - meta_at > 300:
                markets = hl.load_markets(url)
                meta_at = time.time()

            live = []
            for m in markets:
                exp = m.get("expiry")
                kind = m.get("kind") or ""
                if not exp or not kind.startswith(("binaryPrice", "priceTouch")):
                    continue
                ttl_h = (exp - now).total_seconds() / 3600.0
                if 0 < ttl_h <= fair.MAX_TTL_HOURS:
                    live.append((m, ttl_h))

            evs = []
            for m, ttl_h in live:
                try:
                    yb, ya, _, _ = hl.l2(m["yes_coin"], url)
                except RuntimeError:
                    continue
                if yb is None or ya is None:
                    continue
                snap = snapshot_of(m, yb, ya)
                snap["ttl_h"] = ttl_h
                evs.extend(watcher.update(snap))

            seen_rate = len(live) * (3600.0 / max(interval, 1))
            publish(evs, agent, now, seen_rate)
        except Exception as e:  # noqa: BLE001 - the poller must never die
            with LOCK:
                STATE["error"] = str(e)
            print(f"poll failed: {e}", flush=True)
        time.sleep(interval)


class Handler(BaseHTTPRequestHandler):
    agent = None

    def log_message(self, *a):
        pass  # the poller's prints are the interesting output

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            path = "/index.html"
        # Serve anything under web/ - the page is an ES module now and pulls in
        # glasses.js and the vendored SDK.
        if path.startswith("/") and ".." not in path:
            fname = path.lstrip("/")
            full = os.path.normpath(os.path.join(WEBDIR, fname))
            if full.startswith(WEBDIR) and os.path.isfile(full):
                ctype = ("text/html; charset=utf-8" if full.endswith(".html")
                         else "text/javascript; charset=utf-8" if full.endswith(".js")
                         else "application/octet-stream")
                with open(full, "rb") as fh:
                    return self._send(200, fh.read(), ctype)
        if path == "/card":
            with LOCK:
                return self._send(200, {"card": STATE["card"]})
        if path == "/state":
            with LOCK:
                s = {k: v for k, v in STATE.items() if k != "event"}
            s["agent"] = self.agent.report()
            return self._send(200, s)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        route = self.path.split("?")[0]
        if route == "/voice":
            return self._voice()
        if route != "/act":
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            body = {}
        action = body.get("action", "dismiss")
        with LOCK:
            ev = STATE["event"]
            STATE["card"] = None
            STATE["event"] = None
        if ev is not None:
            self.agent.on_action(ev, action == "accept")
            save_weights(self.agent)
            print(f"  tap: {action}  ->  {ev.kind} {ev.label}  "
                  f"(updates {self.agent.pref.n})", flush=True)
        return self._send(200, {"ok": True, "action": action})


    def _voice(self):
        """Raw PCM in, a short confirmation line out.

        Audio never leaves the machine: whisper.cpp runs locally. That is
        both the hackathon's "bring your own STT" instruction and the honest
        reading of its privacy rule - a face-worn microphone should not be
        shipping audio to a third party.
        """
        n = int(self.headers.get("Content-Length") or 0)
        if n > 8 * 1024 * 1024:
            return self._send(413, {"error": "audio too large"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            pcm = base64.b64decode(body.get("pcm") or "")
        except (ValueError, binascii.Error):
            return self._send(400, {"error": "bad audio"})

        rate = int(body.get("rate") or voicemod.DEFAULT_RATE)
        said, err = voicemod.transcribe(pcm, rate=rate)
        if err:
            print(f"  voice: {err}", flush=True)
            return self._send(200, {"reply": "voice unavailable", "error": err})

        intent = voicemod.parse(said)
        with LOCK:
            card = STATE["card"]
        reply = voicemod.apply(intent, self.agent, current_card=card)

        if intent.get("action") == "dismiss":
            with LOCK:
                ev, STATE["card"], STATE["event"] = STATE["event"], None, None
            if ev is not None:
                self.agent.on_action(ev, False)
        save_weights(self.agent)
        print(f"  voice: {said!r} -> {intent.get('action')} "
              f"{intent.get('subject') or ''} -> {reply}", flush=True)
        return self._send(200, {"reply": reply, "said": said,
                                "action": intent.get("action"),
                                "subject": intent.get("subject")})


def main():
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1]) if "--port" in args else 8787
    interval = int(args[args.index("--interval") + 1]) if "--interval" in args else 60
    budget = int(args[args.index("--budget") + 1]) if "--budget" in args else 6

    replay_speed = (float(args[args.index("--replay") + 1])
                    if "--replay" in args else None)

    agent = EventAgent(weights=load_weights(), budget_per_hour=budget)
    watcher = Watcher()
    Handler.agent = agent

    if replay_speed:
        t = threading.Thread(target=replayer,
                             args=(agent, watcher, replay_speed), daemon=True)
    else:
        t = threading.Thread(target=poller,
                             args=(hl.MAINNET, interval, agent, watcher),
                             daemon=True)
    t.start()

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"vantage backend on http://127.0.0.1:{port}", flush=True)
    print(f"  glasses app  http://127.0.0.1:{port}/", flush=True)
    print(f"  polling every {interval}s, budget {budget} cards/hour", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
