// Voice capture: double-tap to talk, release by double-tapping again or by
// going quiet. Raw PCM goes to our backend, which runs whisper locally.
//
// Deliberately push-to-talk rather than always-listening. A hot mic on a
// face-worn device is a privacy problem, it burns battery, and the Even Hub
// rules are explicit that you must respect "the privacy of the people whose
// messages you show" - the same courtesy applies to whoever is standing next
// to the wearer. Nothing is captured until the wearer asks for it.

const MAX_MS = 6000;      // a command, not a monologue
const SILENCE_MS = 900;   // stop once they stop
const RMS_GATE = 600;     // int16 amplitude below this counts as silence

export class Voice {
  constructor(glasses, api) {
    this.glasses = glasses;
    this.api = api;
    this.active = false;
    this.stop = null;
    this.chunks = [];
    this.bytes = 0;
    this.timers = [];
  }

  /** Toggle. Returns the line to show on glass, or null if nothing to say. */
  async toggle(onStatus) {
    if (this.active) return this.finish(onStatus);
    return this.start(onStatus);
  }

  async start(onStatus) {
    this.active = true;
    this.chunks = [];
    this.bytes = 0;
    onStatus?.('listening...');

    let lastLoud = Date.now();
    this.stop = await this.glasses.listen((pcm, meta) => {
      if (!this.active) return;
      // Drop ONLY frames the App positively attributes to someone else.
      // speakerRole is "self" | "other" | "unknown", and unknown is the
      // common case - phone mic and older hosts always report it, and the
      // simulator reports it too. An earlier version here kept frames only
      // when role was Self, which silently discarded every frame and posted
      // nothing at all: the mic looked alive, the backend never heard a word.
      if (meta.role === 'other') return;
      this.chunks.push(pcm);
      this.bytes += pcm.length;
      if (rms16(pcm) > RMS_GATE) lastLoud = Date.now();
    });

    // endpointing: stop on silence, and hard-stop so a stuck mic cannot run
    const tick = setInterval(() => {
      if (!this.active) return;
      if (Date.now() - lastLoud > SILENCE_MS && this.bytes > 0) {
        this.finish(onStatus);
      }
    }, 200);
    const hard = setTimeout(() => this.active && this.finish(onStatus), MAX_MS);
    this.timers = [() => clearInterval(tick), () => clearTimeout(hard)];
    return 'listening...';
  }

  async finish(onStatus) {
    if (!this.active) return null;
    this.active = false;
    this.timers.forEach((f) => f());
    this.timers = [];
    try { await this.stop?.(); } catch (e) { /* ignore */ }
    this.stop = null;

    if (!this.bytes) {
      onStatus?.('heard nothing');
      return 'heard nothing';
    }
    onStatus?.('thinking...');
    console.log('[voice] captured', this.bytes, 'bytes');

    const merged = new Uint8Array(this.bytes);
    let off = 0;
    for (const c of this.chunks) { merged.set(c, off); off += c.length; }
    this.chunks = [];

    try {
      const r = await fetch(this.api + '/voice', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pcm: b64(merged), bytes: merged.length }),
      });
      const j = await r.json();
      const line = j.reply || j.error || 'didn\'t catch that';
      onStatus?.(line);
      return line;
    } catch (e) {
      onStatus?.('voice backend offline');
      return 'voice backend offline';
    }
  }
}

/** Loudness of one int16 LE frame, for endpointing. */
function rms16(u8) {
  const n = u8.length >> 1;
  if (!n) return 0;
  const dv = new DataView(u8.buffer, u8.byteOffset, n * 2);
  let acc = 0;
  for (let i = 0; i < n; i++) { const s = dv.getInt16(i * 2, true); acc += s * s; }
  return Math.sqrt(acc / n);
}

/** Chunked so a few seconds of PCM does not blow the call stack. */
function b64(u8) {
  let s = '';
  const S = 0x8000;
  for (let i = 0; i < u8.length; i += S) {
    s += String.fromCharCode.apply(null, u8.subarray(i, i + S));
  }
  return btoa(s);
}
