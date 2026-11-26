"""Lay a narration track over the rendered demo video.

The video is a silent recording of the glasses display with captions. This
speaks over it, timed against the video's own clock rather than against the
captions - a voiceover is allowed to run across a caption boundary, it just
must not run into the next line.

  python3 -I demo/narrate.py [--voice Samantha] [--rate 160]

Writes demo/vantage-demo-narrated.mp4. `--script` prints the lines with their
timings and durations and exits, which is what you want if you would rather
record this in your own voice.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "recording", "work", "vo")
VIDEO = os.path.join(HERE, "vantage-demo.mp4")
OUT = os.path.join(HERE, "vantage-demo-narrated.mp4")

# (start second in the finished video, line)
NARRATION = [
    (1.0,  "Vantage. An agent that decides what is worth interrupting you for."),
    (7.2,  "Right now it has nothing to say, which is most of the time."),
    (11.4, "On a display worn on your face, the scarce resource is not "
           "information. It is interruption."),
    (18.2, "These are real Hyperliquid outcome markets, recorded and replayed "
           "on a compressed clock."),
    (24.5, "This wearer holds Bitcoin. Tap what matters. Hold what does not."),
    (30.5, "Each tap is a training label. Two for Bitcoin."),
    (36.0, "And a hold for Ethereum, which is not theirs."),
    (40.8, "Nothing in the code names Bitcoin. The weights find it."),
    (45.5, "Double tap to talk. Speech to text is whisper, running locally."),
    (51.8, "No key. No account. No audio leaving the machine."),
    (57.0, "Voice trains the same model the taps do, weighted higher."),
    (62.5, "A spoken preference is deliberate, so it outweighs several "
           "ignored cards."),
    (68.5, "Fewer interruptions. The wearer sets the budget by saying so."),
    (75.0, "Eighty six seconds, from a cold start."),
    (79.0, "Bitcoin, positive. Ethereum, negative."),
    (83.5, "The agent found the preference. It was never told."),
    (93.5, "Measured, not claimed. Learning lifts relevance four points over "
           "eleven hundred real candidate events."),
    (100.2, "The trading premise was tested, and rejected."),
]


def duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def render(voice, rate):
    os.makedirs(WORK, exist_ok=True)
    clips = []
    for i, (start, text) in enumerate(NARRATION):
        p = os.path.join(WORK, f"{i:02d}.aiff")
        subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", p, text],
                       check=True)
        clips.append((start, duration(p), p, text))
    return clips


def check(clips, video_len):
    bad = 0
    for i, (start, dur, _, text) in enumerate(clips):
        end = start + dur
        nxt = clips[i + 1][0] if i + 1 < len(clips) else video_len
        flag = ""
        if end > nxt:
            flag = f"  <-- OVERRUNS by {end - nxt:.1f}s"
            bad += 1
        elif end > video_len:
            flag = "  <-- past the end of the video"
            bad += 1
        print(f"  {start:6.1f}s  {dur:4.1f}s  {text[:62]:<62}{flag}")
    return bad


def main():
    args = sys.argv[1:]
    voice = args[args.index("--voice") + 1] if "--voice" in args else "Samantha"
    rate = int(args[args.index("--rate") + 1]) if "--rate" in args else 160

    if not os.path.exists(VIDEO):
        raise SystemExit(f"{VIDEO} not found - run demo/render.py first")
    vlen = duration(VIDEO)

    clips = render(voice, rate)
    print(f"\nvideo {vlen:.1f}s, voice {voice} at {rate} wpm\n")
    bad = check(clips, vlen)
    if "--script" in args:
        return 0
    if bad:
        print(f"\n{bad} line(s) do not fit - shorten them or move the start")
        return 1

    # one delayed input per line, mixed down to a single track
    cmd = ["ffmpeg", "-y", "-i", VIDEO]
    for _, _, p, _ in clips:
        cmd += ["-i", p]
    chain = []
    for i, (start, _, _, _) in enumerate(clips, start=1):
        ms = int(start * 1000)
        chain.append(f"[{i}:a]adelay={ms}|{ms},aresample=48000[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(1, len(clips) + 1))
    # normalize=0: amix otherwise divides every voice by the input count and
    # the whole track ends up inaudible
    chain.append(f"{mix}amix=inputs={len(clips)}:normalize=0:"
                 f"dropout_transition=0[mix]")
    chain.append("[mix]alimiter=limit=0.95,aformat=sample_fmts=fltp[out]")

    cmd += ["-filter_complex", ";".join(chain),
            "-map", "0:v", "-map", "[out]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-shortest", "-movflags", "+faststart", OUT]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-2500:])
        return 1
    print(f"\n{OUT}  {duration(OUT):.0f}s  "
          f"{os.path.getsize(OUT) / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
