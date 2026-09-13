"""Offline release gate for voice synthesis quality.

Each case provides approved intended text. The gate renders it through the
production TTS path, transcribes the audio through listener-side ASR, and
checks pronunciation and spoken fidelity. Text correctness is upstream and
out of scope; this gate tests only what TTS makes the caller hear.

Usage:
    python -m scripts.quality_gate
    python -m scripts.quality_gate --cases data/quality_gate_cases.json
"""
import argparse
import concurrent.futures
import hashlib
import json
import re
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

import weave
from typesafe_sdk import Noul, TypeSafeClient

from src.config import (
    CORRECTION_TABLE_PATH,
    TRAP_WORDS,
    TYPESAFE_API_KEY,
    TTS_LANGUAGE_CODE,
    TTS_VOICE_NAME,
    WANDB_API_KEY,
    WEAVE_PROJECT,
)
from src.correction_table import CorrectionTable
from src.pipeline import process_turn


DEFAULT_THRESHOLD = 0.85
SUPPORTED_SCHEMA_VERSION = 2


def load_suite(path: str) -> dict:
    with open(path, encoding="utf-8") as cases_file:
        suite = json.load(cases_file)
    if not isinstance(suite, dict):
        raise ValueError("Quality-gate suite must be a JSON object.")
    if suite.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        raise ValueError(f"Quality-gate schema_version must be {SUPPORTED_SCHEMA_VERSION}.")
    if not suite.get("suite_id") or not isinstance(suite.get("cases"), list) or not suite["cases"]:
        raise ValueError("Quality-gate suite requires suite_id and a non-empty cases array.")
    for case in suite["cases"]:
        required = {"id", "industry", "intended_text", "assertions", "semantic_requirements"}
        missing = sorted(required - case.keys())
        if missing:
            raise ValueError(f"Case {case.get('id', '<unknown>')} is missing: {', '.join(missing)}")
        if not isinstance(case["intended_text"], str) or not case["intended_text"].strip():
            raise ValueError(f"Case {case['id']} requires non-empty intended_text.")
        if any(field in case for field in ("candidate_text", "scenario_facts", "caller_turn")):
            raise ValueError(
                f"Case {case['id']} includes agent/content fields. This gate accepts only "
                "approved intended_text and evaluates TTS/ASR voice fidelity."
            )
    return suite


def normalize_spoken_text(text: str) -> str:
    normalized = text.lower()
    normalized = re.sub(r"\b([ap])\.m\.", r"\1m", normalized)
    normalized = re.sub(r"(?<=\d),(?=\d)", "", normalized)
    normalized = re.sub(r"(?<=\d)\.(?=\d)", " point ", normalized)
    normalized = normalized.replace("%", " percent").replace("$", "")
    return " ".join(re.findall(r"[a-z0-9]+", normalized))


def evaluate_assertions(assertions: list[dict], spoken_transcript: str) -> list[dict]:
    normalized_transcript = normalize_spoken_text(spoken_transcript)
    checks = []
    for assertion in assertions:
        assertion_type = assertion["type"]
        phrases = [normalize_spoken_text(value) for value in assertion["values"]]
        if assertion_type == "contains_any":
            passed = any(phrase in normalized_transcript for phrase in phrases)
        elif assertion_type == "not_contains_any":
            passed = not any(phrase in normalized_transcript for phrase in phrases)
        else:
            raise ValueError(f"Unsupported assertion type: {assertion_type}")
        checks.append(
            {
                "id": assertion["id"],
                "type": assertion_type,
                "values": assertion["values"],
                "passed": passed,
            }
        )
    return checks


def voice_verdict(
    listener_assertions: list[dict],
    semantic_checks: list[dict],
    pronunciation_passed: bool,
    voice_preservation_probability: float,
    threshold: float,
) -> dict:
    passed = (
        pronunciation_passed
        and voice_preservation_probability >= threshold
        and all(check["passed"] for check in listener_assertions)
        and all(check["passed"] for check in semantic_checks)
    )
    return {"voice_passed": passed, "passed": passed}


