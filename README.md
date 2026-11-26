# Vantage

An agent that watches prediction markets and interrupts you only when it has
something worth a glance. Built for Even Realities G2.

Track 02 · Crypto & DeFi. Venue: Hyperliquid HIP-4 outcome markets.

## What it is

Not a ticker. The six crypto apps already in the Even Hub catalogue all show
you prices; none of them decides anything.

Vantage decides **what is worth interrupting you for**. On a 576x288 display
worn on your face, the scarce resource is not information — it is
interruption. The agent spends a budget of interruptions and learns from your
taps whether it spent them well.

```
PERCEIVE   Hyperliquid mainnet /info - markets inside the last hour of life
DETECT     resolving / flip / decided, each confirmed before it counts
DECIDE     rank by learned preference, against a self-tuning bar and a budget
LEARN      the tap is the label: what you open, and what you let pass
```

It does **not** claim to price markets better than the market. It tried, and
measurement said it could not — see "Forward result" below. That finding is
what the design is built on: if the book is well calibrated, then the book's
price is good truth, and the open problem is attention, not alpha.

### Measured, on real collected data

`python3 -I agent/replay.py` replays the whole collection window:

| | cards | per hour | relevant |
|---|---|---|---|
| untrained (frozen priors, control) | 197 | 1.6 | 53% |
| trained on one pass of taps | 197 | 1.6 | **57%** |

**+4 points of relevance from learning**, at the same interruption cost, over
1,100 real candidate events. The simulated wearer holds BTC and ignores
everything else; nothing in `events.py` names BTC, and the agent still
separates `coin:BTC` from `coin:ETH` and `coin:HYPE` from taps alone.

> Measured on the first 13.7 hours this read **+19 points (45% → 64%)** over
> 177 events. Five days of data cut it to +4. Same lesson as the P&L section:
> the first number was small-sample flattery. +4 points is the one to quote,
> and it is real — the frozen-priors control rules out the detectors and the
> budget taking credit for it.

### Two things that had to be true

**Confirmation.** Median one-minute move in these markets is 6.4 points and
37% of ticks move 10+. A naive "big move" alert fires 53 times an hour and is
worthless. Every event type is confirmed on a following tick before it counts,
and each fires once per market.

**A self-tuning bar.** A fixed score cutoff does not work: the model happily
learns you ignore ETH, but a slightly-more-interesting ETH card still clears a
constant bar and still interrupts you. So the threshold is the quantile of
recent candidate scores matching the budget — the agent decides how picky to
be, not just what it likes. Adding that alone moved relevance 48% -> 64%.

## Why this venue

HIP-4 outcome markets settle to exactly 1 or 0. That makes ground truth free
and automatic - the agent grades its own past decisions without anyone
labelling anything. Very little else on a smart-glasses platform has that.

Read and write are split across networks on purpose:

| | network | why |
|---|---|---|
| perceive / learn | **mainnet**, read-only | that is where the liquidity is |
| act | **testnet**, signed | hackathon rules: no real money, ever |

Measured 2026-11-18: mainnet carries 309 outcome markets, 266 with a quote off
the 0.5 placeholder, real two-sided books (NFL, Premier League, IPO timing, Fed
policy rates, BTC/SP500 price thresholds). Testnet carries 694 markets but the
books are almost entirely empty, so it is an execution sandbox, not a data
source. Both facts are reproducible with `agent/scan.py`.

## The two learning loops

They are separate on purpose, because they answer different questions and fail
in different ways.

**1. Calibration — "is my probability any good?"**
Bucket by quoted price, compare to realised settlement rate, correct the quote.
Scored by Brier. The correction is shrunk by its own standard error, so a
bucket that only *looks* biased because it is small gets ignored. (Without that
shrinkage the calibrator made Brier *worse* on well-priced buckets — the first
version did exactly that, see `learn_demo.py`.)

**2. Preference — "does this user care?"**
Online logistic regression over the same feature vector, labelled by accept /
dismiss on the glasses. Nothing in the code names a category; the weights find
it. In `learn_demo.py` a synthetic user who only taps sports drives
`is_sports` to +2.7 and `is_price` to -2.7 in 400 taps, at 95% online accuracy.

Run the proof:

```bash
python3 -I agent/learn_demo.py
```

## Run it

One process serves the glasses app and runs the agent:

```bash
python3 -I agent/server.py --port 8787 --interval 45 --budget 6
```

Then open the app, or point the simulator at it:

```bash
evenhub-simulator http://localhost:8787/
```

Replay the learning curve on collected data, no network needed:

