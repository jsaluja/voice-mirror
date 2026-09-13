"""LLM: response generation + IPA/text-fallback generation for new corrections."""
import weave
from google import genai

from src.config import GOOGLE_CLOUD_LOCATION, GOOGLE_CLOUD_PROJECT, LLM_MODEL

_client = genai.Client(vertexai=True, project=GOOGLE_CLOUD_PROJECT, location=GOOGLE_CLOUD_LOCATION)


@weave.op()
def generate_response(user_text: str) -> str:
    response = _client.models.generate_content(
        model=LLM_MODEL,
        contents=user_text,
        config={
            "system_instruction": "You are a helpful voice assistant. Keep replies to 1-2 short spoken sentences.",
        },
    )
    return response.text.strip()


@weave.op()
def generate_pharmacy_response(record: dict) -> str:
    """Phrase the prescription status naturally for a pharmacy IVR reply."""
    response = _client.models.generate_content(
        model=LLM_MODEL,
        contents=(
            f"Patient prescription record: drug={record['drug']!r}, "
            f"status={record['refill_status']!r}. Tell the patient this status "
            f"in one short spoken sentence, naturally mentioning the drug name."
        ),
        config={
            "system_instruction": (
                "You are a pharmacy IVR voice assistant reading back a patient's "
                "prescription status over the phone. Keep it to 1 short spoken sentence."
            ),
        },
    )
    return response.text.strip()


@weave.op()
def generate_correction(word: str) -> dict[str, str]:
    """Ask the LLM for an IPA transcription + plain-text respelling of `word`."""
    prompt = (
        f'For the word "{word}" as spoken in American English, respond with exactly two lines:\n'
        f"IPA: <ipa transcription, no slashes>\n"
        f"RESPELL: <hyphenated syllable respelling a TTS engine would read correctly, "
        f'stressed syllable in CAPS, e.g. "Worcestershire" -> "WUUS-ter-sher", '
        f'"CoreWeave" -> "Core-Weave". Must differ from the original spelling.>'
    )
    response = _client.models.generate_content(model=LLM_MODEL, contents=prompt)
    content = response.text.strip()
    ipa, respell = "", ""
    for line in content.splitlines():
        if line.strip().lower().startswith("ipa:"):
            ipa = line.split(":", 1)[1].strip()
        elif line.strip().lower().startswith("respell:"):
            respell = line.split(":", 1)[1].strip()
    return {"phoneme_ipa": ipa, "text_fallback": respell or word}
