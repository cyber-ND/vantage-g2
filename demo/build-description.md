## The problem

The six crypto apps already in the Even Hub catalogue all show you prices.
None of them decides anything.

On a 576×288 monochrome display worn on your face, the scarce resource is not
information — it is **interruption**. A ticker on your glasses is a ticker you
will learn to ignore. The open problem on this device is not *what can we
show?* but *what is worth looking up for?*

Vantage is an agent whose only job is that judgement, and whose job is to get
better at it the longer you wear it.

## How it works

Four stages, running continuously in a backend the glasses app talks to:

**Perceive** — Hyperliquid mainnet, read-only, restricted to markets inside
the last hour of their life. HIP-4 outcome markets settle to exactly 1 or 0,
so ground truth is free and automatic: the agent grades its own past decisions
without anyone labelling anything. Very little else on a wearable platform has
that property.

**Detect** — three event types (`resolving`, `flip`, `decided`), each
confirmed on a following tick before it counts, and each fired once per
market. This is not optional: the median one-minute move in these markets is
6.4 points and 37% of ticks move 10+, so a naive "big move" alert fires 53
times an hour and is worthless.

**Decide** — candidates are ranked by learned preference against an **adaptive
quantile threshold** matched to an hourly interruption budget. A fixed score
cutoff does not work — the model happily learns you ignore ETH, and a
slightly-more-interesting ETH card still clears a constant bar and still
interrupts you. Letting the agent decide *how picky to be*, not just what it
likes, moved relevance 48% → 64% on its own.

**Learn** — the tap is the label. Accept and dismiss train an online logistic
regression over the event's feature vector. Voice commands train the *same*
weights, carrying more weight because a spoken preference is deliberate.
Nothing in the code names a market; the weights find it.

## Key features

- **One card, never a dashboard.** The poller will not replace a card you have
  not answered yet — swapping the display mid-glance is how you make a
  heads-up device impossible to trust.
- **Tap to open, hold for "not now."** Both are training labels.
- **Double-tap to talk.** `watch bitcoin`, `mute hype`, `fewer interruptions`,
  `what's resolving`, `not now`. Short commands, not dictation.
- **It stays quiet.** In one recent 24-hour stretch it judged 465 polls worth
  of market activity and raised a single card. That is the product working.

## What is measured, not claimed

**Learning: +4 points of relevance (53% → 57%)** across 1,100 real candidate
events, at the same interruption cost, against a frozen-priors control group
that rules out the detectors and the budget taking credit for it.

**Trading: tested and rejected.** 16,154 observations over 1,446 markets,
13,588 gradeable against settlement — break-even at the mid. The one
encouraging bucket (calibrated edge ≥ 0.10, +1.9c per $1 after crossing the
spread) collapses to n=337 independent trades, t = 1.00, 95% CI
[−0.023, +0.072]. **No demonstrated edge.**

That second result is what the design is built on. If the book is well
calibrated then the book's price is good truth, and the open problem is
attention, not alpha. The README reports both numbers in full — including two
earlier, more flattering versions of them that more data corrected.

## Tech stack

- **Even Hub SDK 0.0.16** + `@evenrealities/pretext` for pixel-accurate G2
  font metrics; `evenhub-simulator` with its automation API for testing.
- **Hyperliquid** `/info` — HIP-4 outcome markets, HIP-3 builder perp dexes,
  `l2Book`, `allMids`, `candleSnapshot`, `settledOutcome`.
- **Python 3, standard library only** for the agent: data layer, Parkinson
  volatility estimator, Black–Scholes digital pricer, online logistic
  regression, calibration with standard-error shrinkage, adaptive thresholding.
- **whisper.cpp** for speech-to-text, running locally.
- No bundler — the SDK is a self-contained ES module and is vendored directly.

## Hackathon requirements

- **Money safety.** Read-only on mainnet, signing confined to testnet, no
  funds held, no financial advice given. The agent proposes; the human taps.
- **No keys in the client.** The glasses app is deliberately dumb — it renders
  one card and reports one gesture. Every decision and every credential lives
  in the backend, with external hosts whitelisted in `app.json`.
- **Bring your own STT.** whisper.cpp runs locally: no key, no account, no
  audio leaving the machine. Capture is push-to-talk, not always-listening.
  That is the honest reading of the privacy rule for a face-worn microphone.
- **Prior work.** Two pre-existing Polymarket research repos supplied
  `bs_digital_above()`, the touch-by-reflection approximation, the (lo, mid,
  hi) band convention and the price-bucket calibration method. Everything else
  was written for this event — the README carries a dated, file-by-file table,
  including the parts written between the 18 Nov announcement and the 23 Nov
  build window.

## Five things the APIs were missing

1. **The G2 does not render your DOM, and nothing says so.** The framebuffer
   is driven entirely by SDK containers; your HTML and CSS run in a WebView
   the wearer never looks at. Invisible in a browser and in the console — it
   surfaced only because the simulator's `/api/screenshot/webview` returned a
   perfect card while `/api/screenshot/glasses` returned a blank 576×288.
2. **A tap is signalled by absence.** `CLICK_EVENT` is `0` and protobuf JSON
   omits zero-valued enums, so the most important input on the device arrives
   with no `eventType` field at all. Reading `eventType` and bailing when it is
   missing — the obvious implementation — silently breaks tap while long-press
   keeps working perfectly.
3. **`textContainerUpgrade` carries content but not position**, and there is no
   font size anywhere in `TextContainerProperty`. Text that changes width
   cannot be re-centred without `rebuildPageContainer`, which flashes the whole
   display, so centring ends up done with leading spaces.
4. **`speakerRole` is `"self" | "other" | "unknown"`, and `unknown` is the
   normal value** — undocumented. Keeping only `self` frames discards every
   frame: the mic goes live, endpointing fires, and the backend never receives
   a single byte.
5. **Audio arrives as raw PCM with no endpointing and no documented sample
   rate.** No silence detection, no "is anyone talking", no rate field. All of
   it is yours to build — and a stuck microphone on a wearable is not an
   acceptable failure mode.
