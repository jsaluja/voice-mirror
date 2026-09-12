"""Pronunciation CI linter: scan a voice-bot script library offline.

Runs every line through the existing self-listen loop (`process_turn`) --
TTS -> self-listen ASR -> diff -> bounded retry -- exactly like the old
live/runtime version did, except now it happens at build time against a
batch of canned prompts, not blocking a live user turn. Validated fixes are
written to the persistent correction table, which the live runtime path
(`src.pipeline.speak`) then reads with zero added latency.

Usage:
    python -m scripts.lint_scripts
    python -m scripts.lint_scripts --input scripts/sample_prompts.txt
    python -m scripts.lint_scripts --input scripts/sample_prompts.txt --report data/lint_report.json

Exits with code 1 if any line fails to pass within the retry budget, the same
way a linter/CI check would fail a build.
"""
import argparse
import json
import sys
import time

import weave

from src.config import WANDB_API_KEY, WEAVE_PROJECT
from src.correction_table import CorrectionTable
from src.pipeline import process_turn, speak


def load_script_lines(path: str) -> list[str]:
    lines = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            lines.append(line)
    return lines


def lint(lines: list[str], correction_table: CorrectionTable) -> list[dict]:
    report = []
    for line in lines:
        result = process_turn(line, correction_table)
        report.append(
            {
                "text": result.intended_text,
                "success": result.success,
                "attempts": len(result.attempts),
                "final_wer": result.attempts[-1].wer if result.attempts else None,
                "attempt_log": [
                    {"attempt": a.attempt, "wer": a.wer, "diverged": a.diverged, "heard": a.roundtrip_text}
                    for a in result.attempts
                ],
            }
        )
    return report


def print_report(report: list[dict]) -> None:
    n_pass = sum(1 for r in report if r["success"])
    print(f"\nPronunciation lint report -- {n_pass}/{len(report)} lines passed\n")
    for r in report:
        tag = "PASS" if r["success"] else "FAIL"
        wer = f"{r['final_wer']:.2f}" if r["final_wer"] is not None else "n/a"
        print(f"  [{tag}] ({r['attempts']} attempt(s), wer={wer}) {r['text']}")
    failing = [r for r in report if not r["success"]]
    if failing:
        print(f"\n{len(failing)} line(s) still diverging after retries -- needs a human-reviewed correction:")
        for r in failing:
            print(f"  - {r['text']}")


def compare_latency(sample_line: str, correction_table: CorrectionTable) -> None:
    """Demonstrate that the runtime path pays none of the lint-time cost."""
    lint_start = time.monotonic()
    process_turn(sample_line, correction_table)
    lint_elapsed = time.monotonic() - lint_start

    runtime_start = time.monotonic()
    speak(sample_line, correction_table)
    runtime_elapsed = time.monotonic() - runtime_start

    print("\nLatency comparison (same line, already-validated correction table):")
    print(f"  lint-time self-listen loop : {lint_elapsed * 1000:.0f} ms")
    print(f"  live runtime path (speak)  : {runtime_elapsed * 1000:.0f} ms")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="scripts/sample_prompts.txt", help="Script library, one line per prompt.")
    parser.add_argument("--report", default="data/lint_report.json", help="Where to write the JSON lint report.")
    parser.add_argument("--skip-latency-demo", action="store_true", help="Skip the runtime-vs-lint-time latency comparison.")
    args = parser.parse_args()

    if WANDB_API_KEY:
        weave.init(WEAVE_PROJECT)

    correction_table = CorrectionTable()
    lines = load_script_lines(args.input)
    if not lines:
        print(f"No script lines found in {args.input}")
        sys.exit(1)

    report = lint(lines, correction_table)
    print_report(report)

    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {args.report}")

    if not args.skip_latency_demo:
        compare_latency(lines[0], correction_table)

    if any(not r["success"] for r in report):
        sys.exit(1)


if __name__ == "__main__":
    main()