```bash
python3 -I agent/replay.py
```

### Demo mode

The agent is *supposed* to be quiet. In one recent 24-hour stretch it judged
465 polls worth of market activity and raised a single card — which is the
product working, and is also impossible to film. So the server can take its
ticks from the recorded window instead of from the network, on a compressed
clock:

```bash
python3 -I agent/server.py --port 8787 --budget 6 --replay 900
```

Nothing about that is synthetic. Every snapshot is a real quote collected
from mainnet by `collect.py`, replayed in its original order through the same
`Watcher`, the same `EventAgent` and the same `publish()` the live poller
uses. Only the clock changes — and the feeder pauses whenever a card is
unanswered, so the wearer is never racing the replay.

`demo/vantage-demo.mp4` is recorded from that, frame by frame, straight out
of the simulator's framebuffer:

```bash
python3 -I demo/capture.py && python3 demo/render.py
```

`narrate.py` lays a voiceover over the result, timed against the video's own
clock rather than the captions — a line may run across a caption boundary, it
just may not run into the next line, and `--script` prints the timings so the
narration can be re-recorded in a human voice instead.

`capture.py` drives the real app through the simulator's touchpad API and
speaks the voice commands out of the Mac's speakers into the microphone the
simulator is listening on, so the microphone path in the video is the one a
wearer would use. `render.py` composites the captured frames — they are pure
green with the glyphs in the alpha channel, so over black they are exactly
what the wearer sees — and adds the captions.

### Backend endpoints

| | |
|---|---|
| `GET /` | the glasses app |
| `GET /card` | `{"card": {...}}` or `{"card": null}` |
| `POST /act` | `{"action": "accept"\|"dismiss"}` — learns, clears the card |
| `GET /state` | weights and counters, for a demo overlay |

The WebView is deliberately dumb: it renders one card and reports one tap.
Every decision lives in the backend, which is also what the Even Hub rules
require — external calls whitelisted in `app.json`, CORS honoured, and no key
ever shipped inside the app.

### The G2 does not render your DOM

Worth knowing before you design anything. The glasses framebuffer is driven
**entirely by Even Hub SDK containers**. Your HTML and CSS run in a WebView
the wearer never looks at.

This is invisible in a browser and invisible in the console. It showed up
only against the simulator's automation API:

```
GET /api/screenshot/webview   ->  the card, rendered perfectly
GET /api/screenshot/glasses   ->  blank, 576x288
```

So `web/glasses.js` draws the card as three `TextContainer`s positioned for
576x288 and updates them with `textContainerUpgrade` — never
`rebuildPageContainer`, which flashes the display. Input arrives through
`onEvenHubEvent`: `CLICK_EVENT` is accept, `LONG_PRESS_EVENT` is dismiss, and
only the headline container sets `isEventCapture: 1` (the SDK drops taps when
nothing is listening).

The SDK ships as a self-contained ES module, so it is vendored to
`web/even_hub_sdk.js` and imported directly — no bundler in this project.

### There is no font size

`TextContainerProperty` exposes position, size, padding, border, zOrder and
`textColor` (brightness 0–4). That is the whole typographic toolkit. Measured
with `@evenrealities/pretext`, `"BTC  51%"` is **76px wide on a 576px
screen** and nothing can make it bigger. Line height is 27px.

So hierarchy comes from placement, vertical space and brightness: headline at
y=92 brightness 4, detail at y=134 brightness 3, hint pinned to y=236 at
brightness 1 so it recedes. The block sits above true centre, because on a
heads-up display text centred vertically reads as low.

(An `ImageContainer` would allow real type, but PB caps it at 288×144 — half
the screen — and needs grayscale raster. A possible upgrade, not done here.)

### Centring without flashing

`textContainerUpgrade` carries content and `textColor` only — **not
position**. So a card whose text changes width cannot be re-centred by moving
its container; that needs `rebuildPageContainer`, which flashes the display.

Instead every container spans the full width and centring is done with
leading spaces. A space is exactly 5px, so this lands within ~2px of true
centre and never flashes. `pxTruncate` from pretext handles overflow, since
hand-rolled slicing gets kerning wrong at the cut.

### A tap is signalled by absence

The most important input on the device arrives with **no `eventType` field**.
`CLICK_EVENT` is `0`, and protobuf JSON omits zero-valued enums. Verified
against the simulator:

```
click              -> {"eventSource":1}                 <- no eventType
double_click       -> {"eventType":3,"eventSource":1}
long_press         -> {"eventType":9}
long_press_release -> {"eventType":10}
```