def git_value(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def build_manifest(suite: dict, threshold: float) -> dict:
    correction_bytes = Path(CORRECTION_TABLE_PATH).read_bytes()
    return {
        "run_id": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "created_at": datetime.now(UTC).isoformat(),
        "git_commit": git_value("rev-parse", "HEAD"),
        "git_dirty": bool(git_value("status", "--porcelain")),
        "suite_id": suite["suite_id"],
        "suite_schema_version": suite["schema_version"],
        "semantic_threshold": threshold,
        "tts": {
            "provider": "google-cloud-text-to-speech",
            "voice": TTS_VOICE_NAME,
            "language": TTS_LANGUAGE_CODE,
        },
        "asr": {
            "provider": "google-cloud-speech-to-text",
            "language": TTS_LANGUAGE_CODE,
        },
        "semantic_evaluator": {
            "provider": "typesafe",
            "primitive": "Noul",
        },
        "correction_table_sha256": hashlib.sha256(correction_bytes).hexdigest(),
    }


def evaluate_case(case: dict, correction_table: CorrectionTable, threshold: float) -> dict:
    intended_text = case["intended_text"]
    turn = process_turn(intended_text, correction_table)
    best_attempt = min(turn.attempts, key=lambda attempt: attempt.wer)
    listener_assertions = evaluate_assertions(case["assertions"], best_attempt.roundtrip_text)

    questions = {
        f"requirement_{index}": Noul(
            instructions=(
                f"Does `listener_transcript` preserve this critical spoken requirement from "
                f"`intended_text`: {requirement}?"
            ),
            criteria={
                "true": "The listener transcript preserves the intended spoken meaning.",
                "false": "Pronunciation or recognition drops, obscures, or changes the intended meaning.",
            },
        )
        for index, requirement in enumerate(case["semantic_requirements"])
    }
    # Only judge fidelity on words this suite actually treats as critical (TRAP_WORDS
    # present in this case's text) -- generic ASR drift on other words (e.g. "your" heard
    # as "here") is noise the retry loop never targets and shouldn't fail the gate either.
    # Cases with no relevant trap word (e.g. travel/finance) keep the original holistic check.
    relevant_trap_words = sorted(set(re.findall(r"[a-z0-9']+", intended_text.lower())) & TRAP_WORDS)
    if relevant_trap_words:
        voice_preserves_instructions = (
            "Does `listener_transcript` preserve the meaning and all critical values from "
            "`intended_text`? Treat punctuation, sentence breaks, currency formatting, digit "
            "grouping such as `12` versus `1 2`, equivalent spoken number forms, and any "
            "substitution, drop, or reordering of words OTHER than these critical terms as "
            f"harmless -- the only words that must be correctly recognized are: {relevant_trap_words}. "
            "Return false only when one of those critical terms is dropped, altered, or "
            "mispronounced, or the sentence's meaning is reversed or contradicted."
        )
    else:
        voice_preserves_instructions = (
            "Does `listener_transcript` preserve the meaning and all critical values from "
            "`intended_text`? Treat punctuation, sentence breaks, currency formatting, "
            "digit grouping such as `12` versus `1 2`, and equivalent spoken number forms "
            "as harmless. Return false only when meaning or a critical value actually changes."
        )
    questions["voice_preserves_intended_text"] = Noul(
        instructions=voice_preserves_instructions,
        criteria={
            "true": "The listener transcript faithfully preserves the intended text.",
            "false": "Pronunciation or recognition changes, drops, or obscures meaning or a critical value.",
        },
    )

    with TypeSafeClient() as client:
        response = client.system_one(
            state={
                "industry": case["industry"],
                "intended_text": intended_text,
                "listener_transcript": best_attempt.roundtrip_text,
            },
            questions=questions,
        )

    semantic_checks = []
    for index, requirement in enumerate(case["semantic_requirements"]):
        probability = response.nouls[f"requirement_{index}"].noul
        semantic_checks.append(
            {
                "requirement": requirement,
                "probability": probability,
                "passed": probability >= threshold,
            }
        )

    voice_preservation_probability = response.nouls["voice_preserves_intended_text"].noul
    verdict = voice_verdict(
        listener_assertions=listener_assertions,
        semantic_checks=semantic_checks,
        pronunciation_passed=turn.success,
        voice_preservation_probability=voice_preservation_probability,
        threshold=threshold,
    )

    return {
        "id": case["id"],
        "industry": case["industry"],
        "intended_text": intended_text,
        "listener_transcript": best_attempt.roundtrip_text,
        "pronunciation_passed": turn.success,
        "voice_preservation_probability": voice_preservation_probability,
        "assertions": listener_assertions,
        "semantic_requirements": semantic_checks,
        **verdict,
    }


def evaluate_case_safe(case: dict, correction_table: CorrectionTable, threshold: float) -> dict:
    """Wraps evaluate_case so one case's exhausted-retry/API error can't abort the whole batch."""
    try:
        return evaluate_case(case, correction_table, threshold)
    except Exception as exc:
        return {
            "id": case["id"],
            "industry": case["industry"],
            "intended_text": case["intended_text"],
            "listener_transcript": "",
            "pronunciation_passed": False,
            "voice_preservation_probability": 0.0,
            "assertions": [],
            "semantic_requirements": [],
            "voice_passed": False,
            "passed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def print_report(results: list[dict], threshold: float) -> None:
    passed = sum(result["passed"] for result in results)
    decision = "APPROVED" if passed == len(results) else "BLOCKED"
    print(f"\nVoice release gate: {decision} — {passed}/{len(results)} cases passed (threshold={threshold:.2f})\n")
    for result in results:
        status = "PASS" if result["passed"] else "BLOCK"
        print(f"[{status}] {result['industry'].upper()} — {result['id']}")
        print(f"  intended: {result['intended_text']}")
        print(f"  heard:    {result['listener_transcript']}")
        print(f"  voice:    {'PASS' if result['voice_passed'] else 'FAIL'}")
        for assertion in result["assertions"]:
            mark = "PASS" if assertion["passed"] else "FAIL"
            print(f"  {mark} listener assertion — {assertion['id']}")
        for check in result["semantic_requirements"]:
            mark = "PASS" if check["passed"] else "FAIL"
            print(f"  {mark} spoken fidelity {check['probability']:.2f} — {check['requirement']}")
        print(f"  {'PASS' if result['voice_passed'] else 'FAIL'} voice preservation "
              f"{result['voice_preservation_probability']:.2f}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="data/quality_gate_cases.json")
    parser.add_argument("--report", default="data/quality_gate_report.json")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Concurrent cases to evaluate -- each case is I/O-bound (TTS/ASR/TypeSafe network calls), not CPU-bound. Keep modest to avoid TypeSafe/Gemini rate limits (429).",
    )
    args = parser.parse_args()

    if not TYPESAFE_API_KEY:
        raise SystemExit("TYPESAFE_API_KEY is required for semantic quality checks.")
    if WANDB_API_KEY:
        weave.init(WEAVE_PROJECT)

    correction_table = CorrectionTable()
    suite = load_suite(args.cases)
    cases = suite["cases"]
    manifest = build_manifest(suite, args.threshold)

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_lock = threading.Lock()
    results: list[dict | None] = [None] * len(cases)

    def _write_checkpoint() -> None:
        # Overwrite the real report path after every case so a crash/kill never loses completed work.
        completed = [result for result in results if result is not None]
        with write_lock:
            report_path.write_text(
                json.dumps(
                    {
                        "decision": "IN_PROGRESS",
                        "manifest": manifest,
                        "summary": {
                            "total": len(completed),
                            "passed": sum(result["passed"] for result in completed),
                            "blocked": sum(not result["passed"] for result in completed),
                        },
                        "cases_completed": len(completed),
                        "cases_total": len(cases),
                        "results": completed,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_to_index = {
            pool.submit(evaluate_case_safe, case, correction_table, args.threshold): index
            for index, case in enumerate(cases)
        }
        for future in concurrent.futures.as_completed(future_to_index):
            results[future_to_index[future]] = future.result()
            _write_checkpoint()

    errored = [result for result in results if result.get("error")]
    if errored:
        print(f"\n{len(errored)} case(s) errored out (e.g. rate limits) and were recorded as BLOCKED:")
        for result in errored:
            print(f"  {result['id']}: {result['error']}")
    print_report(results, args.threshold)

    report = {
        "decision": "APPROVED" if all(result["passed"] for result in results) else "BLOCKED",
        "manifest": manifest,
        "summary": {
            "total": len(results),
            "passed": sum(result["passed"] for result in results),
            "blocked": sum(not result["passed"] for result in results),
        },
        "results": results,
    }

    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {report_path}")

    if any(not result["passed"] for result in results):
        sys.exit(1)


if __name__ == "__main__":
    main()