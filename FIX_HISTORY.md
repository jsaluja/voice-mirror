# Fix History / Known Issues

Durable engineering notes for this repo -- bugs found, root causes, fixes,
and known limitations. Keep entries short.

## Stale docstring implied Google ASR transcribes live caller speech (it doesn't)

`src/asr.py` claimed `transcribe_audio()` was "used both for user speech
input and for the self-listen round-trip check." Only the second half was
true. `transcribe_audio()` is called from exactly one place
(`src/pipeline.py: process_turn()`), transcribing our *own* synthesized TTS
audio as an offline intelligibility proxy. `src/vapi_server.py` never calls
it -- live calls are transcribed entirely by Vapi's own transcriber before
reaching our custom-LLM webhook as plain text. TTS is the one path that
genuinely is shared: both live calls (`synthesize_speech_pcm`, LINEAR16 for
Vapi's custom-voice webhook) and the offline gate (`synthesize_speech`, MP3)
call the same `texttospeech.TextToSpeechClient()` with the same voice --
only the output container differs. Fixed the docstring to state this.

## Dashboard showed disagreeing totals (20 vs 5/3 vs 8 entries)

Root cause: two separate suite/report files (`quality_gate_cases.json` +
`quality_gate_cases_blocked.json`) were merged back together in the
dashboard by matching on `intended_text`, and a second code path also pulled
in *any* historical Weave `process_turn` trace not tied to a current suite
case (labeled "pronunciation lint"), even if it belonged to an old,
unrelated ad hoc run. That produced three different, silently-conflated
case populations: the per-report summary, the flattened per-check table,
and a merged current+historical call list feeding the dropdown.

Fixed by consolidating to **one suite file, one report file, one case
list**. `data/quality_gate_cases.json` is now the single canonical suite
(pharmacy + finance + travel + insurance cases, including the real Vraylar
TTS failure as an ordinary case rather than a separately-tracked "blocked"
duplicate). `dashboard.py` now always iterates `release_report["results"]`
to build every row; Weave traces are looked up only to *enrich* a case
with audio/attempt evidence and can never add or drop a row. Case-table
row count, dropdown entry count, and the summary total are now the same
number by construction. The separate, larger "checks" table (assertion +
semantic-requirement + voice-preservation rows) is explicitly labeled as a
different granularity so it's never mistaken for a second case count.

Restoring the pharmacy corpus surfaced real new TTS failures beyond the
known Vraylar case: Metoprolol Succinate, Atorvastatin, Rybelsus, and
Farxiga all currently mis-transcribe through the real Google Cloud
TTS/ASR pipeline (5/10 cases blocked). These are genuine pipeline results,
not fabricated regressions.

## Text/content checks were incorrectly added to the TTS quality gate

An attempted banking regression supplied manually corrupted text directly to
TTS and blocked it against different approved facts. TTS and ASR agreed, so this
was a text/content failure and did not belong in this project. A later attempt to
add an agent-generation stage also expanded the scope incorrectly.

The quality gate is now strictly `intended_text -> TTS -> listener ASR -> voice
verdict`. It does not generate or fact-check text. The blocked regression uses a
real TTS failure: intended `Vraylar` is heard as `regular`. The dashboard shows
only intended text, listener transcript, pronunciation/fidelity checks, retries,
and playable audio.

## Failed pronunciation retry returned a worse final attempt

The Vraylar lint trace exposed a retry regression: the phoneme attempt was
heard as "regular" with WER 0.14, while the final `VRAY-lar` text fallback was
spelled out as "v r. A y l" with WER 0.71. The line was correctly marked as
failed, but `process_turn()` returned the final audio and divergence even when
an earlier attempt scored better.

Fixed in `src/pipeline.py` by retaining the lowest-WER audio and divergence
throughout the retry loop. Exhausted retries remain `success=False`, but a
degrading strategy can no longer replace the best candidate in the returned
result. The marimo dashboard also labels such historical attempts as rejected
regressions rather than implying that the final retry was an improvement.

## Inbound caller-name ASR misrecognition (open, not yet fixed)

During the first live Vapi web test call, the caller said "Jaspal Singh
Saluja" but Vapi's default transcriber (STT) heard "Sysco Singh Solutions".
Since this didn't match any record in `data/patients.json`, the intake flow
correctly fell through to the "couldn't verify identity, transferring to a
pharmacist" escalation path -- so the *code* behaved correctly, but the
*STT* introduced a real-world failure mode we don't currently correct for.

This is the mirror-image problem to the outbound TTS mispronunciation this
repo already fixes (`data/correction_table.json` + self-listen loop): there
we validate our own *output* speech before it ships; here the *input*
speech from the caller can be misheard and we have no correction step at all.

Potential future fix: after name/DOB/zip are all collected but no patient
matches, or even right after the name turn, read the name back for
confirmation ("I heard the name as ___, is that right?") before treating a
non-match as final -- cheap to add given the multi-turn intake loop already
exists in `src/intake.py`.

## Gemini structured extraction returns literal string "null"

`extract_patient_query()` calling Gemini via Vertex AI with only a bare name
(e.g. "Jane Smith", no other fields) sometimes returned the literal string
`"null"` (not JSON null) for unset optional fields. This passed old
`fields.get("dob") or None` checks as truthy, so `next_intake_prompt()`
thought all fields were already collected and skipped straight to
(incorrect) identity matching after just the name.

Fixed in `src/intake.py` with a `_clean(value)` helper treating the
case-insensitive literal string `"null"` as `None`, applied to all 4
extracted fields.

## Vapi html-script-tag widget: `start()` needs explicit assistant ID

`window.vapiSDK.run({ apiKey, assistant, config })` does NOT wire up a
custom button's `vapiInstance.start()` (no args) to the configured
assistant -- it throws `"Assistant or Squad or Workflow must be provided."`
Must call `vapiInstance.start(assistant)` explicitly, passing the assistant
ID, even though it was already passed to `run()`.

## Vapi web test page must be served over http://, not file://

Opening `scripts/vapi_test_call.html` directly as a `file://` URL breaks
the call: Daily.js/Krisp noise-cancellation needs to load an AudioWorklet
via a `blob:` URL, and Chrome treats every `file://` page as a unique
security origin, so blob/worklet loading fails ("Not allowed to load local
resource: blob:...", "KrispInitError: WORKLET_NOT_SUPPORTED"). No requests
ever reached the ngrok tunnel when this happened -- the call silently never
starts. Fix: serve the page over plain local HTTP, e.g.
`python3 -m http.server 8765` from `scripts/`, and open
`http://127.0.0.1:8765/vapi_test_call.html` instead.
