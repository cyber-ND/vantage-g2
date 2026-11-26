// The glasses display layer.
//
// Discovered against the simulator's automation API: the G2 does NOT render
// this page's DOM. /api/screenshot/webview showed the card perfectly while
// /api/screenshot/glasses came back blank, because the framebuffer is driven
// entirely by SDK containers. HTML and CSS are for the phone-side WebView,
// which the wearer never looks at.
//
// Two firmware constraints shape everything below.
//
// 1. There is no font size. TextContainerProperty exposes position, size,
//    padding, border, zOrder and textColor (brightness 0-4) - and nothing
//    else. "BTC  51%" measures 76px on a 576px screen and there is no way to
//    make it bigger with text. So hierarchy comes from placement, generous
//    vertical space and brightness, not scale. (An ImageContainer would allow
//    real type, but PB caps it at 288x144 - half the screen - and needs
//    grayscale raster. Noted as a possible upgrade, not done here.)
//
// 2. textContainerUpgrade carries content and textColor only - NOT position.
//    So a card whose text changes width cannot be re-centred by moving its
//    container; that would need rebuildPageContainer, which flashes the whole
//    display. Instead each container spans the full width and centring is
//    done with leading spaces. A space is exactly 5px, so this lands within
//    ~2px of true centre and never flashes.

import {
  waitForEvenAppBridge,
  OsEventTypeList,
  AudioInputSource,
  AudioSpeakerRole,
} from './even_hub_sdk.js';
import { getTextWidth, pxTruncate } from './pretext.js';

const SCREEN_W = 576;
const SPACE_W = 5;   // measured with pretext
const LINE_H = 27;   // firmware line height

const ID = { HEAD: 1, DETAIL: 2, HINT: 3 };
const NAME = { 1: 'head', 2: 'detail', 3: 'hint' };

// The head+detail block sits a little above true centre - on a heads-up
// display the eye rests high, and text pinned to the middle reads as low.
// The hint is pushed to the floor and dimmed so it recedes until wanted.
const LAYOUT = [
  { containerID: ID.HEAD,   xPosition: 0, yPosition: 92,  width: SCREEN_W, height: 32, textColor: 4 },
  { containerID: ID.DETAIL, xPosition: 0, yPosition: 134, width: SCREEN_W, height: 32, textColor: 3 },
  { containerID: ID.HINT,   xPosition: 0, yPosition: 236, width: SCREEN_W, height: 28, textColor: 1 },
];

const EMPTY = { head: 'Vantage', detail: 'watching', hint: '' };

/** Leading spaces that centre `text` on the display. */
export function centre(text, width = SCREEN_W) {
  const s = String(text ?? '');
  if (!s) return '';
  const w = getTextWidth(s);
  if (w >= width) return s;
  return ' '.repeat(Math.max(0, Math.round((width - w) / 2 / SPACE_W))) + s;
}

/** Trim to one line so it can be centred; wrapped text cannot be.
 *  pxTruncate is the firmware-accurate version from pretext - prefer it over
 *  hand-rolled slicing, which gets kerning wrong at the cut. */
export function fit(text, width = SCREEN_W - 32) {
  return pxTruncate(String(text ?? ''), width);
}

export class Glasses {
  constructor() {
    this.bridge = null;
    this.ready = false;
    this.last = {};
  }

  async connect() {
    this.bridge = await waitForEvenAppBridge();
    const textObject = LAYOUT.map((c) => ({
      ...c,
      containerName: NAME[c.containerID],
      zOrderIndex: c.containerID,
      content: centre(EMPTY[NAME[c.containerID]] ?? ''),
      // Only the headline captures input; the SDK drops taps when no
      // container is listening.
      isEventCapture: c.containerID === ID.HEAD ? 1 : 0,
    }));

    const res = await this.bridge.createStartUpPageContainer({
      containerTotalNum: textObject.length,
      textObject,
    });
    this.ready = true;
    this.last = {};
    return res;
  }

  /** Update only what changed - rewriting every line costs bridge traffic. */
  async show(parts) {
    if (!this.ready) return;
    for (const [id, name] of Object.entries(NAME)) {
      const next = centre(fit(parts[name] ?? ''));
      if (this.last[name] === next) continue;
      this.last[name] = next;
      try {
        await this.bridge.textContainerUpgrade({
          containerID: Number(id),
          containerName: name,
          content: next,
        });
      } catch (e) {
        console.warn('[glasses] upgrade failed', name, e);
      }
    }
  }

  clear() {
    return this.show(EMPTY);
  }

  /**
   * Tap = act on it. Long press = not now. Both are training labels.
   *
   * The host sends the event under listEvent / textEvent / sysEvent depending
   * on which container captured it, and eventType may arrive as an ordinal or
   * a string, so normalise through OsEventTypeList.fromJson before comparing.
   */
  onInput(handler) {
    if (!this.bridge) return () => {};
    return this.bridge.onEvenHubEvent((event) => {
      const src = event?.textEvent ?? event?.listEvent ?? event?.sysEvent;
      if (!src) return;

      // A plain tap arrives with NO eventType field. CLICK_EVENT is 0 and
      // protobuf JSON omits zero-valued enums, so the most important input on
      // the device is signalled by absence. Verified against the simulator:
      //   click              -> {"eventSource":1}             (no eventType)
      //   double_click       -> {"eventType":3,"eventSource":1}
      //   long_press         -> {"eventType":9}
      //   long_press_release -> {"eventType":10}
      // Treating a missing eventType as "no event" silently breaks tap.
      const raw = src.eventType;
      const type = (raw === undefined || raw === null)
        ? OsEventTypeList.CLICK_EVENT
        : (OsEventTypeList.fromJson?.(raw) ?? raw);

      if (type === OsEventTypeList.CLICK_EVENT) handler('accept');
      else if (type === OsEventTypeList.LONG_PRESS_EVENT) handler('dismiss');
      else if (type === OsEventTypeList.DOUBLE_CLICK_EVENT) handler('voice');
      // LONG_PRESS_RELEASE is deliberately ignored - acting on both ends of a
      // long press fires the handler twice for one gesture.
    });
  }

  // ---- microphone -------------------------------------------------------
  // The SDK hands over raw PCM frames and nothing else: no STT on device, no
  // endpointing, no "is anyone talking". Those are ours to do. speakerRole
  // is the one hint we get, and it is the App's classifier rather than a
  // firmware identity claim - good enough to drop frames from the person
  // across the table, not good enough to trust blindly.
  async listen(onFrame) {
    if (!this.bridge) return () => {};
    const off = this.bridge.onEvenHubEvent((event) => {
      const a = event?.audioEvent;
      if (!a?.audioPcm?.length) return;
      onFrame(a.audioPcm, {
        self: a.speakerRole === AudioSpeakerRole.Self,
        role: a.speakerRole,
        source: a.source,
      });
    });
    await this.bridge.audioControl(true, AudioInputSource.Glasses);
    return async () => {
      try { await this.bridge.audioControl(false); } catch (e) { /* already off */ }
      off();
    };
  }
}

/** Card -> the three lines actually drawn on glass. */
export function linesFor(card) {
  if (!card) return { head: 'Vantage', detail: 'nothing right now', hint: '' };
  const pct = Math.round(card.mid * 100);
  const head = card.kind === 'decided'
    ? `${card.label}  ${card.mid >= 0.5 ? 'YES' : 'NO'}`
    : `${card.label}  ${pct}%`;
  return { head, detail: card.detail || '', hint: 'Tap  open      Hold  later' };
}

export const _internals = { LAYOUT, SPACE_W, LINE_H, SCREEN_W };
