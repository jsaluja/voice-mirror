"""TypeSafe (Jev): judges whether escalating to the text-fallback correction is
likely to help, instead of blindly always trying it once a phoneme override fails.

A phoneme override that's already close to correct can regress when swapped
for a cruder plain-text respelling (confirmed empirically for kirkcudbright/
eyjafjallajokull) -- this Choice question replaces that blind escalation with
a real judgment call.
"""
import weave

from src.config import TYPESAFE_API_KEY

try:
    from typesafe_sdk import Choice, TypeSafeClient
except ImportError:  # pragma: no cover
    TypeSafeClient = None


@weave.op()
def should_escalate_to_text_fallback(word: str, phoneme_ipa: str, text_fallback: str, heard_as: str) -> bool:
    """True if the text-fallback respelling is likely to fix the mispronunciation."""
    if not TYPESAFE_API_KEY or TypeSafeClient is None:
        return True  # no judge available -- keep prior fixed-order behavior

    with TypeSafeClient() as client:
        response = client.system_one(
            state={
                "word": word,
                "phoneme_ipa_used": phoneme_ipa,
                "text_fallback_candidate": text_fallback,
                "asr_heard_it_as": heard_as,
            },
            questions={
                "escalate": Choice(
                    instructions=(
                        "A TTS engine already tried `phoneme_ipa_used` for `word` and a "
                        "listener-side ASR still heard it as `asr_heard_it_as`, not `word`. "
                        "Is `text_fallback_candidate` (a plain-text syllable respelling) "
                        "likely to be read more correctly by the TTS engine than the "
                        "phoneme override already tried, or is the phoneme override the "
                        "better bet to keep using?"
                    ),
                    criteria={
                        "switch_to_text_fallback": "The plain-text respelling is likely clearer for the TTS engine.",
                        "keep_phoneme_override": "The phoneme override is at least as good; switching would not help.",
                    },
                ),
            },
        )
    return response.choices["escalate"].choice == "switch_to_text_fallback"
