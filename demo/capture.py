"""Record the glasses display while a scripted demo drives the real app.

This is a screen recording, not a mock-up: every frame is pulled from the
simulator's own framebuffer through /api/screenshot/glasses while the agent
runs, decides and learns. Input arrives through the simulator's touchpad API,
and voice goes in acoustically - `say` out of the speakers, into the mic the
simulator is listening on - so the microphone path in the video is the same
one a wearer would use.

The backend must be running in replay mode, because the agent is supposed to
be quiet: in one recent 24h stretch it judged 465 polls worth of market
activity and raised a single card. Replay feeds it recorded Hyperliquid
quotes on a compressed clock so the whole cycle fits in a demo.

  python3 -I agent/server.py --port 8787 --budget 6 --replay 900 &
  evenhub-simulator --automation-port 9898 http://127.0.0.1:8787/ &
  python3 -I demo/capture.py

Writes frames/*.png, times.json and captions.json into demo/recording/.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

SIM = os.environ.get("VANTAGE_SIM", "http://127.0.0.1:9898")
API = os.environ.get("VANTAGE_API", "http://127.0.0.1:8787")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recording")
FRAMES = os.path.join(OUT, "frames")
FPS = 5.0

T0 = None
CAPTIONS = []       # (start_seconds, text)
_stop = threading.Event()


# ------------------------------------------------------------------ plumbing
def get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def post(url, payload, timeout=10):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def now():
    return time.time() - T0


def say(text):
    """A caption on the finished video, timestamped against the recording."""
    CAPTIONS.append((round(now(), 2), text))
    print(f"  {now():6.1f}s  | {text}", flush=True)


def recorder():
    """Pull the framebuffer at FPS until told to stop."""
    i = 0
    nxt = time.time()
    times = []
    while not _stop.is_set():
        try:
            png = get(f"{SIM}/api/screenshot/glasses", timeout=5)
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
            continue
        name = f"{i:05d}.png"
        with open(os.path.join(FRAMES, name), "wb") as fh:
            fh.write(png)
        times.append({"file": name, "t": round(now(), 3)})
        i += 1
        nxt += 1.0 / FPS
        time.sleep(max(0.0, nxt - time.time()))
    with open(os.path.join(OUT, "times.json"), "w") as fh:
        json.dump(times, fh)
    print(f"\n{i} frames over {now():.1f}s", flush=True)


# -------------------------------------------------------------------- actions
def tap(action):
    try:
        post(f"{SIM}/api/input", {"action": action})
    except (urllib.error.URLError, OSError) as e:
        print(f"  input {action} failed: {e}", flush=True)


def card():
    try:
        return json.loads(get(f"{API}/card"))["card"]
    except (urllib.error.URLError, OSError, ValueError):
        return None


def state():
    try:
        return json.loads(get(f"{API}/state"))
    except (urllib.error.URLError, OSError, ValueError):
        return {}


def wait_card(timeout=90):
    """Block until the agent decides something is worth showing."""
    end = time.time() + timeout
    while time.time() < end:
        c = card()
        if c:
            return c
        time.sleep(0.3)
    return None


def speak(phrase, settle=8.0):
    """Double-tap, then talk at the microphone the simulator is listening on.

    The audio is rendered to a file first and then played, rather than piping
    `say` straight to the speakers. `say` takes half a second to start making
    sound, and the capture endpoints on 900ms of silence - so spoken live it
    loses a race against its own startup and the backend receives an empty
    clip. afplay starts immediately.
    """
    wav = os.path.join(OUT, "phrase.aiff")
    subprocess.run(["say", "-r", "150", "-o", wav, phrase], check=False)
    tap("double_click")
    time.sleep(0.2)
    subprocess.run(["afplay", wav], check=False)
    time.sleep(settle)


def volume(level):
    subprocess.run(["osascript", "-e", f"set volume output volume {level}"],
                   check=False, capture_output=True)


def current_volume():
    r = subprocess.run(
        ["osascript", "-e", "output volume of (get volume settings)"],
        capture_output=True, text=True, check=False)
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


# ----------------------------------------------------------------- the script
def script():
    """The wearer in this demo holds BTC and ignores everything else.

    Nothing in events.py names BTC. If the weights separate coin:BTC from
    coin:ETH by the end, the agent worked that out from the taps.
    """
    say("Idle. The agent is watching, and has nothing worth saying.")
    time.sleep(4)
    say("The scarce resource here is not information. It is interruption.")
    time.sleep(4)

    say("Live Hyperliquid outcome markets, recorded and replayed at 900x")
    time.sleep(1)
    say("This wearer holds BTC. Tap what matters, hold what does not.")

    for _ in range(3):
        c = wait_card(timeout=75)
        if not c:
            break
        mine = c["label"] == "BTC"
        time.sleep(1.5)   # let the glasses catch up before narrating them
        say(f"{c['label']}  {c['detail']}   "
            + ("- mine, so tap" if mine else "- not mine, so hold"))
        time.sleep(3.5)
        if mine:
            tap("click")
        else:
            tap("long_press")
            time.sleep(0.4)
            tap("long_press_release")
        time.sleep(3)

    say("Every one of those is a training label. Nothing in the code says BTC.")
    time.sleep(4.5)

    wait_card(timeout=60)
    say("Double-tap to talk. Speech-to-text is whisper.cpp, running locally.")
    time.sleep(3.5)
    speak("Watch bitcoin")
    say("No key, no account, no audio leaving the machine.")
    time.sleep(4)

    say("Voice trains the same model the taps do, weighted higher.")
    time.sleep(4)
    speak("Fewer interruptions")
    say("The wearer sets the interruption budget by saying so.")
    time.sleep(4)

    st = state().get("agent", {})
    w = st.get("weights", {})
    coins = {k: v for k, v in w.items() if k.startswith("coin:")}
    if coins:
        top = sorted(coins.items(), key=lambda kv: -abs(kv[1]))[:3]
        say("Learned this session:  "
            + ",   ".join(f"{k.split(':')[1]} {v:+.2f}" for k, v in top))
        time.sleep(5)
    say("The agent found the preference. It was never told.")
    time.sleep(4.5)


def main():
    global T0
    os.makedirs(FRAMES, exist_ok=True)
    for f in os.listdir(FRAMES):
        os.remove(os.path.join(FRAMES, f))

    try:
        get(f"{SIM}/api/ping", timeout=4)
    except (urllib.error.URLError, OSError):
        print(f"simulator not reachable at {SIM}")
        return 1
    if not state():
        print(f"backend not reachable at {API}")
        return 1

    # start from an idle display: a card left over from a previous run would
    # make the opening seconds a lie
    if card():
        post(f"{API}/act", {"action": "dismiss"})
        time.sleep(1.0)

    vol = current_volume()
    volume(85)
    T0 = time.time()
    rec = threading.Thread(target=recorder, daemon=True)
    rec.start()
    try:
        script()
    finally:
        _stop.set()
        rec.join(timeout=10)
        if vol is not None:
            volume(vol)
        with open(os.path.join(OUT, "captions.json"), "w") as fh:
            json.dump(CAPTIONS, fh, indent=1)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
