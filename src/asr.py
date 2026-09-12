"""ASR via Deepgram prerecorded REST API. Used both for user speech input and
for the self-listen round-trip check on synthesized TTS audio.

Calls the REST endpoint directly (rather than the deepgram-sdk package) since
the installed SDK major version's API surface changes frequently -- a plain
HTTP call is stable and dependency-free beyond `requests`.
"""
from dataclasses import dataclass

import requests
import weave

from src.config import DEEPGRAM_API_KEY, DEEPGRAM_MODEL

_LISTEN_URL = "https://api.deepgram.com/v1/listen"


@dataclass
class WordConfidence:
    word: str
    confidence: float


@dataclass
class TranscriptResult:
    text: str
    words: list[WordConfidence]


@weave.op()
def transcribe_audio(audio_bytes: bytes, mimetype: str = "audio/mp3") -> TranscriptResult:
    """Transcribe audio bytes, returning text + per-word confidence."""
    response = requests.post(
        _LISTEN_URL,
        params={"model": DEEPGRAM_MODEL, "smart_format": "true", "punctuate": "true"},
        headers={
            "Authorization": f"Token {DEEPGRAM_API_KEY}",
            "Content-Type": mimetype,
        },
        data=audio_bytes,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    alternative = payload["results"]["channels"][0]["alternatives"][0]
    text = alternative.get("transcript", "")
    words = [
        WordConfidence(word=w["word"], confidence=w.get("confidence", 0.0))
        for w in alternative.get("words", [])
    ]
    return TranscriptResult(text=text, words=words)
