"""TTS via Google Cloud (full SSML incl. <phoneme> support)."""
import re
from xml.sax.saxutils import escape

import weave
from google.cloud import texttospeech

from src.config import GOOGLE_CLOUD_PROJECT, TTS_LANGUAGE_CODE, TTS_VOICE_NAME
from src.correction_table import CorrectionTable

_client = texttospeech.TextToSpeechClient()


def build_ssml(text: str, correction_table: CorrectionTable, use_text_fallback_for: set[str] | None = None) -> tuple[str, bool]:
    """Apply correction-table overrides to `text`.

    For each word with a table entry: use <phoneme> unless that word is in
    `use_text_fallback_for` (i.e. phoneme already failed this turn), in which
    case substitute the plain-text fallback spelling instead.

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
        if bare.lower() in use_text_fallback_for and entry.get("text_fallback"):
            parts.append(("plain", entry["text_fallback"]))
        elif entry.get("phoneme_ipa"):
            parts.append(("phoneme", token, entry["phoneme_ipa"]))
            used_ssml = True
        elif entry.get("text_fallback"):
            parts.append(("plain", entry["text_fallback"]))
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
    """Synthesize speech, returning MP3 audio bytes."""
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
    return response.audio_content
