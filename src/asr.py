"""ASR via Google Cloud Speech-to-Text. Used both for user speech input and
for the self-listen round-trip check on synthesized TTS audio.
"""
from dataclasses import dataclass

import weave
from google.cloud import speech

from src.config import TTS_LANGUAGE_CODE

_client = speech.SpeechClient()


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
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.MP3,
        language_code=TTS_LANGUAGE_CODE,
        enable_word_confidence=True,
        enable_automatic_punctuation=True,
    )
    audio = speech.RecognitionAudio(content=audio_bytes)
    response = _client.recognize(config=config, audio=audio, timeout=30)

    text_parts = []
    words: list[WordConfidence] = []
    for result in response.results:
        alternative = result.alternatives[0]
        text_parts.append(alternative.transcript)
        words.extend(
            WordConfidence(word=w.word, confidence=w.confidence)
            for w in alternative.words
        )
    return TranscriptResult(text=" ".join(text_parts).strip(), words=words)
