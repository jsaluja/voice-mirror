"""End-to-end CLI demo: type a message -> LLM response -> self-correcting TTS.

Usage:
    python -m scripts.demo
    python -m scripts.demo --text "Tell me about CoreWeave"
"""
import argparse
import subprocess
import sys
import tempfile

import weave

from src.config import WANDB_API_KEY, WEAVE_PROJECT
from src.correction_table import CorrectionTable
from src.pipeline import full_turn


def play_audio(audio_bytes: bytes) -> None:
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio_bytes)
        path = f.name
    subprocess.run(["afplay", path], check=False)


def run_turn(user_text: str, correction_table: CorrectionTable) -> None:
    result = full_turn(user_text, correction_table)

    print(f"\nIntended: {result.intended_text}")
    for rec in result.attempts:
        tag = "OK" if not rec.diverged else "MISMATCH"
        print(f"  attempt {rec.attempt} [{tag}] wer={rec.wer:.2f} heard=\"{rec.roundtrip_text}\"")
    print(f"Result: {'SUCCESS' if result.success else 'GAVE UP (best-effort audio played)'}")

    play_audio(result.audio)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", help="Single message to run, then exit.")
    args = parser.parse_args()

    if WANDB_API_KEY:
        weave.init(WEAVE_PROJECT)

    correction_table = CorrectionTable()

    if args.text:
        run_turn(args.text, correction_table)
        return

    print("Self-correcting voice agent demo. Type a message (Ctrl+D to quit).")
    while True:
        try:
            user_text = input("\n> ")
        except EOFError:
            print()
            sys.exit(0)
        if not user_text.strip():
            continue
        run_turn(user_text, correction_table)


if __name__ == "__main__":
    main()