Reading `eventType` and bailing when it is missing — the obvious
implementation, and the one written here first — silently breaks tap while
long-press keeps working. `LONG_PRESS_RELEASE` is ignored on purpose, or one
gesture fires the handler twice.

The WebView polls `/card` once a second, not every four. The gap between the
agent deciding and the wearer seeing it is dead time they cannot act in, and
a card on a market that settles in five minutes cannot afford to spend four
seconds of it waiting for the next poll. (At four seconds this was also
visible in the demo recording: the narration described a card the display had
not drawn yet.)

One behaviour worth naming: the poller will **not** replace a card the wearer
has not answered yet. Swapping the display mid-glance is how you make a
heads-up device impossible to trust.

## The fair-value model

`agent/fair.py` is the independent opinion. Without it the agent has nothing
to say: book arithmetic alone (`Yes + No = 1`) only ever detects a crossed
book, and Hyperliquid's makers keep that band tight — measured top-of-book
edge runs -0.1c to -3.3c, i.e. exactly the spread.

Every outcome market references a perp that Hyperliquid itself quotes, so spot
and volatility both come from the venue:

- spot and OHLC from `candleSnapshot`
- volatility from the **Parkinson** high–low estimator over 14 days, cross-
  checked against close-to-close (BTC 0.52/0.52, ETH 0.72/0.71, HYPE 0.91/0.86,
  SP500 0.22/0.23 — the two agree, which is the point of computing both)
- `binaryPrice` → lognormal terminal digital
- `priceTouch` → the same digital, doubled by reflection (ported unchanged)
- output is a `(lo, mid, hi)` band from ±18% on sigma; buying Yes is judged
  against `lo` and No against `1-hi`, so an edge must survive the model's own
  uncertainty before anyone sees it

### MAX_SIGMA, and why it exists

The first version of this model found two "edges": gold No at 0.955 and silver
No at 0.95. Both needed a **~3 sigma move in under half a day**. The model said
0.2–0.4%; the book paid 6.5–9%. The book was right — commodities jump, and a
lognormal tail is fiction. Taking those trades is a fat-tail short.

So the model refuses to have an opinion beyond `MAX_SIGMA = 2.0`. That cut
modelled markets from 41 to 23 in a 120-market scan and removed both false
positives.

## Backtest verdict: the model works, but only near settlement

`agent/backtest.py` replays the model against **1,630 settled binaryPrice
markets**. Outcome coins carry no candle history, so market prices cannot be
replayed — but every settled market still exposes its full spec and its
`settleFraction`, and the underlying's candles reach back to April. So we
rebuild what the model *would have said* H hours before expiry, using only
data available at that moment, and check it against what happened.

Horizon turned out to decide everything:

| replayed at | Brier | base rate 0.475 → 0.249 | largest calibration gap |
|---|---|---|---|
| T−15m | **0.179** | beats base by 28% | 0.095 |
| T−30m | **0.196** | beats base by 21% | 0.113 |
| T−1h | 0.232 | beats base by 7% | 0.199 |
| T−2h | 0.267 | *worse than base* | 0.293 |
| T−24h | 0.297 | *worse than base* | 0.400 |

These markets are short-lived and their threshold is set near spot at
creation, so a long-horizon replay prices a threshold that did not exist yet.
The first run of this backtest used T−24h, concluded "badly miscalibrated —
do not trade this", and was measuring its own error rather than the model's.

Hence `MAX_TTL_HOURS = 1.0`: the model refuses to price anything more than an
hour from settlement.

At T−30m the residual bias is systematic rather than random — the model says
0.31 where reality is 0.20, and says 0.69 where reality is 0.80. That is
exactly the shape `Calibrator` exists to absorb, and seeding it with all 1,455
scorable settlements produces real corrections (0.30 → 0.163, 0.70 → 0.758,
with 0.15 and 0.85 left nearly untouched).

### What that means for the product

The opportunity lives in the final half hour before a market settles. You
cannot sit at a desk watching 300 markets for the last 30 minutes of each —
which is the clearest possible argument for putting this on your face rather
than in a browser tab. The agent is idle most of the time and speaks when a
market is about to resolve and the book disagrees with a calibrated model.

A live 200-market scan currently prices only 7, because few markets sit inside
the one-hour window at any instant. That is the system working, not failing.

## Honest status

Working, with one thing unproven.

- A 120-market scan prices 48 markets and surfaces 3 candidates: BTC No at
  0.14 (fair 0.21), xyz:XYZ100 Yes at 0.68 (fair 0.72), xyz:CL Yes at 0.49
  (fair 0.53). Mid-distribution, real depth, plausible magnitudes.
