"""LLM: response generation + IPA/text-fallback generation for new corrections."""
import weave
from openai import AzureOpenAI

from src.config import (
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_ENDPOINT,
    LLM_MODEL,
)

_client = AzureOpenAI(
    api_key=AZURE_OPENAI_API_KEY,
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_version=AZURE_OPENAI_API_VERSION,
)


@weave.op()
def generate_response(user_text: str) -> str:
    completion = _client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": "You are a helpful voice assistant. Keep replies to 1-2 short spoken sentences.",
            },
            {"role": "user", "content": user_text},
        ],
    )
    return completion.choices[0].message.content.strip()


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
    completion = _client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    content = completion.choices[0].message.content.strip()
    ipa, respell = "", ""
    for line in content.splitlines():
        if line.strip().lower().startswith("ipa:"):
            ipa = line.split(":", 1)[1].strip()
        elif line.strip().lower().startswith("respell:"):
            respell = line.split(":", 1)[1].strip()
    return {"phoneme_ipa": ipa, "text_fallback": respell or word}
