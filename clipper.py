#!/usr/bin/env python3
"""
ClipForge - free, local, file-mode auto clipper (v2 editing).

Takes a local video file, finds the highest-energy moments from the audio,
cuts vertical 9:16 clips around them - with the cut points snapped to
sentence boundaries so clips begin and end on complete thoughts - and
burns in pop-style word captions
(the spoken word turns yellow and pops as it is said), a hook title card
from the peak line, a gentle push-in zoom, a yellow progress bar, and
loudness-normalised audio. No accounts, no APIs, no posting. Everything
runs on your own machine with free tools:

  - ffmpeg            (cutting / rendering)
  - faster-whisper    (local speech-to-text for captions)

Usage:
  python clipper.py --file episode.mp4 --top 3
  python clipper.py --file stream.mp4 --top 5 --clip-len 30 --lead-in 6 --layout fill
  python clipper.py --file greg-episode.mp4 --campaign iced-coffee-hour
  python clipper.py --batch-dir incoming/ --top 3

Campaign mode: a profile in profiles/<name>.json supplies the clip
length limits, watermark, guest name card, hook notes and the post
tags/text template for one campaign; every clip then also gets a
<clip>.post.txt "post pack" next to it, ready to copy when posting by
hand. Batch mode processes a folder of campaign subfolders in one run.
The tool only ever creates files - it never posts, submits or uploads
anything, for any campaign.
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import wave

import numpy as np

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def run(cmd, **kw):
    """Run a command, raising with its stderr tail on failure."""
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(
            "command failed: %s\n%s" % (" ".join(cmd[:3]), r.stderr[-1500:]))
    return r


def probe_duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path])
    return float(r.stdout.strip())


def probe_has_audio(path):
    r = run(["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", path])
    return bool(r.stdout.strip())


# --------------------------------------------------------------------------
# Step 1 - moment detection from audio energy
# --------------------------------------------------------------------------

def extract_audio_wav(video_path, wav_path):
    run(["ffmpeg", "-v", "error", "-y", "-i", video_path,
         "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", wav_path])


def load_wav_mono(path):
    with wave.open(path, "rb") as w:
        n = w.getnframes()
        raw = w.readframes(n)
        sr = w.getframerate()
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return audio, sr


def find_moments(audio, sr, top, min_gap, clip_len):
    """Score each 1-second window by loudness + loudness rise (+ a cheap
    speech-presence factor), then greedily pick the best separated peaks.

    Returns a list of dicts: {peak (sec), score}."""
    win = sr  # 1 second
    n_win = len(audio) // win
    if n_win < 5:
        return []
    frames = audio[: n_win * win].reshape(n_win, win)

    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    db = 20.0 * np.log10(rms + 1e-6)

    # Speech-presence proxy: fraction of samples clearly above silence.
    active = np.mean(np.abs(frames) > 0.02, axis=1)

    # Smooth loudness over ~5 s so bursts, not single samples, win.
    kernel = np.ones(5) / 5.0
    smooth = np.convolve(db, kernel, mode="same")

    # Rise: how much louder than ~5 s ago (laughter, shouting, a punchline).
    rise = np.zeros_like(smooth)
    rise[5:] = smooth[5:] - smooth[:-5]
    rise = np.clip(rise, 0, None)

    # Normalise both parts to 0..1 across this file, then combine.
    def norm(x):
        lo, hi = np.percentile(x, 5), np.percentile(x, 99)
        if hi - lo < 1e-6:
            return np.zeros_like(x)
        return np.clip((x - lo) / (hi - lo), 0, 1)

    score = 0.7 * norm(smooth) + 0.3 * norm(rise)

    # Near-silent windows (music beds, pauses) are poor clip material.
    score = np.where(active < 0.08, score * 0.25, score)

    # Greedy peak picking with a minimum separation.
    order = np.argsort(-score)
    picked = []
    for idx in order:
        t = float(idx)
        if score[idx] <= 0:
            break
        if all(abs(t - p["peak"]) >= min_gap for p in picked):
            picked.append({"peak": t, "score": round(float(score[idx]), 3)})
        if len(picked) >= top:
            break
    picked.sort(key=lambda p: p["peak"])
    return picked


# --------------------------------------------------------------------------
# Step 2 - captions (faster-whisper word timestamps -> pop-style ASS)
# --------------------------------------------------------------------------

SWEAR_WORDS = [
    "fuck", "fucking", "fucked", "fucker", "shit", "bullshit", "bitch",
    "asshole", "dick", "pussy", "whore", "slut", "bastard", "nigger",
    "nigga", "cunt",
]
_SWEAR_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in SWEAR_WORDS) + r")\b",
    re.IGNORECASE)


def mask_swear(word):
    def repl(m):
        w = m.group(0)
        return w[0] + "***" if len(w) > 1 else "*"
    return _SWEAR_RE.sub(repl, word)


def ass_time(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return "%d:%02d:%05.2f" % (h, m, s)


def ass_escape(text):
    return text.replace("\\", "").replace("{", "").replace("}", "")


FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


def resolve_font():
    """Pick the caption/hook font: a downloaded display font from fonts/
    if present (Anton first, then Montserrat ExtraBold), else the
    guaranteed system fallback. Returns (family_name, fontsdir|None)."""
    if os.path.exists(os.path.join(FONTS_DIR, "Anton-Regular.ttf")):
        return "Anton", FONTS_DIR
    if os.path.exists(os.path.join(FONTS_DIR, "Montserrat-ExtraBold.ttf")):
        return "Montserrat ExtraBold", FONTS_DIR
    return "DejaVu Sans", None


PROFILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "profiles")


def load_profile(name):
    """Load a campaign profile from profiles/<name>.json. Adding a new
    campaign is just adding a new JSON file there - no code changes.
    Raises SystemExit with a clear message when the profile is missing."""
    fname = name if name.endswith(".json") else name + ".json"
    path = os.path.join(PROFILES_DIR, fname)
    if not os.path.exists(path):
        avail = []
        if os.path.isdir(PROFILES_DIR):
            avail = sorted(f[:-5] for f in os.listdir(PROFILES_DIR)
                           if f.endswith(".json"))
        raise SystemExit(
            "error: no campaign profile %r in profiles/ (available: %s). "
            "Billy Porter and Decentraland have NO profile yet - their "
            "exact clip rules are not on file and must be read off the "
            "Whop campaign pages before a profile is written."
            % (name, ", ".join(avail) if avail else "none"))
    with open(path, encoding="utf-8") as f:
        profile = json.load(f)
    profile["_profile_name"] = os.path.splitext(fname)[0]
    return profile


# Backwards-compatible alias (older scripts/docs used this name).
def load_campaign(name):
    return load_profile(name)


def resolve_watermark(profile):
    """Return (watermark_dict_or_None, missing_bool) for a profile.
    The watermark file path in the profile is relative to profiles/."""
    wm = profile.get("watermark")
    if not wm:
        return None, False
    path = wm.get("file", "")
    if not os.path.isabs(path):
        path = os.path.join(PROFILES_DIR, path)
    if not os.path.exists(path):
        return None, True
    return {"path": path,
            "position": wm.get("position", "bottom-right"),
            "opacity": float(wm.get("opacity", 0.85)),
            "width": int(wm.get("width", 200))}, False


def print_campaign(profile):
    """Print what a campaign profile will do, so the human reviewer
    sees the settings, the extras and the rules notes before a run."""
    cl = profile.get("clip_length", {}) or {}
    print("campaign profile: %s"
          % profile.get("display_name", profile.get("_profile_name")))
    print("  clips: default %ss (min %s, max %s), layout %s, language %s"
          % (cl.get("default", 30), cl.get("min", "-"), cl.get("max", "-"),
             profile.get("layout", "fit"), profile.get("language", "auto")))
    wm, missing = resolve_watermark(profile)
    if profile.get("watermark"):
        print("  watermark: %s%s"
              % (profile["watermark"].get("file"),
                 "  *** FILE MISSING - clips will carry NO watermark ***"
                 if missing else ""))
    else:
        print("  watermark: none")
    nc = profile.get("name_card")
    if nc:
        print("  name card: first %.1fs%s"
              % (float(nc.get("duration", 2.0)),
                 ", default text %r" % nc.get("default_text")
                 if nc.get("default_text") else
                 " (text from --name-card or name_card.txt)"))
    post = profile.get("post", {}) or {}
    if post.get("youtube_tags"):
        print("  post tags (YouTube): %s" % post["youtube_tags"])
    if post.get("instagram_tags"):
        print("  post tags (Instagram): %s" % post["instagram_tags"])
    for note in profile.get("rules_notes", []):
        print("  note: %s" % note)


def ass_header(font_name):
    return """[Script Info]