- Across all 265 quoted markets, 78 fall inside the model's validity window.
  Median edge is -11.2c (i.e. no edge, as it should be) with a real right tail:
  +10.2c on xyz:ANT, +7.7c on xyz:NVDA, both with 600+ contracts of depth.
- Those earlier numbers were measured **before** `MAX_TTL_HOURS` existed, so
  most of them sat outside the window where the model has any power. They are
  not evidence of edge and should not be quoted.
- **Measured, and the answer is no.** See "Forward result" below. The model
  does not produce tradeable edge. The trading premise is dead; the
  infrastructure and the preference loop are not.
- Preference learning converges in hundreds of examples; calibration now has
  1,455 real settlements behind it. Both loops are trained on real data.

## Forward collection

The backtest graded the model's *probabilities*. It could not grade *profit*,
because settled outcome coins expose no price history — so there is no way to
ask, retrospectively, what the book was paying. The only honest answer comes
from writing the book down in real time and waiting.

`agent/collect.py` does that. Every 60 seconds it finds markets inside the
one-hour window and appends one JSONL row per (market, side) carrying the live
ask and bid, the depth, the model's raw fair value, the calibrated fair value,
and whether the agent *would* have taken it. It records every modelled
candidate, not only the ones that pass the gates — so the gates themselves can
be evaluated afterwards, rather than assumed correct.

```bash
nohup python3 -I agent/collect.py collect --interval 60 > agent/collect.log 2>&1 &
```

```bash
python3 -I agent/collect.py grade
```

`grade` looks up `settleFraction` for everything recorded and reports realised
P&L per $1 staked at the price actually quoted, split three ways: all
observations, the ones the agent would have taken, and the ones it would have
skipped. If "would take" does not beat "would skip", the gates are worthless
and we find that out from data rather than from optimism.

Let it run until the build week starts. A day gives a signal; three gives a
number worth putting in a pitch.

## Voice

Double-tap to talk. Short commands, not dictation — the rubric asks for "a
short phrase... instead of long dictation", and on a device with no speaker
that is the right interaction anyway.

```
"watch bitcoin"    -> strong positive for coin:BTC
"mute hype"        -> strong negative for coin:HYPE
"quieter"          -> fewer interruptions per hour
"what's resolving" -> show me the best candidate now
"not now"          -> dismiss the current card
```

Every one lands in the **same** `OnlineLogistic` the taps train, so voice and
touch teach one model rather than two. A spoken preference is deliberate, so
it carries `VOICE_WEIGHT = 6` — worth several ignored cards.

Speech-to-text is **whisper.cpp running locally**. No key, no audio leaving
the machine. That is the hackathon's "bring your own STT" instruction and the
honest reading of its privacy rule: a face-worn microphone should not be
shipping audio to a third party. Capture is push-to-talk for the same reason.

```bash
brew install whisper-cpp
```

```bash
curl -L -o agent/models/ggml-base.en.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
```

### Three things the simulator caught

**A general model has never heard of HYPE.** Measured: `"mute hype"` →
`"Mute type."`, `"ignore zcash"` → `"Ignore Skash."` Biasing the decoder with
`--prompt` listing the tickers fixed the long forms (Hyperliquid, Solana) but
not the short ones. So when a verb parses but its object does not, a
Levenshtein pass runs against the known vocabulary — bounded to that case, so
a wrong guess costs a mis-tagged preference rather than a wrong action. With
both: 5/5 on known mishears, 0 false positives across unrelated speech
("watch out" and "watch the road" correctly resolve to no subject).

**`speakerRole` is `"self" | "other" | "unknown"`, and unknown is normal.**
Keeping only `self` frames — the obvious reading — silently discarded every
frame. The mic went live, endpointing fired, and the backend never received a
byte. Only frames positively attributed to `"other"` are dropped now.

**PCM arrives raw with no endpointing.** The SDK gives frames and nothing
else: no silence detection, no "is anyone talking". Both are ours, so there
is an RMS gate at 600, a 900ms silence timeout and a 6s hard stop, because a
stuck microphone on a wearable is not an acceptable failure mode.

## Forward result: measured over five days

Five days of live collection: 16,154 observations over 1,446 markets, 13,588
rows gradeable against settlement. Realised P&L per $1 staked:

| | n | P&L @ ask | P&L @ mid | avg spread |
|---|---|---|---|---|
| all observations | 13588 | −0.120 | **+0.000** | 0.240 |
| agent **would take** | 1793 | −0.035 | **−0.003** | 0.064 |
| agent would skip | 11795 | −0.132 | **+0.001** | 0.267 |

