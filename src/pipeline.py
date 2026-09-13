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
from src.critic import should_escalate_to_text_fallback
from src.diff import DivergenceResult, compute_divergence
from src.intake import extract_patient_query, find_candidate_records, load_patients, verify_patient_match
from src.llm import generate_correction, generate_pharmacy_response, generate_response
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
    critic_already_asked: set[str] = set()
    attempt_records: list[AttemptRecord] = []
    attempt = 0
    audio = b""
    divergence: DivergenceResult | None = None
    best_audio = b""
    best_divergence: DivergenceResult | None = None

    while True:
        rendered, used_ssml = build_ssml(intended_text, correction_table, use_text_fallback_for=fallback_words)
        audio = synthesize_speech(rendered, is_ssml=used_ssml)
        transcript = transcribe_audio(audio)
        divergence = compute_divergence(intended_text, transcript.text)

        if best_divergence is None or divergence.wer < best_divergence.wer:
            best_audio = audio
            best_divergence = divergence

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
                audio=best_audio,
                success=False,
                attempts=attempt_records,
                final_divergence=best_divergence,
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
            elif existing.get("text_fallback") and word.lower() not in fallback_words:
                if word.lower() in critic_already_asked:
                    # Already asked once and the phoneme override still diverged on
                    # retry -- don't ask the identical question again and get stuck
                    # repeating a losing strategy, just escalate.
                    fallback_words.add(word.lower())
                    continue
                critic_already_asked.add(word.lower())
                # Don't blindly escalate to the text fallback -- ask whether it's
                # actually likely to beat the phoneme override already tried.
                if should_escalate_to_text_fallback(
                    word,
                    phoneme_ipa=existing.get("phoneme_ipa", ""),
                    text_fallback=existing["text_fallback"],
                    heard_as=mismatch.hypothesis_word,
                ):
                    fallback_words.add(word.lower())


@weave.op()
def full_turn(user_text: str, correction_table: CorrectionTable) -> TurnResult:
    """ASR1 (assumed already text here) -> LLM -> process_turn."""
    response_text = generate_response(user_text)
    return process_turn(response_text, correction_table)


@weave.op()
def speak(text: str, correction_table: CorrectionTable) -> bytes:
    """Zero-latency runtime path: apply the already-validated correction table
    and synthesize once. No self-listen ASR call, no retry loop -- this is
    what a live voice agent actually calls after `process_turn` has vetted
    the script at lint time. Latency here is identical to a plain TTS call.
    """
    rendered, used_ssml = build_ssml(text, correction_table)
    return synthesize_speech(rendered, is_ssml=used_ssml)


@dataclass
class PharmacyTurnResult:
    needs_clarification: bool
    matched_record: dict | None
    turn: TurnResult


@weave.op()
def handle_pharmacy_call(utterance: str, correction_table: CorrectionTable) -> PharmacyTurnResult:
    """Live pharmacy IVR turn: identify the caller (extract -> match -> TypeSafe
    confidence gate), then either ask for clarification or read back their
    prescription status through the self-correcting TTS loop.
    """
    query = extract_patient_query(utterance)
    candidates = find_candidate_records(query, load_patients())
    record = verify_patient_match(query, candidates)

    if record is None:
        clarification = (
            "I couldn't confirm your identity from that -- "
            "can you give me your full name, date of birth, and zip code?"
        )
        return PharmacyTurnResult(
            needs_clarification=True,
            matched_record=None,
            turn=process_turn(clarification, correction_table),
        )

    response_text = generate_pharmacy_response(record)
    return PharmacyTurnResult(
        needs_clarification=False,
        matched_record=record,
        turn=process_turn(response_text, correction_table),
    )