Title: ClipForge captions
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Pop,%s,62,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,5,2,2,24,24,290,1
Style: Hook,%s,54,&H00FFFFFF,&H00FFFFFF,&H00000000,&H7F000000,-1,0,0,0,100,100,0,0,3,14,0,8,40,40,84,1
Style: HookLow,%s,54,&H00FFFFFF,&H00FFFFFF,&H00000000,&H7F000000,-1,0,0,0,100,100,0,0,3,14,0,8,40,40,168,1
Style: NameCard,%s,46,&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,-1,0,0,0,100,100,0,0,3,12,0,8,40,40,56,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" % (font_name, font_name, font_name, font_name)


YELLOW = r"{\c&H00FFFF&}"
WHITE = r"{\c&H00FFFFFF&}"
# Scale pop on the spoken word: 100% -> 115% -> 100% over the first
# 150 ms of the word's event (events start exactly at the word start).
POP_TAG = (r"{\fscx100\fscy100"
           r"\t(0,70,\fscx115\fscy115)"
           r"\t(70,150,\fscx100\fscy100)}")


def wrap_hook_text(text):
    """Wrap a hook title to two balanced lines (ASS \\N) when it is
    longer than ~24 characters - the same wrapping build_hook_text
    applies to auto-built hooks."""
    if len(text) > 24:
        ws = text.split(" ")
        if len(ws) > 1:
            best = min(range(1, len(ws)),
                       key=lambda k: abs(len(" ".join(ws[:k]))
                                         - len(" ".join(ws[k:]))))
            text = " ".join(ws[:best]) + r"\N" + " ".join(ws[best:])
    return text