**The data is sound.** Buying everything at the mid returns +0.0004 — the
no-arbitrage identity holds, so collection and grading are correct.

**At the mid, the agent is break-even.** Not negative, not positive:
−0.003 against +0.001 for the trades it skips. The model neither beats the
market nor loses to it.

**The ask-price gap is spread, not skill.** Would-take rows average a 0.064
spread against 0.267 elsewhere, and half of 0.064 is almost exactly the
−0.035 gap. The agent picks tightly-quoted markets; that is all the ask
column is measuring.

### An earlier version of this section was wrong

On the first 1,889 rows this read "the gates are *inverted* — the agent is
reliably selecting the worst available trades", from a would-take figure of
−0.136 at the mid. With 7× the data that figure is −0.003. **The inversion
was small-sample noise, and the confident wording around it was not
justified by n=288.**

### The high-edge bucket looks real and is not

| calibrated edge | n | P&L @ ask | P&L @ mid |
|---|---|---|---|
| 0.03 – 0.06 | 799 | −0.063 | −0.028 |
| 0.06 – 0.10 | 622 | −0.035 | −0.004 |
| **0.10 +** | 430 | **+0.019** | **+0.058** |

Profitable after crossing the spread — which is the first genuinely
encouraging number in this project, and it does not survive scrutiny. Those
430 rows are repeated snapshots, not independent trades. Collapsed to one
trade per market+side:

```
n = 337    mean P&L@ask +0.0244    se 0.0244    t = +1.00
win rate 0.445          95% CI [-0.0234, +0.0722]
```

The confidence interval straddles zero and t is 1.0. **There is no
demonstrated edge** — the point estimate is positive and indistinguishable
from noise. Three buckets were examined, so even that point estimate carries
a multiple-comparisons discount.

### What this means

The backtest asked *"do the model's probabilities beat the base rate?"* and
the honest answer was yes: Brier 0.196 against 0.249. But the real bar is
*"do they beat the market's price?"*, and five days of forward data say no —
break-even at the mid, nothing significant anywhere.

That is why this project routes attention instead of claiming alpha. The
pivot was not a reaction to a bad number; it holds under the corrected ones
too.

## Prior work (disclosure — hackathon rule)

Built before 2026-11-23 and reused here, per the "say what existed before"
rule:

- `../polymarket-finance-test/` — calibration of resolved markets by price
  bucket, real-fills analysis (avoiding stale-midpoint traps), book-depth
  measurement after fees, and option-implied probability extraction from CBOE
  chains via digital call spreads.
- `../polymarket-weather-test/` — the "dead bucket" deterministic edge and a
  late-day probabilistic edge on daily-high temperature markets.

Ported into this repo: `bs_digital_above()`, the touch-by-reflection
approximation and the `(lo, mid, hi)` band convention (all in `agent/fair.py`),
plus the price-bucket calibration method in `Calibrator`. The Polymarket data
layer is *not* reused — different venue, different API — and the volatility
source changed from CBOE option chains to Hyperliquid's own candles.

### Written for this hackathon, with dates

Everything else in this repository was written for this event. The hackathon
opened for pre-registration on 18 November and the build window runs 23–29
November, and some of this work lands in the gap between those two dates. So,
precisely, by last-modified date:

| when | what |
|---|---|
| **18–19 Nov** (after announcement, before the build window) | the Hyperliquid data layer and model work: `agent/hl.py`, `agent/fair.py`, `agent/policy.py`, `agent/scan.py`, `agent/backtest.py`, `agent/collect.py`, `agent/learn_demo.py`, `agent/events.py`, `agent/replay.py`, `app.json`, and the vendored SDK in `web/` |
| **23–29 Nov** (build window) | the product: `agent/server.py`, `agent/voice.py`, `web/glasses.js`, `web/index.html`, `web/voice.js`, `demo/`, this README, and the five days of forward collection the results above are measured on |

The split is roughly: the first two days answered *"is there anything here?"*
and produced the finding that killed the trading premise. The build week
produced the thing you wear — the glasses rendering layer, the voice channel,
the backend, and the measurement that corrected the early numbers.

Data files (`agent/backtest_cache.json`, `agent/observations.jsonl`,
`agent/state.json`) are collected output, not source.

## Safety

Testnet only. The agent proposes; the human taps. No funds are ever held, no
advice is given, no keys ship in the client — all signing happens in the
backend.
