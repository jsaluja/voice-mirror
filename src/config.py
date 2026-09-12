"""Central config: env vars + tunables for the self-correct loop."""
import os

from dotenv import load_dotenv

load_dotenv()

DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
WANDB_API_KEY = os.environ.get("WANDB_API_KEY", "")
WEAVE_PROJECT = os.environ.get("WEAVE_PROJECT", "voice-self-correct-loop")

# TypeSafe (Jev): judges whether escalating to the text-fallback correction is
# likely to help, instead of blindly always trying it once a phoneme override fails.
TYPESAFE_API_KEY = os.environ.get("TYPESAFE_API_KEY", "")

GOOGLE_CLOUD_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
TTS_VOICE_NAME = os.environ.get("TTS_VOICE_NAME", "en-US-Neural2-C")
TTS_LANGUAGE_CODE = os.environ.get("TTS_LANGUAGE_CODE", "en-US")

# LLM: Azure OpenAI (plain OPENAI_API_KEY was invalid; Azure creds were ready in shell)
AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("AZURE_API_KEY", "")
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_ENDPOINT", "")
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_API_VERSION", "2024-04-01-preview")
LLM_MODEL = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME") or os.environ.get("AZURE_DEPLOYMENT", "gpt-4.1-mini")
DEEPGRAM_MODEL = os.environ.get("DEEPGRAM_MODEL", "nova-2")

MAX_RETRIES = 2  # bounded retry per plan MVP scope

# Hardcoded trap words (per plan MVP scope) -- only these gate the retry
# loop. Natural ASR reading variants (dates, abbreviations, compound-word
# splits) are excluded to avoid false-positive "corrections".
TRAP_WORDS = {
    w.strip().lower()
    for w in os.environ.get(
        "TRAP_WORDS", "coreweave,typesafe,marimo,aria,kirkcudbright,eyjafjallajokull"
    ).split(",")
    if w.strip()
}

CORRECTION_TABLE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "correction_table.json",
)