def build_hook_text(words, peak_local, max_words=7):
    """Build the hook title from the words around the clip's peak moment:
    the phrase containing the loudest window, expanded to at most
    max_words words (never across a >0.8 s pause), cleaned + uppercased,
    wrapped to two lines if long. Returns '' when there are no words."""
    if not words:
        return ""
    idx = None
    for i, (s, e, _t) in enumerate(words):
        if s <= peak_local <= e:
            idx = i
            break
    if idx is None:
        idx = min(range(len(words)),
                  key=lambda i: abs((words[i][0] + words[i][1]) / 2.0
                                    - peak_local))
    lo = hi = idx
    while hi - lo + 1 < max_words:
        grew = False
        if hi + 1 < len(words) and words[hi + 1][0] - words[hi][1] <= 0.8:
            hi += 1
            grew = True
        elif lo - 1 >= 0 and words[lo][0] - words[lo - 1][1] <= 0.8:
            lo -= 1
            grew = True
        if not grew:
            break
    text = " ".join(w[2] for w in words[lo:hi + 1])
    text = mask_swear(ass_escape(text))
    text = re.sub(r"\s+", " ", text).strip(" \"'“”‘’")
    text = text.upper()
    return wrap_hook_text(text)


def build_ass(words, ass_path, font_name, hook_text="", clip_dur=0.0,
              pop=True, name_card=None, name_card_dur=2.0):
    """words: list of (start, end, text) in clip-local seconds.

    Groups words into phrases of up to 3. For each spoken word, one event
    shows the phrase-so-far: earlier words white, the current word yellow
    (with a brief scale pop). Words not yet spoken are not shown yet -
    that is the 'pop'. An optional hook title event (layer 1, top-centre)
    covers roughly the first 3 seconds. An optional campaign name card
    (layer 2, top-centre, first ~2 s - the guest's name, on screen from
    the very first frame) sits ABOVE the hook; when a name card is
    present the hook uses the HookLow style so the two never overlap."""
    # Group into phrases: max 3 words, break on a >0.6 s pause or >1.8 s span.
    phrases = []
    cur = []
    for w in words:
        if cur:
            gap = w[0] - cur[-1][1]
            span = w[1] - cur[0][0]
            if len(cur) >= 3 or gap > 0.6 or span > 1.8:
                phrases.append(cur)
                cur = []
        cur.append(w)
    if cur:
        phrases.append(cur)

    lines = [ass_header(font_name)]
    if name_card and clip_dur > 0:
        lines.append("Dialogue: 2,%s,%s,NameCard,,0,0,0,,%s"
                     % (ass_time(0),
                        ass_time(min(name_card_dur, clip_dur)),
                        ass_escape(name_card)))
    if hook_text and clip_dur > 0:
        hook_style = "HookLow" if name_card else "Hook"
        lines.append("Dialogue: 1,%s,%s,%s,,0,0,0,,%s"
                     % (ass_time(0), ass_time(min(3.0, clip_dur)),
                        hook_style, hook_text))
    for phrase in phrases:
        shown = []
        for i, (start, end, text) in enumerate(phrase):
            shown.append(mask_swear(ass_escape(text)))
            parts = []
            for j, wtxt in enumerate(shown):
                if j == i:
                    parts.append((POP_TAG if pop else "") + YELLOW + wtxt)
                else:
                    parts.append(WHITE + wtxt)
            body = " ".join(parts)
            # Last word of a phrase stays up until the next phrase starts.
            lines.append("Dialogue: 0,%s,%s,Pop,,0,0,0,,%s"
                         % (ass_time(start), ass_time(end), body))
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(phrases)


