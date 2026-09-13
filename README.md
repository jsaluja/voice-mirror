# Voice Mirror

### The self-correcting loop for voice agents.

Voice AI agents mispronounce or hallucinate on critical words — drug names,
account numbers, policy IDs, travel destinations — and nobody catches it
until a customer reports it back or churns to a competitor.

Voice Mirror closes that gap with a self-correcting loop: every spoken
response is run back through ASR to compare what a listener would hear
against what was intended. If it's wrong, the agent retries with phonetic
overrides or SSML until it's right. Cases that still fail get a human in the
loop to listen and approve the correct pronunciation. Approved corrected
audio is saved permanently and reused for free on every future call.

A live dashboard shows pass/fail per case, which words got fixed and how, and
real cost savings from caching — every reused fix is a TTS call you never pay
for again. The correction loop is drug-name-agnostic and industry-agnostic:
the same mechanism plugs into any voice agent that reads back a critical
string out loud, from pharmacy to finance, insurance, and travel, without
retraining or rebuilding a thing.

Built end-to-end on CoreWeave's stack: audit trails and the caching layer run
on CoreWeave, TypeSafe (Jev) is the confidence judge, and Marimo notebooks
power the live dashboard and human-in-the-loop workflow.

## How the loop works

```
LLM response
    │
    ▼
Correction table lookup  ──►  known fix?  ──►  apply phoneme/SSML override
    │
    ▼
TTS synthesis (cached on exact rendered text)
    │
    ▼
Self-listen ASR round-trip
    │
    ▼
Diff intended text vs. what was heard
    │
    ├── match ──► done, ship it
    │
    └── mismatch ──► retry with phonetic override / SSML (bounded attempts)
              │
              ├── now matches ──► done
              │
              └── still fails ──► flag for human review
                        │
                        ▼
              Human listens to every attempt, approves the
              correct one ──► saved permanently to the
              correction table ──► reused free on every future call
```

Every stage is a `weave.op()`, so a full turn — including every retry — shows
up as one trace tree with a complete audit trail.

## Repository layout

```
dashboard.py              Marimo release-gate dashboard (the main demo surface)
scripts/
  quality_gate.py          Offline release gate: renders every case through the
                            production TTS path, transcribes it, and scores it
  demo.py                  CLI demo: type a message -> LLM -> self-correcting TTS
  lint_scripts.py           Batch pronunciation linter over a prompt library
src/
  pipeline.py               Core loop: LLM -> correction lookup -> TTS ->
                            self-listen ASR -> diff -> retry gate
  tts.py                    Google Cloud TTS synthesis (SSML + <phoneme> support)
  tts_cache.py              Exact-text-match audio cache (never fuzzy-matched,
                            to avoid replaying the wrong drug name/dosage)
  correction_table.py       Persistent word -> {phoneme_ipa, text_fallback} memory
  asr.py                    Google Cloud Speech-to-Text for the self-listen check
  diff.py                   Word-level diff between intended text and round-trip
  critic.py                 TypeSafe (Jev) judge: decide when escalating to a
                            text-fallback correction is likely to help
  intake.py                 Patient/caller identity matching with TypeSafe as a
                            confidence gate on ambiguous matches
  llm.py                    Response generation + IPA/text-fallback generation
  vapi_server.py            Vapi webhook server (live phone call integration)
  config.py                 Central env vars + tunables (trap words, thresholds)
data/                       Patients fixture, correction table, quality gate
                            cases/reports (all synthetic demo data)
tests/                      Test suite
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in WANDB_API_KEY, TYPESAFE_API_KEY, etc.
gcloud auth application-default login   # for Google Cloud TTS/STT/Gemini
```

## Running it

**Live release-gate dashboard:**

```bash
marimo run dashboard.py --host 127.0.0.1 --port 2726 --no-token
```

**CLI demo (single turn, no phone call):**

```bash
python -m scripts.demo --text "Tell me about my Vraylar refill"
```

**Full offline quality gate (all cases, release decision + report):**

```bash
python -m scripts.quality_gate --report data/quality_gate_report.json --threshold 0.85 --workers 3
```

**Live phone integration (Vapi):**

```bash
uvicorn src.vapi_server:app --host 0.0.0.0 --port 8000
```

Point a Vapi assistant's custom-LLM and voice-server URLs at this server
(e.g. via an `ngrok` tunnel) to take live calls through the same
self-correcting loop.

## Why it matters

Two-layer caching turns every fix into a permanent, reusable asset:

- **Correction table** — avoids re-generating a phonetic override for a word
  that's already been fixed and human-approved.
- **Exact-text audio cache** — avoids paying for a TTS call when the exact
  same rendered text has already been synthesized, on both offline test runs
  and live calls.

The dashboard surfaces both: cache hit rate, dollars of TTS spend avoided,
and a browsable table of every corrected word with its human-confirmed
pronunciation and pass/fail impact.
