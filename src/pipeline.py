"""Pipeline core: LLM -> correction lookup -> TTS -> self-listen ASR -> diff -> gate.

Turn-by-turn, synchronous, bounded retries -- the "simplest thing that can
work" per the plan's MVP scope. Every stage is a weave.op() so a full turn
(including retries) shows up as one trace tree.
"""
from dataclasses import dataclass, field

import weave

from src.asr import transcribe_audio
from src.config import MAX_RETRIES
from src.correction_table import CorrectionTable
from src.diff import DivergenceResult, compute_divergence
from src.llm import generate_correction, generate_response
from src.tts import build_ssml, synthesize_speech


@dataclass
class AttemptRecord:
    attempt: int
    rendered_text: str
    used_ssml: bool
    roundtrip_text: str
    wer: float
    diverged: bool


@dataclass
class TurnResult:
    intended_text: str
    audio: bytes
    success: bool
    attempts: list[AttemptRecord] = field(default_factory=list)
    final_divergence: DivergenceResult | None = None


@weave.op()
def process_turn(intended_text: str, correction_table: CorrectionTable, max_retries: int = MAX_RETRIES) -> TurnResult:
    """Self-listen loop: synthesize, check, learn, retry (bounded)."""
    fallback_words: set[str] = set()
    attempt_records: list[AttemptRecord] = []
    attempt = 0
    audio = b""
    divergence: DivergenceResult | None = None

    while True:
        rendered, used_ssml = build_ssml(intended_text, correction_table, use_text_fallback_for=fallback_words)
        audio = synthesize_speech(rendered, is_ssml=used_ssml)
        transcript = transcribe_audio(audio)
        divergence = compute_divergence(intended_text, transcript.text)

        attempt_records.append(
            AttemptRecord(
                attempt=attempt,
                rendered_text=rendered,
                used_ssml=used_ssml,
                roundtrip_text=transcript.text,
                wer=divergence.wer,
                diverged=divergence.diverged,
            )
        )

        if not divergence.diverged:
            return TurnResult(
                intended_text=intended_text,
                audio=audio,
                success=True,
                attempts=attempt_records,
                final_divergence=divergence,
            )

        attempt += 1
        if attempt > max_retries:
            return TurnResult(
                intended_text=intended_text,
                audio=audio,
                success=False,
                attempts=attempt_records,
                final_divergence=divergence,
            )

        for mismatch in divergence.mismatches:
            word = mismatch.reference_word
            if not word:
                continue
            existing = correction_table.get(word)
            if existing is None:
                new_entry = generate_correction(word)
                correction_table.set(
                    word,
                    phoneme_ipa=new_entry.get("phoneme_ipa"),
                    text_fallback=new_entry.get("text_fallback"),
                )
            else:
                fallback_words.add(word.lower())


@weave.op()
def full_turn(user_text: str, correction_table: CorrectionTable) -> TurnResult:
    """ASR1 (assumed already text here) -> LLM -> process_turn."""
    response_text = generate_response(user_text)
    return process_turn(response_text, correction_table)