def transcribe_span(model, video_path, start, end, language):
    """Cut the clip's audio span to a temp wav and transcribe with word
    timestamps. Returns list of (start, end, word) in clip-local seconds."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    try:
        run(["ffmpeg", "-v", "error", "-y", "-ss", "%.2f" % start,
             "-i", video_path, "-t", "%.2f" % (end - start),
             "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", wav_path])
        # Pass a decoded numpy array, not the file path: some PyAV builds
        # reject the kwargs faster-whisper uses when opening files itself.
        audio, _sr = load_wav_mono(wav_path)
        segments, _info = model.transcribe(
            audio, language=language, word_timestamps=True,
            vad_filter=True, beam_size=1)
        words = []
        for seg in segments:
            for w in seg.words or []:
                txt = w.word.strip()
                if txt:
                    words.append((float(w.start), float(w.end), txt))
        return words
    finally:
        if os.path.exists(wav_path):
            os.unlink(wav_path)


# --------------------------------------------------------------------------
# Step 2b - sentence-aware start/end snapping
# --------------------------------------------------------------------------

SNAP_MARGIN = 8.0          # extra seconds transcribed beyond each edge
SNAP_END_MAX_EXTEND = 6.0  # max seconds the end may grow to finish a sentence
SNAP_TAIL_PAD = 0.4        # breathing room after the last word of a sentence
SNAP_MAX_SHIFT = 4.0       # max seconds either edge may move
SNAP_PAUSE = 0.7           # a pause longer than this also ends a sentence

_SENT_END_RE = re.compile(r"[.?!][\"'”’)\]]*$")


def build_sentences(words):
    """Group absolute-timed words [(start, end, text)] into sentences.
    A sentence ends at a word whose text ends in . ? or !, or just before
    a pause longer than ~0.7 s. Returns a list of dicts with the sentence
    start/end times and first/last word indices."""
    sentences = []
    first = 0
    for i, (_s, e, txt) in enumerate(words):
        boundary = bool(_SENT_END_RE.search(txt))
        if not boundary and i + 1 < len(words):
            boundary = words[i + 1][0] - e > SNAP_PAUSE
        if boundary:
            sentences.append({"start": words[first][0], "end": e,
                              "first": first, "last": i})
            first = i + 1
    if first < len(words):
        sentences.append({"start": words[first][0],
                          "end": words[-1][1],
                          "first": first, "last": len(words) - 1})
    return sentences


def snap_window(words, req_start, req_end, peak, clip_len, duration):
    """Snap a requested [req_start, req_end] window to sentence
    boundaries, using absolute-timed words that cover the window plus
    margin. Returns (start, end, start_moved, end_moved).

    End: if the target end falls inside a sentence, extend to that
    sentence's end (+ tail pad) when the extension is <= ~6 s; if the
    sentence runs longer, pull back to the previous sentence's end
    instead, provided the clip keeps >= 70% of the requested length and
    the peak stays inside. An end that already falls in a pause between
    sentences is left alone.
    Start: move to the beginning of the sentence containing the target
    start; if that shift would exceed ~4 s, fall forward to the next
    sentence start instead (peak must stay inside the window). Shifts
    are capped at ~4 s either way."""
    start, end = req_start, req_end
    start_moved = end_moved = False
    sentences = build_sentences(words)
    if not sentences:
        return start, end, start_moved, end_moved

    # ---- end snap ------------------------------------------------------
    for i, s in enumerate(sentences):
        if s["start"] < req_end < s["end"]:
            # Tail pad after the sentence's last word, but capped at the
            # next sentence's first word: when the speaker rolls straight
            # on without a pause, an uncapped pad would swallow the
            # first word(s) of the next sentence and the clip would
            # still end mid-thought.
            ext_end = s["end"] + SNAP_TAIL_PAD
            if i + 1 < len(sentences):
                ext_end = min(ext_end, sentences[i + 1]["start"])
            if ext_end - req_end <= SNAP_END_MAX_EXTEND:
                end = min(duration, ext_end)
                end_moved = end != req_end
            elif i > 0:
                prev_end = min(sentences[i - 1]["end"] + SNAP_TAIL_PAD,
                               s["start"])
                if (prev_end > peak
                        and prev_end - req_start >= 0.7 * clip_len):
                    end = prev_end
                    end_moved = True
            break

    # ---- start snap ----------------------------------------------------
    for i, s in enumerate(sentences):
        if s["start"] <= req_start < s["end"]:
            back = s["start"]
            fwd = (sentences[i + 1]["start"]
                   if i + 1 < len(sentences) else None)
            cand = None
            if req_start - back <= SNAP_MAX_SHIFT:
                cand = back
            elif (fwd is not None and fwd - req_start <= SNAP_MAX_SHIFT
                    and fwd <= peak):
                cand = fwd
            if cand is not None and cand < end:
                start = cand
                start_moved = start != req_start
            break

    return start, end, start_moved, end_moved


# --------------------------------------------------------------------------
# Step 3 - render one vertical clip
# --------------------------------------------------------------------------

_WM_POSITIONS = {
    "bottom-right": ("W-w-16", "H-h-16"),
    "bottom-left": ("16", "H-h-16"),
    "top-right": ("W-w-16", "16"),
    "top-left": ("16", "16"),
}


def render_clip(video_path, start, end, layout, ass_path, fontsdir,
                out_path, zoom=True, progress=True, watermark=None):
    """Render one vertical clip: layout -> gentle push-in zoom -> captions
    + hook + name card (libass) -> yellow progress bar -> optional
    campaign watermark (drawn last, so nothing ever covers it), with
    loudness-normalised audio (-14 LUFS / -1.5 dBTP house standard).

    watermark: None, or {"path", "position", "opacity", "width"}."""
    dur = end - start
    if layout == "fill":
        base = ("scale=720:1280:force_original_aspect_ratio=increase,"
                "crop=720:1280")
    else:  # fit: whole frame over a blurred, filled background
        base = ("split[a][b];"
                "[a]scale=720:1280:force_original_aspect_ratio=increase,"
                "crop=720:1280,boxblur=20:5[bg];"
                "[b]scale=720:1280:force_original_aspect_ratio=decrease[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2")
    vf = base
    if zoom:
        # Slow push-in, 1.00 -> 1.07 across the clip: scale the frame up
        # per-frame (eval=frame) and centre-crop back to 720x1280.
        # (crop's w/h expressions reject 't' at configure time, so the
        # animated scale + fixed crop is the robust route.) Applied
        # BEFORE the captions so text stays rock-steady while the
        # picture breathes.
        vf += (",scale=w='720*(1+0.07*t/%.2f)':h='1280*(1+0.07*t/%.2f)'"
               ":eval=frame,crop=720:1280" % (dur, dur))
    vf += ",ass=%s" % ass_path
    if fontsdir:
        vf += ":fontsdir=%s" % fontsdir

    cmd = ["ffmpeg", "-v", "error", "-y",
           "-ss", "%.2f" % start, "-i", video_path]
    if progress or watermark:
        # Yellow progress bar, drawn AFTER the captions: a solid yellow
        # strip (extra input) slid in from the left by an overlay whose
        # x expression tracks the frame timestamp, so the visible width
        # grows left-to-right with playback. (drawbox cannot do this -
        # in this ffmpeg build its w expression is evaluated only once.)
        # The campaign watermark is overlaid after the bar, so it stays
        # visible for the whole clip and is never covered.
        fc = [vf + "[main]"]
        cur = "main"
        idx = 1
        if progress:
            cmd += ["-f", "lavfi", "-i",
                    "color=c=0xFFFF00:s=720x6:r=30:d=%.2f" % (dur + 1.0)]
            fc.append("[%s][%d:v]overlay=x='-720+720*t/%.2f':y=1274[pg]"
                      % (cur, idx, dur))
            idx += 1
            cur = "pg"
        if watermark:
            cmd += ["-loop", "1", "-framerate", "30",
                    "-t", "%.2f" % (dur + 1.0), "-i", watermark["path"]]
            wx, wy = _WM_POSITIONS.get(
                watermark.get("position", "bottom-right"),
                _WM_POSITIONS["bottom-right"])
            fc.append("[%d:v]scale=%d:-1,format=rgba,"
                      "colorchannelmixer=aa=%.2f[wm]"
                      % (idx, watermark.get("width", 200),
                         watermark.get("opacity", 0.85)))
            fc.append("[%s][wm]overlay=x='%s':y='%s'[vout]" % (cur, wx, wy))
            cur = "vout"
        cmd += ["-filter_complex", ";".join(fc),
                "-map", "[%s]" % cur, "-map", "0:a"]
    else:
        cmd += ["-vf", vf]
    cmd += ["-t", "%.2f" % dur,
            "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            out_path]
    run(cmd)


# --------------------------------------------------------------------------
# Campaign settings, post packs, per-file processing
# --------------------------------------------------------------------------

class Settings(object):
    """Fully resolved run settings for one input file (CLI flags win
    over profile values, which win over the built-in defaults)."""
    pass


def build_settings(args, profile, out_dir, name_card_text=None):
    s = Settings()
    s.profile = profile
    s.out_dir = out_dir
    s.top = args.top
    s.model_name = args.model

    def pick(cli_value, profile_key, default):
        if cli_value is not None:
            return cli_value
        if profile is not None and profile.get(profile_key) is not None:
            return profile[profile_key]
        return default

    # Clip length: profile default, clamped to the profile's min/max.
    cl = (profile or {}).get("clip_length", {}) or {}
    clip_len = args.clip_len if args.clip_len is not None \
        else cl.get("default", 30)
    lo, hi = cl.get("min"), cl.get("max")
    if lo is not None and clip_len < lo:
        print("  clip length raised to the campaign minimum: %ss" % lo)
        clip_len = lo
    if hi is not None and clip_len > hi:
        print("  clip length lowered to the campaign maximum: %ss" % hi)
        clip_len = hi
    s.clip_len = float(clip_len)
    s.layout = pick(args.layout, "layout", "fit")
    s.language = pick(args.language, "language", None)
    s.lead_in = float(pick(args.lead_in, "lead_in", 6))
    s.min_gap = float(pick(args.min_gap, "min_gap", 60))

    hook_off = bool((profile or {}).get("hook", {})
                    and profile["hook"].get("enabled") is False)
    s.no_hook = args.no_hook or hook_off
    # Optional forced hook text (--hook): when set, this exact text is
    # used as the hook title card instead of the auto-built one. It is
    # intended for single-clip re-renders where a human has approved
    # the wording; default behaviour is unchanged when it is absent.
    s.hook_override = getattr(args, "hook", None)
    s.no_pop = args.no_pop
    s.no_zoom = args.no_zoom
    s.no_progress = args.no_progress
    s.no_snap = args.no_snap

    # Watermark (campaign only). A configured-but-missing file is a
    # loud warning, not a crash: clips are still made, and every post
    # pack carries the warning so nothing un-watermarked gets posted
    # by mistake.
    s.watermark = None
    s.watermark_missing = False
    if profile is not None:
        s.watermark, s.watermark_missing = resolve_watermark(profile)
        if s.watermark_missing:
            print("  *** WARNING: campaign watermark file is missing "
                  "(%s) - clips will have NO watermark ***"
                  % profile["watermark"].get("file"))

    # Name card (campaign only): CLI --name-card wins, then the batch
    # folder's name_card.txt, then the profile's default text.
    s.name_card_text = None
    s.name_card_dur = 2.0
    nc = (profile or {}).get("name_card")
    if nc:
        s.name_card_dur = float(nc.get("duration", 2.0))
        s.name_card_text = name_card_text or nc.get("default_text")
        if not s.name_card_text:
            print("  *** WARNING: this campaign wants a guest name card "
                  "but no name was supplied (--name-card) - clips will "
                  "have NO name card ***")
    return s


def suggest_title(hook_text):
    """Turn the ALL-CAPS hook text into a readable suggested post
    title. It is a suggestion - the poster edits it freely."""
    t = re.sub(r"\s+", " ", hook_text.replace(r"\N", " ")).strip()
    if not t:
        return ""
    return " ".join(w[:1].upper() + w[1:].lower() if w else w
                    for w in t.split(" "))


def write_post_pack(meta, profile, out_dir):
    """Write <clip>.post.txt next to a campaign clip: suggested title
    (from the hook) + the campaign's post text/tags from its profile,
    ready to copy when posting by hand. This tool never posts."""
    post = profile.get("post", {}) or {}
    title = suggest_title(meta.get("hook", ""))
    template = post.get("text_template", "")
    body = template.replace("{title}", title) if template else title
    lines = ["POST PACK - %s"
             % profile.get("display_name", profile.get("_profile_name")),
             "Clip: %s (%.1fs)" % (meta["clip"], meta["duration"])]
    if meta.get("watermark_missing"):
        lines.append("WARNING: the campaign watermark file was MISSING "
                     "when this clip was made - do NOT post it until "
                     "the real watermark has been added.")
    if profile.get("name_card") and not meta.get("name_card"):
        lines.append("WARNING: this campaign wants the guest's name on "
                     "screen at the start, but no name was supplied - "
                     "do NOT post until a name card is added.")
    lines += ["",
              "Suggested title:",
              title or "(no hook text - write your own title)",
              "",
              "Post text (copy-paste):",
              body]
    if post.get("youtube_tags"):
        lines += ["", "YouTube tags: %s" % post["youtube_tags"]]
    if post.get("instagram_tags"):
        lines.append("Instagram tags: %s" % post["instagram_tags"])
    notes = profile.get("post_notes", [])
    if notes:
        lines += ["", "Remember:"] + ["- %s" % n for n in notes]
    lines += ["",
              "Posting is manual: review the clip first, then post it "
              "from your phone. This tool never posts, submits or "
              "uploads anything."]
    path = os.path.join(out_dir,
                        os.path.splitext(meta["clip"])[0] + ".post.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def process_file(src, s, model, font_name, fontsdir):
    """Make all clips for one input file under resolved settings `s`.
    Writes clips + per-clip JSON (+ post packs in campaign mode) +
    summary.json into s.out_dir. Returns the list of clip metadata.
    Raises on file-level errors (batch mode catches and continues)."""
    src = os.path.abspath(src)
    if not os.path.exists(src):
        raise RuntimeError("file not found: %s" % src)
    if not probe_has_audio(src):
        raise RuntimeError("no audio stream in %s" % src)

    duration = probe_duration(src)
    print("source: %s (%.1f s)" % (os.path.basename(src), duration))

    os.makedirs(s.out_dir, exist_ok=True)
    out_dir = os.path.abspath(s.out_dir)

    # --- moments -------------------------------------------------------
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        full_wav = tmp.name
    try:
        extract_audio_wav(src, full_wav)
        audio, sr = load_wav_mono(full_wav)
    finally:
        if os.path.exists(full_wav):
            os.unlink(full_wav)

    moments = find_moments(audio, sr, s.top, s.min_gap, s.clip_len)
    if not moments:
        raise RuntimeError("no moments found (file too short or silent?)")
    print("picked %d moment(s):" % len(moments))
    for m in moments:
        print("  peak at %6.1f s  score %.3f" % (m["peak"], m["score"]))

    # --- clips -----------------------------------------------------------
    results = []
    for i, m in enumerate(moments, 1):
        req_start = max(0.0, m["peak"] - s.lead_in)
        req_end = min(duration, req_start + s.clip_len)
        if req_end - req_start < s.clip_len and req_start > 0:
            req_start = max(0.0, req_end - s.clip_len)
        start, end = req_start, req_end
        start_snapped = end_snapped = False

        if s.no_snap:
            words = transcribe_span(model, src, start, end, s.language)
        else:
            # Transcribe the window plus margin, snap both edges to
            # sentence boundaries, then keep the words that fall inside
            # the final window (converted to clip-local times).
            reg_start = max(0.0, req_start - SNAP_MARGIN)
            reg_end = min(duration, req_end + SNAP_MARGIN)
            region = transcribe_span(model, src, reg_start, reg_end,
                                     s.language)
            words_abs = [(reg_start + a, reg_start + b, t)
                         for (a, b, t) in region]
            start, end, start_snapped, end_snapped = snap_window(
                words_abs, req_start, req_end, m["peak"], s.clip_len,
                duration)
            dur_local = end - start
            words = []
            for (a, b, t) in words_abs:
                if b <= start or a >= end:
                    continue
                words.append((max(0.0, a - start),
                              min(dur_local, b - start), t))
        if s.no_hook:
            hook_text = ""
        elif getattr(s, "hook_override", None):
            hook_text = wrap_hook_text(re.sub(
                r"\s+", " ", s.hook_override).strip().upper())
        else:
            hook_text = build_hook_text(words, m["peak"] - start)
        with tempfile.NamedTemporaryFile(suffix=".ass", delete=False,
                                         dir=out_dir) as tmp:
            ass_path = tmp.name
        try:
            n_phrases = build_ass(words, ass_path, font_name,
                                  hook_text=hook_text,
                                  clip_dur=end - start,
                                  pop=not s.no_pop,
                                  name_card=s.name_card_text,
                                  name_card_dur=s.name_card_dur)
            name = "clip-%02d.mp4" % i
            out_path = os.path.join(out_dir, name)
            render_clip(src, start, end, s.layout, ass_path, fontsdir,
                        out_path, zoom=not s.no_zoom,
                        progress=not s.no_progress,
                        watermark=s.watermark)
        finally:
            if os.path.exists(ass_path):
                os.unlink(ass_path)

        meta = {
            "clip": name,
            "source": src,
            "campaign": (s.profile.get("_profile_name")
                         if s.profile else None),
            "requested_start": round(req_start, 2),
            "requested_end": round(req_end, 2),
            "start": round(start, 2),
            "end": round(end, 2),
            "start_snapped": start_snapped,
            "end_snapped": end_snapped,
            "duration": round(end - start, 2),
            "moment_peak": m["peak"],
            "score": m["score"],
            "layout": s.layout,
            "caption_words": len(words),
            "caption_phrases": n_phrases,
            "font": font_name,
            "hook": hook_text.replace(r"\N", " "),
            "name_card": s.name_card_text,
            "watermark": (s.watermark["path"] if s.watermark else None),
            "watermark_missing": bool(s.watermark_missing),
            "editing": {
                "zoom": not s.no_zoom,
                "progress_bar": not s.no_progress,
                "word_pop": not s.no_pop,
                "loudnorm_lufs": -14,
            },
        }
        with open(os.path.join(out_dir, "clip-%02d.json" % i), "w") as f:
            json.dump(meta, f, indent=2)
        if s.profile is not None:
            write_post_pack(meta, s.profile, out_dir)
        results.append(meta)
        print("  wrote %s  [%.1f-%.1f s]  %d caption words"
              % (name, start, end, len(words)))
        if not s.no_snap:
            print("    snap: requested %.1f-%.1f -> start %s, end %s"
                  % (req_start, req_end,
                     "moved to %.1f" % start if start_snapped else "kept",
                     "moved to %.1f" % end if end_snapped else "kept"))

    summary = {"source": src, "source_duration": round(duration, 2),
               "campaign": (s.profile.get("_profile_name")
                            if s.profile else None),
               "clips": results}
    with open(os.path.join(out_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print("done: %d clip(s) in %s" % (len(results), out_dir))
    return results


# --------------------------------------------------------------------------
# Batch mode + main
# --------------------------------------------------------------------------

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".m4v"}


def run_batch(args):
    """Process a batch folder: each subfolder is named after a campaign
    profile; every video inside is clipped with that profile into
    <batch-dir>/output/<profile output subfolder>/<video stem>/.
    A subfolder may hold a name_card.txt whose first line is the guest
    name card for all its videos. One bad file never kills the batch -
    it is logged in batch-summary.json and the run continues."""
    batch_dir = os.path.abspath(args.batch_dir)
    if not os.path.isdir(batch_dir):
        print("error: batch dir not found: %s" % batch_dir,
              file=sys.stderr)
        return 1
    out_root = os.path.join(batch_dir, "output")
    os.makedirs(out_root, exist_ok=True)

    entries = []
    jobs = []  # (profile_name, profile, video_path, folder_name_card)
    for sub in sorted(os.listdir(batch_dir)):
        subpath = os.path.join(batch_dir, sub)
        if sub == "output" or sub.startswith(".") \
                or not os.path.isdir(subpath):
            continue
        try:
            profile = load_profile(sub)
        except SystemExit as e:
            print("batch: skipping folder %r - %s" % (sub, e))
            entries.append({"folder": sub, "status": "skipped",
                            "error": str(e)})
            continue
        folder_name_card = None
        ncf = os.path.join(subpath, "name_card.txt")
        if os.path.exists(ncf):
            with open(ncf, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        folder_name_card = line.strip()
                        break
        for fn in sorted(os.listdir(subpath)):
            fpath = os.path.join(subpath, fn)
            if os.path.splitext(fn)[1].lower() in VIDEO_EXTS \
                    and os.path.isfile(fpath):
                jobs.append((sub, profile, fpath, folder_name_card))

    if not jobs:
        print("batch: no videos found under %s" % batch_dir)
        with open(os.path.join(out_root, "batch-summary.json"),
                  "w") as f:
            json.dump({"batch_dir": batch_dir, "entries": entries},
                      f, indent=2)
        return 1

    from faster_whisper import WhisperModel
    print("loading whisper model '%s' (cpu)..." % args.model)
    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    font_name, fontsdir = resolve_font()
    print("caption font: %s%s" % (font_name,
                                  " (fonts/)" if fontsdir else " (system)"))

    ok = failed = 0
    for sub, profile, fpath, folder_name_card in jobs:
        print("\n=== batch: %s / %s ===" % (sub, os.path.basename(fpath)))
        print_campaign(profile)
        stem = os.path.splitext(os.path.basename(fpath))[0]
        out_dir = os.path.join(
            out_root, profile.get("output_subfolder", sub), stem)
        s = build_settings(args, profile, out_dir,
                           name_card_text=folder_name_card
                           or args.name_card)
        try:
            results = process_file(fpath, s, model, font_name, fontsdir)
            entries.append({"profile": sub, "file": fpath,
                            "status": "ok", "output_dir": out_dir,
                            "clips": [r["clip"] for r in results]})
            ok += 1
        except Exception as e:  # one bad file must not kill the batch
            print("batch: FAILED %s: %s" % (fpath, e))
            entries.append({"profile": sub, "file": fpath,
                            "status": "error", "error": str(e)})
            failed += 1

    batch_summary = {"batch_dir": batch_dir,
                     "files_ok": ok, "files_failed": failed,
                     "entries": entries}
    with open(os.path.join(out_root, "batch-summary.json"), "w") as f:
        json.dump(batch_summary, f, indent=2)
    print("\nbatch done: %d file(s) ok, %d failed; summary in %s"
          % (ok, failed, os.path.join(out_root, "batch-summary.json")))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(
        description="ClipForge - local file-mode auto clipper. "
                    "Creates clip files only; never posts anything.")
    ap.add_argument("--file", help="input video file (single-file mode)")
    ap.add_argument("--batch-dir", default=None,
                    help="batch mode: a folder whose subfolders are "
                         "named after campaign profiles; every video "
                         "inside is processed with that profile into "
                         "<batch-dir>/output/<campaign subfolder>/")
    ap.add_argument("--top", type=int, default=3,
                    help="number of clips per file (default 3)")
    ap.add_argument("--clip-len", type=float, default=None,
                    help="clip length in seconds (default 30, or the "
                         "campaign profile's default)")
    ap.add_argument("--lead-in", type=float, default=None,
                    help="seconds before the moment peak (default 6)")
    ap.add_argument("--layout", choices=["fit", "fill"], default=None,
                    help="fit = full frame over blurred background; "
                         "fill = centre crop (default fit)")
    ap.add_argument("--output-dir", default=None,
                    help="where clips go (default ./output, or "
                         "./output/<campaign subfolder> with --campaign)")
    ap.add_argument("--language", default=None,
                    help="caption language, e.g. en or hi "
                         "(default: auto-detect)")
    ap.add_argument("--min-gap", type=float, default=None,
                    help="minimum seconds between picked moments "
                         "(default 60)")
    ap.add_argument("--model", default="small",
                    help="whisper model size (default small)")
    ap.add_argument("--no-hook", action="store_true",
                    help="skip the hook title card in the first ~3 seconds")
    ap.add_argument("--hook", default=None,
                    help="force this exact text as the hook title card "
                         "instead of auto-building it from the words "
                         "around the peak (single-clip re-renders with "
                         "human-approved wording; wrapped to two lines "
                         "when long, like auto-built hooks)")
    ap.add_argument("--no-pop", action="store_true",
                    help="skip the scale pop on the spoken caption word")
    ap.add_argument("--no-zoom", action="store_true",
                    help="skip the gentle push-in zoom on the picture")
    ap.add_argument("--no-progress", action="store_true",
                    help="skip the yellow progress bar at the bottom")
    ap.add_argument("--no-snap", action="store_true",
                    help="skip sentence-aware snapping of clip start/end "
                         "(cut at the exact requested times instead)")
    ap.add_argument("--campaign", default=None,
                    help="campaign profile to use, e.g. iced-coffee-hour "
                         "or ladbible (see profiles/); applies that "
                         "campaign's clip length limits, watermark, "
                         "name card and post template, and writes a "
                         ".post.txt pack next to each clip. Never posts.")
    ap.add_argument("--name-card", default=None,
                    help="guest name for the campaign name card "
                         "(overrides the profile default / batch "
                         "folder's name_card.txt)")
    args = ap.parse_args()

    if args.batch_dir:
        if args.file:
            print("error: use either --file or --batch-dir, not both",
                  file=sys.stderr)
            return 1
        return run_batch(args)
    if not args.file:
        print("error: --file is required (or use --batch-dir)",
              file=sys.stderr)
        return 1

    profile = None
    if args.campaign:
        profile = load_profile(args.campaign)
        print_campaign(profile)

    if args.output_dir:
        out_dir = args.output_dir
    elif profile is not None:
        out_dir = os.path.join(
            "output", profile.get("output_subfolder",
                                  profile["_profile_name"]))
    else:
        out_dir = "output"

    s = build_settings(args, profile, out_dir,
                       name_card_text=args.name_card)
    if not os.path.exists(args.file):
        print("error: file not found: %s" % args.file, file=sys.stderr)
        return 1

    from faster_whisper import WhisperModel
    print("loading whisper model '%s' (cpu)..." % args.model)
    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    font_name, fontsdir = resolve_font()
    print("caption font: %s%s" % (font_name,
                                  " (fonts/)" if fontsdir else " (system)"))
    try:
        process_file(args.file, s, model, font_name, fontsdir)
    except RuntimeError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
