"""TTS via Google Cloud (full SSML incl. <phoneme> support)."""
import re
from xml.sax.saxutils import escape

import weave
from google.cloud import texttospeech

from src.config import GOOGLE_CLOUD_PROJECT, TTS_LANGUAGE_CODE, TTS_VOICE_NAME
from src.correction_table import CorrectionTable
from src.tts_cache import get_cached_audio, store_audio

_client = texttospeech.TextToSpeechClient()


def build_ssml(text: str, correction_table: CorrectionTable, use_text_fallback_for: set[str] | None = None) -> tuple[str, bool]:
    """Apply correction-table overrides to `text`.

    For each word with a table entry: prefer the plain-text fallback
    respelling (e.g. "VRAY-lar") over the <phoneme> IPA override by default.

    This is deliberately IPA-averse: our own quality-gate data showed the
    <phoneme alphabet="ipa"> tag is unreliable on the configured voice and
    can regress a merely-mispronounced word into one spelled out letter by
    letter (worse than the original mispronunciation) -- see the Vraylar/
    Esketamine/Clozapine cases in AGENTS.md, all triggered on the retry
    attempt right after a phoneme override kicked in. `use_text_fallback_for`
    is now a no-op safety net for words with no text_fallback saved yet; the
    phoneme override is only used as a last resort when no plain-text
    fallback exists at all.

    Returns (ssml_or_text, used_ssml). When no phoneme override is used, the
    returned string is plain (unescaped) text -- XML escaping only applies
    when actually building a <speak> SSML document.
    """
    use_text_fallback_for = use_text_fallback_for or set()
    tokens = re.findall(r"\w+|\W+", text)
    # Each part is ("plain", raw_text) or ("phoneme", raw_text, ipa)
    parts: list[tuple] = []
    used_ssml = False
    for token in tokens:
        bare = token.strip()
        entry = correction_table.get(bare) if bare else None
        if not entry:
            parts.append(("plain", token))
            continue
        if entry.get("text_fallback"):
            parts.append(("plain", entry["text_fallback"]))
        elif entry.get("phoneme_ipa") and bare.lower() not in use_text_fallback_for:
            parts.append(("phoneme", token, entry["phoneme_ipa"]))
            used_ssml = True
        else:
            parts.append(("plain", token))

    if used_ssml:
        body = "".join(
            f'<phoneme alphabet="ipa" ph="{escape(p[2])}">{escape(p[1])}</phoneme>'
            if p[0] == "phoneme"
            else escape(p[1])
            for p in parts
        )
        return f"<speak>{body}</speak>", True

    return "".join(p[1] for p in parts), False


def _trace_audio_as_content(output: bytes, **_):
    """Store the full MP3 as a Weave media blob instead of a truncated string preview."""
    return weave.Content.from_bytes(output, extension="mp3", mimetype="audio/mpeg")


@weave.op(postprocess_output=_trace_audio_as_content)
def synthesize_speech(text: str, is_ssml: bool = False) -> bytes:
    """Synthesize speech, returning MP3 audio bytes.

    Replays cached audio for an exact-text repeat instead of calling Google
    TTS again -- see src/tts_cache.py for why this is exact-match only.
    """
    cached = get_cached_audio(TTS_VOICE_NAME, TTS_LANGUAGE_CODE, "MP3", None, is_ssml, text)
    if cached is not None:
        return cached

    synth_input = (
        texttospeech.SynthesisInput(ssml=text)
        if is_ssml
        else texttospeech.SynthesisInput(text=text)
    )
    voice = texttospeech.VoiceSelectionParams(
        language_code=TTS_LANGUAGE_CODE, name=TTS_VOICE_NAME
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3
    )
    response = _client.synthesize_speech(
        input=synth_input,
        voice=voice,
        audio_config=audio_config,
        timeout=30,
    )
    store_audio(TTS_VOICE_NAME, TTS_LANGUAGE_CODE, "MP3", None, is_ssml, text, response.audio_content)
    return response.audio_content


@weave.op(postprocess_output=_trace_audio_as_content)
def synthesize_speech_pcm(text: str, is_ssml: bool, sample_rate_hertz: int) -> bytes:
    """Synthesize speech as raw LINEAR16 PCM at an exact sample rate -- the
    format Vapi's custom-voice webhook requires (no container/headers).

    Replays cached audio for an exact-text repeat instead of calling Google
    TTS again -- this is the live-call cost-saving path (Vapi cannot tell
    the difference; it just gets PCM bytes back either way).
    """
    cached = get_cached_audio(TTS_VOICE_NAME, TTS_LANGUAGE_CODE, "LINEAR16", sample_rate_hertz, is_ssml, text)
    if cached is not None:
        return cached

    synth_input = (
        texttospeech.SynthesisInput(ssml=text)
        if is_ssml
        else texttospeech.SynthesisInput(text=text)
    )
    voice = texttospeech.VoiceSelectionParams(
        language_code=TTS_LANGUAGE_CODE, name=TTS_VOICE_NAME
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
        sample_rate_hertz=sample_rate_hertz,
    )
    response = _client.synthesize_speech(
        input=synth_input,
        voice=voice,
        audio_config=audio_config,
        timeout=30,
    )
    store_audio(TTS_VOICE_NAME, TTS_LANGUAGE_CODE, "LINEAR16", sample_rate_hertz, is_ssml, text, response.audio_content)
    return response.audio_content
