"""Build a HyperFrames video project from script.json + source/template.html.

Usage:
    python tools/make_video.py videos/<slug> [--force-tts] [--no-fonts]

Pipeline:
    1. TTS every scene with Azure Speech (晓晓温柔 by default; cached per text/voice/style/rate/pitch), keeping word timings.
    2. Lay scenes on a timeline (lead + speech + pause), mix narration.mp3 with ffmpeg.
    3. Synthesize a soft ambient bed (bgm.wav) matching the total length.
    4. Split narration into caption chunks timed by word boundaries.
    5. Fill source/template.html placeholders -> index.html.
    6. Subset CJK fonts to exactly the characters used -> assets/fonts/*.woff2.

source/template.html placeholders:
    {{TOTAL}}                 root duration (seconds)
    {{<scene>.start}}         scene start (seconds)
    {{<scene>.dur}}           scene duration (seconds)
    /*@FONTS@*/               replaced with @font-face rules
    /*@TIMING@*/              replaced with `const T = {...};`
    <!--@CAPTIONS@-->         replaced with timed caption clips
    <!--@AUDIO@-->            replaced with narration + bgm <audio> clips

Every build appends a 3.6s end card (longer if script.json outro is larger)
centered on www.chinadong.net. Templates should not add a second one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import time
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT_CACHE = ROOT / "tools" / ".font-cache"
SITE_URL = "www.chinadong.net"
END_CARD_SECONDS = 3.6

DEFAULT_FONTS = {
    "Noto Sans SC": {
        400: "https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf",
        700: "https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@main/Sans/SubsetOTF/SC/NotoSansSC-Bold.otf",
        900: "https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@main/Sans/SubsetOTF/SC/NotoSansSC-Black.otf",
    },
    "Noto Serif SC": {
        700: "https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@main/Serif/SubsetOTF/SC/NotoSerifSC-Bold.otf",
        900: "https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@main/Serif/SubsetOTF/SC/NotoSerifSC-Black.otf",
    },
    "Ma Shan Zheng": {
        400: "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/mashanzheng/MaShanZheng-Regular.ttf",
    },
}

CAPTION_BREAK = "，。！？；：,.!?;:"
CAPTION_MAX = 20


def log(msg: str) -> None:
    print(f"[make_video] {msg}", flush=True)


# ---------------------------------------------------------------- TTS

def _pick(scene: dict, spec: dict, key: str, default: str) -> str:
    if key in scene and scene[key] not in (None, ""):
        return str(scene[key])
    if key in spec and spec[key] not in (None, ""):
        return str(spec[key])
    return default


def voice_profile(spec: dict, scene: dict) -> tuple[str, str, str, str]:
    """Per-scene voice, style, rate, pitch. Default is 晓晓温柔."""
    voice = _pick(scene, spec, "voice", "zh-CN-XiaoxiaoNeural")
    if "style" in scene:
        style = str(scene.get("style") or "")
    elif "voice" not in scene and "style" in spec:
        style = str(spec.get("style") or "")
    elif voice == "zh-CN-XiaoxiaoNeural":
        style = "gentle"
    else:
        style = ""
    rate = _pick(scene, spec, "rate", "0.95")
    pitch = _pick(scene, spec, "pitch", "0%")
    return voice, style, rate, pitch


def tts(text: str, voice: str, style: str, rate: str, pitch: str, out_mp3: Path) -> list[dict]:
    tools_dir = str(Path(__file__).resolve().parent)
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from azure_tts import build_ssml, synthesize

    meta = out_mp3.with_suffix(".json")
    key = hashlib.sha1(f"azure|{voice}|{style}|{rate}|{pitch}|{text}".encode()).hexdigest()
    if out_mp3.exists() and meta.exists():
        cached = json.loads(meta.read_text("utf-8"))
        if cached.get("key") == key:
            return cached["words"]
    ssml = build_ssml(text, voice, style, rate, pitch)
    last = None
    for attempt in range(6):
        try:
            audio, words = synthesize(ssml)
            out_mp3.write_bytes(audio)
            meta.write_text(json.dumps({"key": key, "words": words}, ensure_ascii=False, indent=1), "utf-8")
            return words
        except Exception as e:
            last = e
            log(f"  tts retry {attempt + 1}: {e.__class__.__name__}: {e}")
            time.sleep(2 + attempt * 2)
    raise RuntimeError(f"TTS failed: {last}")


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


# ---------------------------------------------------------------- timing

def locate_words(text: str, words: list[dict]) -> list[dict]:
    cursor = 0
    located = []
    for w in words:
        pos = text.find(w["w"], cursor)
        if pos < 0:
            continue
        located.append({**w, "pos": pos})
        cursor = pos + len(w["w"])
    return located


def time_at_char(located: list[dict], char_pos: int, fallback: float) -> float:
    for w in located:
        if w["pos"] >= char_pos:
            return w["t"]
    return fallback


def split_captions(text: str) -> list[tuple[int, int]]:
    """Return (start, end) char ranges, broken at punctuation and capped in length."""
    spans = []
    start = 0
    for i, ch in enumerate(text):
        if ch in CAPTION_BREAK:
            if ch in ".," and 0 < i < len(text) - 1 and text[i - 1].isdigit() and text[i + 1].isdigit():
                continue
            spans.append((start, i + 1))
            start = i + 1
    if start < len(text):
        spans.append((start, len(text)))

    merged: list[list[int]] = []
    for s, e in spans:
        if merged and (e - merged[-1][0]) <= 9:
            merged[-1][1] = e
        else:
            merged.append([s, e])

    result = []
    for s, e in merged:
        while e - s > CAPTION_MAX:
            cut = s + CAPTION_MAX
            for j in range(s + CAPTION_MAX, s + 8, -1):
                if text[j - 1] in "、和与及的":
                    cut = j
                    break
            result.append((s, cut))
            s = cut
        result.append((s, e))
    return [(s, e) for s, e in result if text[s:e].strip(CAPTION_BREAK + " ")]


def clean_caption(s: str) -> str:
    return s.strip().rstrip("，。；：,.;:")


# ---------------------------------------------------------------- audio

def mix_narration(segments: list[tuple[Path, float]], total: float, out: Path) -> None:
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for path, _ in segments:
        cmd += ["-i", str(path)]
    parts = []
    for i, (_, start) in enumerate(segments):
        ms = int(round(start * 1000))
        parts.append(f"[{i}:a]aresample=48000,adelay={ms}|{ms}[a{i}]")
    inputs = "".join(f"[a{i}]" for i in range(len(segments)))
    parts.append(f"{inputs}amix=inputs={len(segments)}:normalize=0,apad,atrim=0:{total:.3f}[out]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[out]", "-ac", "1", "-b:a", "160k", str(out)]
    subprocess.run(cmd, check=True)


def synth_bgm(total: float, out: Path, seed: int = 7) -> None:
    """Warm, unobtrusive pad + sparse music-box arpeggio in C major (deterministic)."""
    import numpy as np

    sr = 44100
    n = int(total * sr)
    t = np.arange(n) / sr
    rng = np.random.default_rng(seed)

    def hz(midi: float) -> float:
        return 440.0 * 2 ** ((midi - 69) / 12)

    # Cmaj7 - Am7 - Fmaj7 - G6, 4 bars of 4s each
    chords = [[48, 55, 59, 64], [45, 52, 55, 60], [41, 48, 52, 57], [43, 50, 55, 59]]
    bar = 4.0
    pad = np.zeros(n)
    for k in range(int(math.ceil(total / bar)) + 1):
        chord = chords[k % 4]
        s0 = k * bar - 0.8
        seg_start = max(0, int(s0 * sr))
        seg_end = min(n, int((s0 + bar + 1.6) * sr))
        if seg_start >= seg_end:
            continue
        tt = t[seg_start:seg_end] - s0
        env = np.clip(tt / 1.2, 0, 1) * np.clip((bar + 1.6 - tt) / 1.2, 0, 1)
        env = env ** 1.5
        for note in chord:
            f = hz(note + 12)
            for det in (-0.12, 0.12):
                ph = rng.uniform(0, 2 * np.pi)
                pad[seg_start:seg_end] += env * 0.05 * np.sin(2 * np.pi * f * (1 + det / 100) * tt + ph)
            pad[seg_start:seg_end] += env * 0.012 * np.sin(2 * np.pi * f * 2 * tt)

    bell = np.zeros(n)
    step = 0.5
    pattern = [0, 2, 1, 3, 2, 1, 3, 0]
    for i in range(int(total / step)):
        if i % 8 in (5, 7):
            continue
        k = int(i * step / bar)
        chord = chords[k % 4]
        note = chord[pattern[i % 8]] + 24
        s = int(i * step * sr)
        length = int(1.6 * sr)
        e = min(n, s + length)
        tt = t[s:e] - i * step
        f = hz(note)
        env = np.exp(-tt * 3.2) * np.clip(tt / 0.008, 0, 1)
        bell[s:e] += 0.035 * env * (np.sin(2 * np.pi * f * tt) + 0.25 * np.sin(2 * np.pi * f * 2.01 * tt))

    mix = pad + bell
    kernel = np.hanning(9)
    y = np.convolve(mix, kernel / kernel.sum(), mode="same")
    fade = np.minimum(1, np.minimum(t / 3.0, (total - t) / 4.0)).clip(0, 1)
    y *= fade
    y /= max(1e-9, np.abs(y).max())
    y *= 0.5
    stereo = np.stack([y, np.roll(y, int(0.012 * sr))], axis=1)
    pcm = (stereo * 32767).astype("<i2")
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def synth_bgm_space(total: float, out: Path, seed: int = 11) -> None:
    """Cold documentary ambience: low drone, slow minor pads, airy noise and sparse sonar pings."""
    import numpy as np

    sr = 44100
    n = int(total * sr)
    t = np.arange(n) / sr
    rng = np.random.default_rng(seed)

    def hz(midi: float) -> float:
        return 440.0 * 2 ** ((midi - 69) / 12)

    drone = np.zeros(n)
    for midi, amp in ((38, 0.10), (45, 0.05), (50, 0.035)):
        f = hz(midi)
        lfo = 0.75 + 0.25 * np.sin(2 * np.pi * t / (17 + midi % 5) + rng.uniform(0, 6.28))
        drone += amp * lfo * (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * f * 2.003 * t))

    # Dm(add9) - Bbmaj7 - Fmaj7 - Am7(add11), 8s each, long crossfades
    chords = [[62, 65, 69, 76], [58, 62, 65, 69], [53, 60, 64, 69], [57, 60, 64, 67]]
    bar = 8.0
    pad = np.zeros(n)
    for k in range(int(math.ceil(total / bar)) + 1):
        chord = chords[k % 4]
        s0 = k * bar - 2.0
        a = max(0, int(s0 * sr))
        b = min(n, int((s0 + bar + 4.0) * sr))
        if a >= b:
            continue
        tt = t[a:b] - s0
        env = np.sin(np.pi * np.clip(tt / (bar + 4.0), 0, 1)) ** 2
        for note in chord:
            f = hz(note)
            for det in (-0.18, 0.0, 0.18):
                ph = rng.uniform(0, 2 * np.pi)
                pad[a:b] += env * 0.018 * np.sin(2 * np.pi * f * (1 + det / 100) * tt + ph)

    noise = rng.standard_normal(n)
    k = np.hanning(801)
    air = np.convolve(noise, k / k.sum(), mode="same")
    air *= 0.9 * (0.6 + 0.4 * np.sin(2 * np.pi * t / 23.0))

    ping = np.zeros(n)
    for i, s in enumerate(np.arange(6.0, total - 4, 12.0)):
        a = int(s * sr)
        b = min(n, a + int(5 * sr))
        tt = t[a:b] - s
        f = hz(81 if i % 2 == 0 else 76)
        env = np.exp(-tt * 1.1) * np.clip(tt / 0.01, 0, 1)
        ping[a:b] += 0.03 * env * np.sin(2 * np.pi * f * tt)
        echo = int(0.42 * sr)
        if b + echo < n:
            ping[a + echo:b + echo] += 0.012 * env * np.sin(2 * np.pi * f * tt)

    y = drone + pad + air + ping
    fade = np.minimum(1, np.minimum(t / 4.0, (total - t) / 5.0)).clip(0, 1)
    y *= fade
    y /= max(1e-9, np.abs(y).max())
    y *= 0.5
    stereo = np.stack([y, np.roll(y, int(0.018 * sr))], axis=1)
    pcm = (stereo * 32767).astype("<i2")
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


BGM_STYLES = {"warm": synth_bgm, "space": synth_bgm_space}


# ---------------------------------------------------------------- fonts

def fetch(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"  downloading {url.rsplit('/', 1)[-1]}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        dest.write_bytes(r.read())
    return dest


def end_card_markup(start: float, dur: float) -> str:
    return f"""<section id="endcard" class="clip" data-start="{start:.3f}" data-duration="{dur:.3f}" data-track-index="8" style="position:absolute;left:0;right:0;top:0;bottom:8px;background:#fbf5ec;z-index:4;">
        <div style="position:absolute;inset:0;background-image:radial-gradient(rgba(34,37,58,.08) 2px, transparent 2.5px);background-size:40px 40px;"></div>
        <div class="endcard-inner" style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:0 72px 36px;opacity:0;">
          <div style="font-family:'Noto Sans SC',sans-serif;font-weight:700;font-size:26px;letter-spacing:.42em;color:#4c536c;">更多内容</div>
          <div style="margin-top:20px;font-family:'Noto Sans SC',sans-serif;font-weight:700;font-size:64px;letter-spacing:.01em;color:#22253a;">{SITE_URL}</div>
          <div style="margin-top:28px;width:88px;height:6px;border-radius:3px;background:#e8553f;"></div>
        </div>
      </section>"""


def end_card_script() -> str:
    return """<script>
      (function () {
        var roots = window.__timelines || {};
        var keys = Object.keys(roots);
        var tl = roots.main || (keys.length ? roots[keys[0]] : null);
        var card = document.querySelector("#endcard .endcard-inner");
        var node = document.getElementById("endcard");
        if (!tl || !card || !node) return;
        tl.fromTo(card, { opacity: 0, y: 26 }, { opacity: 1, y: 0, duration: 0.7, ease: "power3.out" }, Number(node.dataset.start));
        tl.seek(0);
      })();
    </script>"""


def visible_text(html: str) -> str:
    body = re.sub(r"<script[\s\S]*?</script>", " ", html)
    body = re.sub(r"<style[\s\S]*?</style>", " ", body)
    body = re.sub(r"<[^>]+>", " ", body)
    scripts = " ".join(re.findall(r"<script[^>]*>([\s\S]*?)</script>", html))
    strings = " ".join(re.findall(r"[\"'`]([^\"'`\n]*)[\"'`]", scripts))
    return body + strings


def subset_fonts(project: Path, families: dict, text: str) -> str:
    from fontTools import subset

    chars = set(text) | set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ%+-→·•…—“”‘’《》（）()！？，。、：；…")
    chars = "".join(sorted(c for c in chars if not c.isspace()))
    out_dir = project / "assets" / "fonts"
    out_dir.mkdir(parents=True, exist_ok=True)
    rules = []
    for family, weights in families.items():
        for weight, url in weights.items():
            src = fetch(url, FONT_CACHE / url.rsplit("/", 1)[-1])
            slug = re.sub(r"\W+", "-", family.lower()).strip("-") + f"-{weight}.woff2"
            dest = out_dir / slug
            opts = subset.Options()
            opts.flavor = "woff2"
            opts.layout_features = ["*"]
            opts.name_IDs = ["*"]
            opts.notdef_outline = True
            font = subset.load_font(str(src), opts)
            sub = subset.Subsetter(opts)
            sub.populate(text=chars)
            sub.subset(font)
            subset.save_font(font, str(dest), opts)
            rules.append(
                f'@font-face {{ font-family: "{family}"; font-weight: {weight}; font-style: normal; '
                f'font-display: block; src: url("assets/fonts/{slug}") format("woff2"); }}'
            )
    return "\n      ".join(rules)


# ---------------------------------------------------------------- build

def build(project: Path, force_tts: bool, with_fonts: bool) -> None:
    spec = json.loads((project / "script.json").read_text("utf-8"))
    lead_default = spec.get("lead", 0.35)
    pause_default = spec.get("pause", 0.7)

    vo_dir = project / "assets" / "vo"
    vo_dir.mkdir(parents=True, exist_ok=True)
    if force_tts:
        for p in vo_dir.glob("*"):
            p.unlink()

    timing: dict = {}
    segments = []
    captions = []
    cursor = 0.0
    for scene in spec["scenes"]:
        sid = scene["id"]
        text = scene["text"]
        voice, style, rate, pitch = voice_profile(spec, scene)
        style_label = style or "plain"
        log(f"scene {sid}: {len(text)} chars, {voice} / {style_label} / rate {rate}")
        mp3 = vo_dir / f"{sid}.mp3"
        words = tts(text, voice, style, rate, pitch, mp3)
        speech = probe_duration(mp3)
        lead = scene.get("lead", lead_default)
        pause = scene.get("pause", pause_default)
        start = round(cursor, 3)
        vo_start = start + lead
        dur = round(lead + speech + pause, 3)
        located = locate_words(text, words)

        cues = {}
        for name, phrase in scene.get("cues", {}).items():
            pos = text.find(phrase)
            if pos < 0:
                raise ValueError(f"cue {sid}.{name}: phrase {phrase!r} not in narration")
            cues[name] = round(vo_start + time_at_char(located, pos, speech), 3)

        spans = split_captions(text)
        starts = [vo_start + time_at_char(located, s, speech) for s, _ in spans]
        for i, (s, e) in enumerate(spans):
            c_start = starts[i]
            c_end = starts[i + 1] if i + 1 < len(spans) else min(vo_start + speech + 0.25, start + dur)
            captions.append({"text": clean_caption(text[s:e]), "start": round(c_start, 3), "dur": round(max(0.3, c_end - c_start), 3)})

        timing[sid] = {"start": start, "dur": dur, "end": round(start + dur, 3), "vo": round(vo_start, 3), "speech": round(speech, 3), "cues": cues}
        segments.append((mp3, vo_start))
        cursor += dur

    end_at = cursor
    outro = max(float(spec.get("outro") or 0), END_CARD_SECONDS)
    total = round(cursor + outro, 3)
    log(f"total duration {total:.1f}s ({total / 60:.2f} min)")

    narration = project / "assets" / "narration.mp3"
    mix_narration(segments, total, narration)
    bgm_cfg = spec.get("bgm", {"volume": 0.16})
    bgm_path = project / "assets" / "bgm.mp3"
    if bgm_cfg:
        log("synthesizing bgm")
        wav = project / "assets" / "bgm.wav"
        BGM_STYLES[bgm_cfg.get("style", "warm")](total, wav)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-b:a", "160k", str(bgm_path)], check=True)
        wav.unlink()

    html = (project / "source" / "template.html").read_text("utf-8")
    if 'id="endcard"' not in html:
        if "<!--@AUDIO@-->" not in html:
            raise ValueError("template missing <!--@AUDIO@-->; cannot place the end card")
        html = html.replace("<!--@AUDIO@-->", end_card_markup(end_at, outro) + "\n      <!--@AUDIO@-->", 1)
        html = html.replace("</body>", end_card_script() + "\n  </body>", 1)
    html = html.replace("{{TOTAL}}", f"{total:.3f}")
    for sid, info in timing.items():
        html = html.replace(f"{{{{{sid}.start}}}}", f"{info['start']:.3f}")
        html = html.replace(f"{{{{{sid}.dur}}}}", f"{info['dur']:.3f}")
    html = html.replace("/*@TIMING@*/", "const T = " + json.dumps(timing, ensure_ascii=False) + ";")

    cap_html = "\n      ".join(
        f'<div id="cap-{i:03d}" class="clip caption" data-start="{c["start"]:.3f}" data-duration="{c["dur"]:.3f}" data-track-index="20"><span>{c["text"]}</span></div>'
        for i, c in enumerate(captions)
    )
    html = html.replace("<!--@CAPTIONS@-->", cap_html)

    audio = [f'<audio id="narration" src="assets/narration.mp3" data-start="0" data-duration="{total:.3f}" data-track-index="30" data-volume="1"></audio>']
    if bgm_cfg:
        audio.append(f'<audio id="bgm" src="assets/bgm.mp3" data-start="0" data-duration="{total:.3f}" data-track-index="31" data-volume="{bgm_cfg.get("volume", 0.16)}"></audio>')
    html = html.replace("<!--@AUDIO@-->", "\n      ".join(audio))

    leftover = re.findall(r"\{\{[^}]+\}\}", html)
    if leftover:
        raise ValueError(f"unfilled placeholders: {sorted(set(leftover))}")

    if with_fonts:
        log("subsetting fonts")
        families = {k: {int(w): u for w, u in v.items()} for k, v in spec.get("fonts", {}).items()} or DEFAULT_FONTS
        html = html.replace("/*@FONTS@*/", subset_fonts(project, families, visible_text(html)))

    (project / "index.html").write_text(html, "utf-8")
    (project / "timing.json").write_text(json.dumps({"total": total, "scenes": timing, "captions": captions}, ensure_ascii=False, indent=1), "utf-8")
    log(f"wrote index.html ({len(captions)} captions)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--force-tts", action="store_true")
    ap.add_argument("--no-fonts", action="store_true")
    args = ap.parse_args()
    project = Path(args.project).resolve()
    if not (project / "script.json").exists():
        sys.exit(f"missing {project / 'script.json'}")
    build(project, args.force_tts, not args.no_fonts)


if __name__ == "__main__":
    main()
