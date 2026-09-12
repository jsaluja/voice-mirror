"""Word-level diff between intended text and self-listen round-trip transcript."""
import re
from dataclasses import dataclass

import jiwer
import weave

from src.config import TRAP_WORDS


@dataclass
class Mismatch:
    reference_word: str
    hypothesis_word: str


@dataclass
class DivergenceResult:
    wer: float
    mismatches: list[Mismatch]  # already filtered to trap words only

    @property
    def diverged(self) -> bool:
        return bool(self.mismatches)


def _normalize(text: str) -> str:
    # Hyphens become spaces (not dropped) so "GPU-accelerated" tokenizes the
    # same way ASR naturally renders it ("GPU accelerated") -- avoids
    # flagging tokenization artifacts as false-positive mispronunciations.
    text = text.replace("-", " ")
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


@weave.op()
def compute_divergence(intended_text: str, roundtrip_text: str) -> DivergenceResult:
    ref = _normalize(intended_text)
    hyp = _normalize(roundtrip_text)
    if not ref:
        return DivergenceResult(wer=0.0, mismatches=[])

    wer = jiwer.wer(ref, hyp)
    output = jiwer.process_words(ref, hyp)

    mismatches: list[Mismatch] = []
    for chunk in output.alignments[0]:
        if chunk.type == "substitute":
            ref_words = output.references[0][chunk.ref_start_idx : chunk.ref_end_idx]
            hyp_words = output.hypotheses[0][chunk.hyp_start_idx : chunk.hyp_end_idx]
            for r, h in zip(ref_words, hyp_words):
                mismatches.append(Mismatch(reference_word=r, hypothesis_word=h))
        elif chunk.type == "delete":
            ref_words = output.references[0][chunk.ref_start_idx : chunk.ref_end_idx]
            for r in ref_words:
                mismatches.append(Mismatch(reference_word=r, hypothesis_word=""))

    # Only trap words gate the retry loop -- natural ASR reading variants
    # (date formats, abbreviation expansion, compound-word splits) are not
    # real mispronunciations and shouldn't trigger corrections.
    trap_mismatches = [m for m in mismatches if m.reference_word.lower() in TRAP_WORDS]

    return DivergenceResult(wer=wer, mismatches=trap_mismatches)
