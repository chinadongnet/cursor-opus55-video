"""Azure Speech synthesis for the video pipeline.

Default voice is 晓晓温柔: zh-CN-XiaoxiaoNeural, style gentle.
Credentials come from SPEECH_KEY / SPEECH_REGION, or tools/.azure-speech.local.json.

Word timings need the Speech SDK (REST only returns audio). This interpreter
is often newer than the SDK wheels, so synthesis falls back to Python 3.12.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
LOCAL_CONFIG = Path(__file__).resolve().parent / ".azure-speech.local.json"
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_STYLE = "gentle"
DEFAULT_RATE = "0.95"
DEFAULT_PITCH = "0%"

_SDK_PYTHON: list[str] | None = None
_SDK_PYTHON_READY = False


def xml_text(value: str) -> str:
    return escape(str(value), {"'": "&apos;", '"': "&quot;"})


def build_ssml(text: str, voice: str, style: str, rate: str, pitch: str) -> str:
    body = f'<prosody rate="{xml_text(rate)}" pitch="{xml_text(pitch)}">{xml_text(text)}</prosody>'
    if style:
        body = f'<mstts:express-as style="{xml_text(style)}">{body}</mstts:express-as>'
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="zh-CN">'
        f'<voice name="{xml_text(voice)}">{body}</voice></speak>'
    )


def load_credentials() -> tuple[str, str]:
    key = os.environ.get("SPEECH_KEY") or os.environ.get("AZURE_SPEECH_KEY") or ""
    region = os.environ.get("SPEECH_REGION") or os.environ.get("AZURE_SPEECH_REGION") or ""
    if LOCAL_CONFIG.exists():
        data = json.loads(LOCAL_CONFIG.read_text("utf-8"))
        key = key or str(data.get("key") or "")
        region = region or str(data.get("region") or "")
    region = region or "eastasia"
    if not key:
        raise RuntimeError(
            "缺少 Azure Speech 密钥。设置 SPEECH_KEY，或写入 tools/.azure-speech.local.json"
        )
    return key, region


def _duration_seconds(evt) -> float:
    dur = getattr(evt, "duration", None)
    if dur is None:
        return 0.0
    if hasattr(dur, "total_seconds"):
        return float(dur.total_seconds())
    return float(dur) / 10_000_000


def _sdk_synthesize(ssml: str) -> tuple[bytes, list[dict]]:
    import azure.cognitiveservices.speech as speechsdk

    key, region = load_credentials()
    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    speech_config.speech_synthesis_voice_name = DEFAULT_VOICE
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3
    )
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
    words: list[dict] = []

    def on_word(evt) -> None:
        text = (evt.text or "").strip()
        if not text:
            return
        words.append({
            "t": round(evt.audio_offset / 10_000_000, 3),
            "d": round(_duration_seconds(evt), 3),
            "w": text,
        })

    synthesizer.synthesis_word_boundary.connect(on_word)
    result = synthesizer.speak_ssml_async(ssml).get()
    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        details = ""
        if result.reason == speechsdk.ResultReason.Canceled:
            cancellation = result.cancellation_details
            details = f"{cancellation.reason}: {cancellation.error_details}"
        raise RuntimeError(f"Azure TTS failed: {result.reason} {details}".strip())
    if not result.audio_data:
        raise RuntimeError("Azure TTS returned no audio")
    return bytes(result.audio_data), words


def _sdk_imports() -> bool:
    try:
        import azure.cognitiveservices.speech  # noqa: F401
    except ImportError:
        return False
    return True


def _find_sdk_python() -> list[str] | None:
    global _SDK_PYTHON, _SDK_PYTHON_READY
    if _SDK_PYTHON_READY:
        return _SDK_PYTHON
    _SDK_PYTHON_READY = True
    if _sdk_imports():
        _SDK_PYTHON = [sys.executable]
        return _SDK_PYTHON
    candidates = [
        ["py", "-3.12"],
        [r"C:\Users\jiang\AppData\Local\Programs\Python\Python312\python.exe"],
    ]
    for cmd in candidates:
        try:
            probe = subprocess.run(
                cmd + ["-c", "import azure.cognitiveservices.speech"],
                capture_output=True, text=True,
            )
        except FileNotFoundError:
            continue
        if probe.returncode == 0:
            _SDK_PYTHON = cmd
            return _SDK_PYTHON
    _SDK_PYTHON = None
    return None


def synthesize(ssml: str) -> tuple[bytes, list[dict]]:
    cmd = _find_sdk_python()
    if not cmd:
        raise RuntimeError(
            "当前 Python 没有 azure-cognitiveservices-speech，也找不到带该 SDK 的 Python 3.12"
        )
    if cmd == [sys.executable]:
        return _sdk_synthesize(ssml)

    import tempfile

    with tempfile.TemporaryDirectory(prefix="azure-tts-") as tmp:
        folder = Path(tmp)
        ssml_path = folder / "in.xml"
        audio_path = folder / "out.mp3"
        words_path = folder / "words.json"
        ssml_path.write_text(ssml, "utf-8")
        proc = subprocess.run(
            cmd + [str(Path(__file__).resolve()), "--worker", str(ssml_path), str(audio_path), str(words_path)],
            capture_output=True, text=True, encoding="utf-8",
        )
        if proc.returncode != 0 or not audio_path.exists():
            detail = (proc.stderr or proc.stdout or "azure tts worker failed").strip()
            raise RuntimeError(detail)
        words = json.loads(words_path.read_text("utf-8"))
        return audio_path.read_bytes(), words


def _worker(ssml_path: str, audio_path: str, words_path: str) -> None:
    ssml = Path(ssml_path).read_text("utf-8")
    audio, words = _sdk_synthesize(ssml)
    Path(audio_path).write_bytes(audio)
    Path(words_path).write_text(json.dumps(words, ensure_ascii=False), "utf-8")


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--worker":
        try:
            _worker(sys.argv[2], sys.argv[3], sys.argv[4])
        except Exception as exc:
            print(f"{exc.__class__.__name__}: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        print("usage: python tools/azure_tts.py --worker <ssml> <mp3> <words.json>", file=sys.stderr)
        sys.exit(2)
