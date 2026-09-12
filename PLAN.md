# Self-Correcting Voice Agent — CoreWeave Hacks (Sep 12-13, 2026)

A voice agent that verifies its own spoken output by listening to itself,
catching pronunciation errors before the user hears them, and permanently
fixing them so it never makes the same mistake twice.

## The loop

1. **User speaks** → ASR transcribes → LLM generates a text response.
2. **TTS synthesizes** the response into audio.
3. **Self-listen step:** feed that synthesized audio back through ASR (or a
   forced-alignment/confidence model) to get a "what a listener would
   actually hear" transcript.
4. **Compare** the round-trip transcript against the intended text:
   - Word-level diff (Levenshtein / WER via `jiwer`)
   - Flag mispronunciations of names, numbers, acronyms, domain terms
   - Flag low ASR confidence spans (indicates ambiguous/mumbled audio)
5. **Decision gate:**
   - Divergence below threshold → play audio to user, log success.
   - Divergence above threshold → don't play it yet. Instead:
     - Rewrite the response using a correction (SSML `<phoneme>` override or
       plain-text substitution)
     - Re-synthesize and re-check (bounded retry, max 2-3 attempts)
6. **Log every attempt** (original text, audio, round-trip transcript, diff
   score, action taken) as a Weave trace so the correction is fully visible
   and replayable.
7. **Learn across turns/sessions:** maintain a persistent correction table —
   once a word fails, permanently store its fix so future turns don't need
   the retry loop at all.

## Correction table

Each entry has two remediation strategies, tried in order:

```json
{
  "coreweave": {
    "phoneme_ipa": "kɔːɹˈwiːv",
    "text_fallback": "Core Weave"
  }
}
```

1. Try `<phoneme alphabet="ipa" ph="...">` first (if the TTS engine honors
   SSML phoneme overrides).
2. If the round-trip check still fails (or the engine ignores SSML, e.g.
   ElevenLabs), fall back to a plain-text substitution — no SSML needed,
   works on any engine since it's just different input text.

Phonetic spellings for the correction table can come from:
- Asking the LLM directly for an IPA transcription (cheap, decent for
  common words)
- A G2P library (`g2p_en`) or dictionary (CMUdict/ARPAbet, needs mapping to
  IPA)
- Hardcoded for known "trap words" ahead of the demo (most reliable)

## Stack

- **ASR:** faster-whisper or Deepgram/AssemblyAI streaming (need word-level
  confidence scores for the divergence check)
- **TTS:** Google Cloud TTS / Amazon Polly / Azure TTS (full SSML incl.
  `<phoneme>`) — ElevenLabs has weak/no SSML support, use text-fallback path
  if using it
- **LLM:** response generation + "rewrite on failure" step
- **Tracing:** Weave (`weave.op()` around transcribe / generate / synthesize
  / self-check / rewrite)
- **Dashboard:** marimo notebook reading from Weave — plot correction rate
  over time, live audio player for before/after clips
- **Diffing:** `jiwer` (WER) or token-level Levenshtein + ASR confidence
  threshold

## Why this fits the judging categories

- **Best Loop Design:** the loop is self-contained and visually obvious —
  before/after audio of a mispronunciation getting caught and fixed live.
- **Best Use of Weave:** every attempt is a natural trace tree; dashboard
  shows error rate trending down as the correction table grows.
- **Most Production-Ready:** self-correction + persistent lexicon fixes are
  real production voice-system techniques, not a toy gimmick.
- **Best Social Media demo:** "watch the AI catch itself mispronouncing its
  own words" is a clean 30-second clip.

## MVP scope (~24hr hackathon)

- Turn-by-turn processing (record → process → respond), no streaming.
- Hardcode a small set of "trap words" (numbers, tricky names/acronyms) to
  reliably trigger visible corrections during the demo.
- Cap retries at 2 attempts to keep latency reasonable.
- Correction-table persistence as a simple JSON file keyed by word →
  override; nothing fancier needed.
- Reserve the last few hours for the marimo dashboard + a clean demo script:
  say a tricky word → show the catch + fix → say it again later and show
  it's instant the second time (already in the correction table).

## Demo script

1. Say a name/number that's known to trip up the TTS.
2. Show the round-trip ASR catching the mismatch (live trace in Weave/marimo).
3. Show the `<phoneme>` (or text-fallback) correction being generated and
   applied, and play the corrected audio.
4. Later in the same session, say the same word again — show it's now
   correct on the first try, no retry needed, because it's already in the
   correction table.
