"""Central config: env vars + tunables for the self-correct loop."""
import json
import os

from dotenv import load_dotenv

load_dotenv()

WANDB_API_KEY = os.environ.get("WANDB_API_KEY", "")
WEAVE_PROJECT = os.environ.get("WEAVE_PROJECT", "voice-mirror")

# TypeSafe (Jev): judges whether escalating to the text-fallback correction is
# likely to help, instead of blindly always trying it once a phoneme override fails.
TYPESAFE_API_KEY = os.environ.get("TYPESAFE_API_KEY", "")

# Vapi: telephony/voice-agent layer. Our server plugs in as a custom-llm +
# custom-voice provider (see src/vapi_server.py) -- Vapi never talks to
# Google Cloud directly.
VAPI_API_KEY = os.environ.get("VAPI_API_KEY", "")
VAPI_PUBLIC_KEY = os.environ.get("VAPI_PUBLIC_KEY", "")

GOOGLE_CLOUD_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
GOOGLE_CLOUD_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
TTS_VOICE_NAME = os.environ.get("TTS_VOICE_NAME", "en-US-Neural2-C")
TTS_LANGUAGE_CODE = os.environ.get("TTS_LANGUAGE_CODE", "en-US")

# LLM: Gemini via Vertex AI (uses the same ADC credentials as TTS/ASR, no API key needed)
LLM_MODEL = os.environ.get("LLM_MODEL", "gemini-2.5-flash")

MAX_RETRIES = 2  # bounded retry per plan MVP scope

# Hardcoded trap words (per plan MVP scope) -- only these gate the retry
# loop. Natural ASR reading variants (dates, abbreviations, compound-word
# splits) are excluded to avoid false-positive "corrections". Pharmacy drug
# names are trap words so the self-correct loop actually attempts a phoneme
# fix before the quality gate blocks a release on them -- derived from the
# suite file below instead of hand-copied, since the drug list is too large
# (250+) to keep in sync manually without it silently going stale.
_CASES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "quality_gate_cases.json"
)


def _pharmacy_trap_words() -> set[str]:
    try:
        with open(_CASES_PATH) as f:
            suite = json.load(f)
    except (OSError, json.JSONDecodeError):
        return set()
    words: set[str] = set()
    for case in suite.get("cases", []):
        if case.get("industry") != "pharmacy":
            continue
        for assertion in case.get("assertions", []):
            if assertion.get("type") != "contains_any":
                continue
            values = [v.strip().lower() for v in assertion.get("values", []) if v.strip()]
            # A multi-word value is a drug-name variant (e.g. "metoprolol succinate")
            # only when the same assertion also lists a single-word alternative --
            # dosage-amount phrasings ("twenty milligrams to forty milligrams") never
            # do, so those are left whole and not split into generic number/unit words.
            is_drug_name_assertion = any(" " not in v for v in values)
            for value in values:
                if " " not in value:
                    words.add(value)
                elif is_drug_name_assertion:
                    words.update(value.split())
    return words


TRAP_WORDS = {
    w.strip().lower()
    for w in os.environ.get(
        "TRAP_WORDS", "coreweave,typesafe,marimo,aria,kirkcudbright,eyjafjallajokull"
    ).split(",")
    if w.strip()
} | _pharmacy_trap_words()

CORRECTION_TABLE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "correction_table.json",
)

PATIENTS_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "patients.json",
)
