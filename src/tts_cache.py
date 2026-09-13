"""Exact-text-match cache for synthesized TTS audio.

Keyed on the exact rendered text (post correction-table) plus voice/encoding
config -- deliberately NOT a semantic/fuzzy match. A fuzzy "close enough"
match could replay a cached clip for the wrong drug name or dosage, which is
a correctness risk this cache must never take. Same text -> same audio is
the only guarantee Google TTS itself makes, so it's the only guarantee this
cache relies on.
"""
import hashlib
import json
import os
import threading

_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tts_cache"
)
_STATS_PATH = os.path.join(_CACHE_DIR, "_stats.json")
_lock = threading.Lock()


def _cache_key(voice_name: str, language_code: str, encoding: str, sample_rate_hertz: int | None, is_ssml: bool, text: str) -> str:
    raw = f"{voice_name}|{language_code}|{encoding}|{sample_rate_hertz}|{is_ssml}|{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _record(hit: bool) -> None:
    with _lock:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        stats = {"hits": 0, "misses": 0}
        if os.path.exists(_STATS_PATH):
            try:
                with open(_STATS_PATH) as f:
                    stats = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        stats["hits" if hit else "misses"] += 1
        with open(_STATS_PATH, "w") as f:
            json.dump(stats, f)


def get_stats() -> dict:
    if os.path.exists(_STATS_PATH):
        try:
            with open(_STATS_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"hits": 0, "misses": 0}


def get_cached_audio(voice_name: str, language_code: str, encoding: str, sample_rate_hertz: int | None, is_ssml: bool, text: str) -> bytes | None:
    """Return cached audio bytes for an identical prior request, or None."""
    key = _cache_key(voice_name, language_code, encoding, sample_rate_hertz, is_ssml, text)
    path = os.path.join(_CACHE_DIR, f"{key}.bin")
    hit = os.path.exists(path)
    _record(hit)
    if not hit:
        return None
    with open(path, "rb") as f:
        return f.read()


def store_audio(voice_name: str, language_code: str, encoding: str, sample_rate_hertz: int | None, is_ssml: bool, text: str, audio_bytes: bytes) -> None:
    key = _cache_key(voice_name, language_code, encoding, sample_rate_hertz, is_ssml, text)
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(os.path.join(_CACHE_DIR, f"{key}.bin"), "wb") as f:
        f.write(audio_bytes)
