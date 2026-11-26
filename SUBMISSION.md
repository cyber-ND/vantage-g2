# DoraHacks submission — copy/paste

**Track:** Crypto & DeFi (Track 02)
**Repo:** https://github.com/cyber-ND/vantage-g2
**Video:** `demo/vantage-demo.mp4` (105s) — needs uploading to YouTube/Vimeo
unlisted, then paste the link into the Build form.

---

## Name

Vantage

## Tagline (one line)

An agent that decides what is worth interrupting you for, on Even Realities G2.

## Short description

Vantage watches Hyperliquid HIP-4 outcome markets and spends a budget of
interruptions on the few moments worth a glance. It learns what to show you
from your taps and from short voice commands, against a threshold it tunes
itself. On a 576x288 display worn on your face, the scarce resource is not
information — it is interruption.

## Full description

The six crypto apps already in the Even Hub catalogue all show you prices.
None of them decides anything. Vantage is not a ticker: it is an agent whose
only job is to judge what deserves your attention, and to get better at that
judgement the longer you wear it.

**Perceive.** Hyperliquid mainnet, read-only, markets inside the last hour of
their life. HIP-4 outcome markets settle to exactly 1 or 0, so ground truth is
free and automatic — the agent grades its own past decisions without anyone
labelling anything.

**Detect.** Three event types — resolving, flip, decided — each confirmed on a
following tick before it counts, and each fired once per market. This matters:
the median one-minute move in these markets is 6.4 points and 37% of ticks
move 10+, so a naive "big move" alert fires 53 times an hour and is worthless.

**Decide.** Candidates are ranked by learned preference against an adaptive
quantile threshold matched to an hourly budget. A fixed cutoff does not work —
the model happily learns you ignore ETH, and a slightly-more-interesting ETH
card still clears a constant bar. Letting the agent decide how picky to be,
rather than only what it likes, moved relevance 48% → 64% on its own.

**Learn.** The tap is the label. Accept and dismiss train an online logistic
regression over the event's features; voice commands train the same weights,
weighted higher because a spoken preference is deliberate. Nothing in the code
names a market — the weights find it.

### What is measured, not claimed

- **Learning:** +4 points of relevance (53% → 57%) across 1,100 real candidate
  events, at the same interruption cost, against a frozen-priors control group
  that rules out the detectors and the budget taking credit.
- **Trading:** tested and rejected. 16,154 observations over 1,446 markets,
  13,588 gradeable against settlement: break-even at the mid. The one
  encouraging bucket (edge ≥ 0.10, +1.9c after spread) collapses to n=337
  independent trades, t = 1.00, 95% CI [−0.023, +0.072]. No demonstrated edge.

That second result is the one the design is built on. If the book is well
calibrated, the book's price is good truth, and the open problem is attention,
not alpha. The README reports both numbers in full, including two earlier,
flattering versions of them that more data corrected.

### Safety

Read-only on mainnet, signing on testnet, no funds held, no advice given, no
keys in the client. Speech-to-text is whisper.cpp running locally — no key, no
account, no audio leaving the machine, which is the honest reading of the
privacy rule for a face-worn microphone. Capture is push-to-talk for the same
reason.

## Five things the APIs were missing

1. **The G2 does not render your DOM, and nothing says so.** The framebuffer
   is driven entirely by SDK containers; HTML and CSS run in a WebView the
   wearer never sees. Invisible in a browser and in the console — it showed up
   only because `/api/screenshot/webview` returned a perfect card while
   `/api/screenshot/glasses` returned a blank 576x288.
2. **A tap is signalled by absence.** `CLICK_EVENT` is `0` and protobuf JSON
   omits zero-valued enums, so the most important input on the device arrives
   with no `eventType` field at all. Reading `eventType` and bailing when it is
   missing — the obvious implementation — silently breaks tap while long-press
   keeps working.
3. **`textContainerUpgrade` carries content but not position**, and there is no
   font size anywhere in `TextContainerProperty`. So text that changes width
   cannot be re-centred without `rebuildPageContainer`, which flashes the whole
   display. Centring ends up done with leading spaces.
4. **`speakerRole` is `"self" | "other" | "unknown"`, and `unknown` is the
   normal value** — undocumented. Keeping only `self` frames discards every
   frame: the mic goes live, endpointing fires, and the backend never receives
   a byte.
5. **Audio arrives as raw PCM with no endpointing and no documented sample
   rate.** No silence detection, no "is anyone talking", no rate field. All of
   it is yours to build, and a stuck microphone on a wearable is not an
   acceptable failure mode.

## Prior work disclosure

Two pre-existing Polymarket research repos supplied `bs_digital_above()`, the
touch-by-reflection approximation, the (lo, mid, hi) band convention and the
price-bucket calibration method. Everything else was written for this event:
the Hyperliquid data layer, model and attention agent on 18–19 Nov (after
announcement, before the build window), and the product — glasses rendering,
voice, backend, demo and the five days of forward collection — during the
build window. The README has the file-by-file table.

## Team

Solo.
