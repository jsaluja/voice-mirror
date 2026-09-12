"""Persistent correction table: word -> {phoneme_ipa, text_fallback}.

This is the loop's cumulative memory -- once a word fails the self-listen
check, its fix is stored here permanently so future turns skip the retry.
"""
import json
import os
import threading

from src.config import CORRECTION_TABLE_PATH

_lock = threading.Lock()


class CorrectionTable:
    def __init__(self, path: str = CORRECTION_TABLE_PATH):
        self.path = path
        self._data: dict[str, dict[str, str]] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self._data = json.load(f)
        else:
            self._data = {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with _lock:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False, sort_keys=True)

    def get(self, word: str) -> dict[str, str] | None:
        return self._data.get(word.lower())

    def set(self, word: str, phoneme_ipa: str | None = None, text_fallback: str | None = None) -> None:
        key = word.lower()
        entry = self._data.setdefault(key, {})
        if phoneme_ipa:
            entry["phoneme_ipa"] = phoneme_ipa
        if text_fallback:
            entry["text_fallback"] = text_fallback
        self.save()

    def all(self) -> dict[str, dict[str, str]]:
        return dict(self._data)
