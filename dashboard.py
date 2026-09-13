import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium", auto_download=["html"])


@app.cell
def _():
    import base64
    import os
    from pathlib import Path
    import altair as alt
    import pandas as pd
    import marimo as mo
    import weave
    import wandb

    return Path, alt, base64, mo, os, pd, wandb, weave


@app.cell
def _(mo):
    mo.md("""
    # Voice Mirror

    ### The self-correcting loop for voice agents.

    Pharmacy, finance, insurance, travel — any voice agent that reads back a
    drug name, an account number, a policy ID, or a flight code can mispronounce
    or hallucinate it, and nobody catches it until a customer churns. Voice
    Mirror runs every spoken response back through ASR, retries with phonetic
    overrides until it's right, and escalates to a human when it can't self-fix
    — permanently, for every future call.
    """)
    return


@app.cell
def _(Path):
    import json

    # Single canonical report -- every table, chart, and dropdown below is
    # derived from this same case list so their counts always agree.
    _report_path = Path("data/quality_gate_report.json")
    if _report_path.exists():
        release_report = json.loads(_report_path.read_text(encoding="utf-8"))
    else:
        release_report = None
    return (release_report,)


@app.cell
def _(mo, release_report):
    if release_report is not None:
        _decision = release_report["decision"]
        _summary = release_report["summary"]
        _manifest = release_report["manifest"]
        _icon = "PASS" if _decision == "APPROVED" else "STOP"
        release_overview = mo.md(
            f"""
            ### {_icon}: Voice release gate — {_decision}

            **{_summary['passed']}/{_summary['total']} cases passed**
            """
        )
    else:
        release_overview = mo.md(
            "_No release report found. Run `python -m scripts.quality_gate` to create one._"
        )
    release_overview
    return


@app.cell
def _(Path, alt, mo, pd, release_report):
    import json as _json_cache_stats

    # Google Cloud TTS Neural2 pricing: $16 / 1M characters synthesized.
    _NEURAL2_PRICE_PER_MILLION_CHARS = 16.0
    # Ground the per-call cost in the actual case texts rather than a guess.
    _case_texts = [r["intended_text"] for r in release_report["results"]] if release_report else []
    _avg_chars = (sum(len(t) for t in _case_texts) / len(_case_texts)) if _case_texts else 60.0
    _cost_per_call = _avg_chars / 1_000_000 * _NEURAL2_PRICE_PER_MILLION_CHARS

    _stats_path = Path("data/tts_cache/_stats.json")
    if _stats_path.exists():
        _stats = _json_cache_stats.loads(_stats_path.read_text(encoding="utf-8"))
        _hits, _misses = _stats.get("hits", 0), _stats.get("misses", 0)
        _total = _hits + _misses
        _rate = (_hits / _total * 100) if _total else 0.0
        _saved_so_far = _hits * _cost_per_call
        # Projection: what this hit rate is worth at a real pharmacy IVR line's call volume.
        _calls_per_month_at_scale = 100_000
        _saved_per_month_at_scale = (_rate / 100) * _calls_per_month_at_scale * _cost_per_call

        _cache_df = pd.DataFrame([
            {"result": "Cache hit (TTS call avoided)", "count": _hits},
            {"result": "Cache miss (TTS call made)", "count": _misses},
        ])
        _donut = alt.Chart(_cache_df).mark_arc(innerRadius=70, outerRadius=120).encode(
            theta=alt.Theta("count:Q"),
            color=alt.Color(
                "result:N",
                title=None,
                scale=alt.Scale(
                    domain=["Cache hit (TTS call avoided)", "Cache miss (TTS call made)"],
                    range=["#2a9d5c", "#d1495b"],
                ),
            ),
            tooltip=["result", "count"],
        ).properties(width=260, height=260)
        cache_overview = mo.hstack([
            mo.ui.altair_chart(_donut),
            mo.vstack([
                mo.stat(
                    value=f"{_rate:.0f}%",
                    label="Cache hit rate",
                    caption=f"{_hits} of {_total} TTS requests served from cache",
                    direction="increase",
                    bordered=True,
                ),
                mo.stat(
                    value=f"${_saved_so_far:,.2f}",
                    label="Saved so far",
                    caption="Google TTS spend avoided in this test suite",
                    direction="increase",
                    bordered=True,
                ),
                mo.stat(
                    value=f"${_saved_per_month_at_scale:,.0f}/mo",
                    label="Projected at scale",
                    caption=f"At {_calls_per_month_at_scale:,} calls/month, same hit rate",
                    direction="increase",
                    bordered=True,
                ),
            ]),
        ], justify="center", align="center", gap=2)
    else:
        cache_overview = mo.md("_No TTS cache stats found yet._")
    cache_overview
    return


@app.cell
def _(mo, os, wandb, weave):
    # Autonomous connect: read creds from the environment (.env / shell), no
    # human paste-and-click step -- same WANDB_API_KEY/WEAVE_PROJECT the CLI
    # scripts (lint_scripts.py, demo.py) already use.
    from src.config import WANDB_API_KEY, WEAVE_PROJECT

    client = None
    if WANDB_API_KEY:
        os.environ.setdefault('WANDB_API_KEY', WANDB_API_KEY)
        wandb.login(key=WANDB_API_KEY, relogin=True)
        client = weave.init(WEAVE_PROJECT)
        _status = mo.md(f'Connected to **{WEAVE_PROJECT}**')
    else:
        _status = mo.md('_WANDB_API_KEY not set -- export it or add it to .env, then rerun this notebook._')
    _status
    return (client,)


@app.cell
def _(base64, client, pd, release_report):
    # Every row here comes from a suite case in release_report -- Weave is only
    # consulted to enrich a case with audio/attempt evidence. It can never add
    # or drop a row, so this table's count always equals the case count above.
    _cases = release_report["results"] if release_report is not None else []

    def content_bytes(value):
        # Weave content may deserialize as bytes, a Content object, or a
        # plain dict containing base64 data.
        if value is None:
            return None
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        data = value.data if hasattr(value, 'data') else value.get('data') if isinstance(value, dict) else None
        if isinstance(data, str):
            try:
                return base64.b64decode(data)
            except Exception:
                return None
        return bytes(data) if isinstance(data, (bytes, bytearray)) else None

    _pt_by_text = {}
    if client is not None:
        # High limit -- large suites (300+ cases) generate 1000s of traced calls,
        # and every case's process_turn call must be reachable, not just recent ones.
        _calls = list(client.get_calls(limit=5000, sort_by=[{'field': 'started_at', 'direction': 'desc'}]))
        _pt_calls = [c for c in _calls if '/process_turn:' in c.op_name]
        _tts_calls = [c for c in _calls if '/synthesize_speech:' in c.op_name]
        _tts_by_trace = {}
        for _t in _tts_calls:
            _tts_by_trace.setdefault(_t.trace_id, []).append(_t)
        # Calls are newest-first; keep only the most recent trace per intended text.
        for _c in _pt_calls:
            _out = _c.output
            if _out is None or _out.intended_text in _pt_by_text:
                continue
            _pt_by_text[_out.intended_text] = (_c, _out, _tts_by_trace.get(_c.trace_id, []))

    lines_data = []
    # Raw (undecoded) trace material per scenario -- audio is only decoded on demand for
    # whichever row is selected, instead of eagerly for every attempt of every case.
    audio_sources_by_scenario = {}
    for _result in _cases:
        _match = _pt_by_text.get(_result["intended_text"])
        _attempts, _trace_tts = [], []
        _best_attempt = _last_attempt = None
        if _match is not None:
            _call, _out, _trace_tts = _match
            _attempts = list(_out.attempts) if _out.attempts else []
            _trace_tts = sorted(_trace_tts, key=lambda t: t.started_at)
            _best_attempt = min(_attempts, key=lambda attempt: attempt.wer) if _attempts else None
            _last_attempt = _attempts[-1] if _attempts else None
            audio_sources_by_scenario[_result['id']] = (_attempts, _trace_tts, _out, _best_attempt)

        _failed_checks = [
            assertion["id"] for assertion in _result["assertions"] if not assertion["passed"]
        ] + [
            requirement["requirement"]
            for requirement in _result["semantic_requirements"]
            if not requirement["passed"]
        ]

        lines_data.append({
            'scenario_id': _result['id'],
            'industry': _result['industry'],
            'intended_text': _result['intended_text'],
            'status': 'PASS' if _result['passed'] else 'FAIL',
            'overall_passed': _result['passed'],
            'voice_passed': _result['voice_passed'],
            'failed_checks': _failed_checks,
            'listener_transcript': _result['listener_transcript'],
            'num_attempts': len(_attempts),
            'selected_attempt': _best_attempt.attempt if _best_attempt is not None else None,
            'final_wer': _best_attempt.wer if _best_attempt is not None else None,
            'last_attempt_wer': _last_attempt.wer if _last_attempt is not None else None,
            'retry_regressed': bool(
                _best_attempt is not None
                and _last_attempt is not None
                and _last_attempt.wer > _best_attempt.wer
            ),
        })

    lines_data = sorted(lines_data, key=lambda row: row['scenario_id'])

    calls_df = pd.DataFrame([
        {k: v for k, v in r.items() if k != 'failed_checks'}
        for r in lines_data
    ])
    return audio_sources_by_scenario, calls_df, content_bytes, lines_data


@app.cell
def _(mo):
    mo.md("""
    ## Correction Table -- Fixed Drug Names

    Every word below was auto-flagged mid-pipeline as mispronounced, given a
    phonetic override, and retried. `human_confirmed` means a person has
    listened to the audio and approved that override for permanent reuse
    (see the review panel under a selected case below).
    """)
    return


@app.cell
def _(Path, mo, pd, release_report):
    import json as _json_correction_table

    from src.config import TRAP_WORDS as _TRAP_WORDS
    import re as _re_correction

    _table_path = Path("data/correction_table.json")
    _correction_table = (
        _json_correction_table.loads(_table_path.read_text(encoding="utf-8"))
        if _table_path.exists() else {}
    )

    # Cross-reference each corrected word against the report to show how many
    # attempts it took and whether the fix ultimately stuck.
    _cases = release_report["results"] if release_report is not None else []
    _rows = []
    for _word, _entry in sorted(_correction_table.items()):
        _matches = [
            r for r in _cases
            if _word in _re_correction.findall(r"[a-z0-9']+", r["intended_text"].lower())
        ]
        _rows.append({
            "word": _word,
            "phoneme_ipa": _entry.get("phoneme_ipa") or "",
            "text_fallback": _entry.get("text_fallback") or "",
            "human_confirmed": bool(_entry.get("human_confirmed")),
            "cases_affected": len(_matches),
            "cases_passed": sum(1 for r in _matches if r["passed"]),
        })

    correction_table_df = pd.DataFrame(_rows)
    correction_table_view = (
        mo.ui.table(correction_table_df, selection=None)
        if not correction_table_df.empty
        else mo.md("_No correction table entries yet._")
    )
    correction_table_view
    return


@app.cell
def _(mo):
    mo.md("""
    ## Industry Summary

    Click a pie slice below to drill into that industry + status; the
    case table and audio underneath update to match. Click the same slice
    again to clear the filter and see every case.
    """)
    return


@app.cell
def _(alt, calls_df, mo):
    _industry_status = (
        calls_df.groupby(['industry', 'status']).size().reset_index(name='count')
        if not calls_df.empty else calls_df
    )
    _click = alt.selection_point(fields=['industry', 'status'], name='industry_status_click')
    if not calls_df.empty:
        industry_chart = mo.ui.altair_chart(
            alt.Chart(_industry_status).mark_arc(innerRadius=45).encode(
                theta=alt.Theta('count:Q', title='Cases'),
                color=alt.Color(
                    'status:N',
                    title='Status',
                    scale=alt.Scale(domain=['PASS', 'FAIL'], range=['#2a9d5c', '#d1495b']),
                ),
                opacity=alt.condition(_click, alt.value(1.0), alt.value(0.35)),
                column=alt.Column('industry:N', title=None),
                tooltip=['industry', 'status', 'count'],
            ).add_params(_click).properties(title='Cases by industry and status -- click a slice', width=140, height=140)
        )
    else:
        industry_chart = mo.md(
            "_No release report found. Run `python -m scripts.quality_gate` to create one._"
        )
    industry_chart
    return (industry_chart,)


@app.cell
def _(calls_df, industry_chart, mo):
    _selected = getattr(industry_chart, 'value', None)
    if _selected is not None and not _selected.empty:
        _pairs = set(zip(_selected['industry'], _selected['status']))
        industry_df = calls_df[
            calls_df.apply(lambda r: (r['industry'], r['status']) in _pairs, axis=1)
        ]
        _label = ", ".join(f"{industry} / {status}" for industry, status in sorted(_pairs))
    else:
        industry_df = calls_df
        _label = "all industries"
    mo.md(f"### Cases -- {_label}")
    return (industry_df,)


@app.cell
def _(industry_df, mo):
    case_table = mo.ui.table(
        industry_df[['scenario_id', 'industry', 'status', 'intended_text', 'listener_transcript', 'num_attempts']],
        selection='single',
        label='Click a case to hear its audio below',
    ) if not industry_df.empty else None
    case_table
    return (case_table,)


@app.cell
def _(audio_sources_by_scenario, case_table, content_bytes, lines_data, mo):
    _selection = getattr(case_table, 'value', None)
    if _selection is not None and not _selection.empty:
        _scenario_id = _selection.iloc[0]['scenario_id']
        row = next((r for r in lines_data if r['scenario_id'] == _scenario_id), None)
    else:
        row = None

    if row is not None:
        # No matching Weave trace (e.g. call fell outside the fetch window) -- show "n/a" instead of crashing.
        _wer = lambda value: f"{value:.2f}" if value is not None else "n/a"
        if row['retry_regressed']:
            outcome = mo.md(
                f"**VOICE FAIL — retry regression rejected.** Attempt {row['selected_attempt']} "
                f"was retained at WER {_wer(row['final_wer'])}; the last retry worsened "
                f"to {_wer(row['last_attempt_wer'])}. "
                + ("Failed checks: " + "; ".join(row['failed_checks']) if row['failed_checks'] else "")
            )
        elif row['overall_passed']:
            outcome = mo.md(f"**VOICE PASS.** Selected WER {_wer(row['final_wer'])}.")
        else:
            failed = " Failed checks: " + "; ".join(row['failed_checks']) if row['failed_checks'] else ""
            outcome = mo.md(
                f"**VOICE FAIL** — no attempt preserved the intended speech; "
                f"best WER {_wer(row['final_wer'])}.{failed}"
            )
        # Audio is decoded here, on demand, only for the one selected row's attempts.
        _audio_players = []
        _attempts, _trace_tts, _out, _best_attempt = audio_sources_by_scenario.get(
            row['scenario_id'], ([], [], None, None)
        )
        _selected_audio_bytes = content_bytes(_out.audio) if _out is not None else None
        for _attempt in _attempts:
            _audio_bytes = content_bytes(_trace_tts[_attempt.attempt].output) if _attempt.attempt < len(_trace_tts) else None
            _is_selected = _best_attempt is not None and _attempt.attempt == _best_attempt.attempt
            if _is_selected and _selected_audio_bytes is not None:
                _audio_bytes = _selected_audio_bytes
            _label = f"Attempt {_attempt.attempt} (WER {_wer(_attempt.wer)})"
            if _is_selected:
                _label += " — selected"
            elif row['retry_regressed'] and _attempt.attempt == row['num_attempts'] - 1:
                _label += " — rejected retry"
            _player = mo.audio(_audio_bytes) if _audio_bytes else mo.md('_no audio_')
            # Show what the ASR actually heard for this specific attempt, so "sounds right but ASR misheard it"
            # is visibly distinguishable from "TTS itself broke" (e.g. letter-spelling regressions).
            _audio_players.append(mo.vstack([
                mo.md(f"**{_label}**"),
                _player,
                mo.md(f"_ASR heard:_ \"{_attempt.roundtrip_text}\""),
            ]))
        if not _audio_players:
            _audio_players = [mo.md('_no audio available_')]
        audio_ui = mo.vstack(
            [
                mo.md(f"**Intended text:** {row['intended_text']}"),
                mo.md(f"**Listener transcript:** {row['listener_transcript']}"),
                outcome,
                mo.hstack(_audio_players),
            ]
        )
    else:
        audio_ui = mo.md('_Click a row in the case table above to hear its audio._')
    audio_ui
    return (row,)


@app.cell
def _(audio_sources_by_scenario, mo, row):
    # Human-in-the-loop: a person listens to each attempt above and approves
    # whichever one actually sounds right -- that approval is what makes a
    # correction-table entry permanent and safe to reuse on every future call,
    # instead of trusting WER/semantic-judge scoring alone.
    import re as _re_review

    from src.config import TRAP_WORDS as _TRAP_WORDS_REVIEW

    if row is None:
        review_panel = mo.md("")
        relevant_words = []
        attempt_picker = None
        approve_button = None
    else:
        relevant_words = sorted(
            set(_re_review.findall(r"[a-z0-9']+", row['intended_text'].lower())) & _TRAP_WORDS_REVIEW
        )
        _attempts, _trace_tts, _out, _best_attempt = audio_sources_by_scenario.get(
            row['scenario_id'], ([], [], None, None)
        )
        if not relevant_words or not _attempts:
            review_panel = mo.md("")
            attempt_picker = None
            approve_button = None
        else:
            attempt_picker = mo.ui.dropdown(
                options={
                    f"Attempt {a.attempt} (WER {a.wer:.2f}) -- \"{a.roundtrip_text}\"": a.attempt
                    for a in _attempts
                },
                label="Which attempt above sounded correct to you?",
            )
            approve_button = mo.ui.run_button(label="Approve & save to correction table")
            review_panel = mo.vstack([
                mo.md(
                    f"**Human review** -- critical term(s) in this case: `{', '.join(relevant_words)}`. "
                    "Listen to the attempts above, then approve the one that actually sounds right."
                ),
                attempt_picker,
                approve_button,
            ])
    review_panel
    return approve_button, attempt_picker, relevant_words


@app.cell
def _(Path, approve_button, attempt_picker, audio_sources_by_scenario, mo, relevant_words, row):
    import json as _json_approve

    if not approve_button or not approve_button.value or attempt_picker.value is None:
        approval_result = mo.md("")
    else:
        _attempts, _trace_tts, _out, _best_attempt = audio_sources_by_scenario.get(
            row['scenario_id'], ([], [], None, None)
        )
        _chosen = next((a for a in _attempts if a.attempt == attempt_picker.value), None)
        _table_path = Path("data/correction_table.json")
        _table = (
            _json_approve.loads(_table_path.read_text(encoding="utf-8"))
            if _table_path.exists() else {}
        )
        for _word in relevant_words:
            _entry = _table.setdefault(_word, {})
            # A plain (non-SSML) attempt sounding right means no override is needed at all.
            _entry["human_confirmed"] = True
            _entry["confirmed_attempt_transcript"] = _chosen.roundtrip_text if _chosen else None
            _entry["override_needed"] = bool(_chosen.used_ssml) if _chosen else _entry.get("override_needed", False)
        _table_path.write_text(
            _json_approve.dumps(_table, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        approval_result = mo.md(
            f"**Saved.** `{', '.join(relevant_words)}` marked `human_confirmed` "
            f"against attempt {attempt_picker.value}'s pronunciation. Reload the "
            "Correction Table above to see it."
        )
    approval_result
    return


@app.cell
def _(mo, pd, release_report):
    _rows = []
    for _result in (release_report["results"] if release_report is not None else []):
        for _assertion in _result["assertions"]:
            _rows.append(
                {
                    "industry": _result["industry"],
                    "scenario": _result["id"],
                    "check": _assertion["id"],
                    "evaluator": "listener transcript assertion",
                    "passed": _assertion["passed"],
                    "score": None,
                }
            )
        for _requirement in _result["semantic_requirements"]:
            _rows.append(
                {
                    "industry": _result["industry"],
                    "scenario": _result["id"],
                    "check": _requirement["requirement"],
                    "evaluator": "TypeSafe Noul",
                    "passed": _requirement["passed"],
                    "score": round(_requirement["probability"], 3),
                }
            )
        _rows.append(
            {
                "industry": _result["industry"],
                "scenario": _result["id"],
                "check": "Intended text preserved in listener transcript",
                "evaluator": "TypeSafe Noul + pronunciation lint",
                "passed": _result["voice_passed"],
                "score": round(_result["voice_preservation_probability"], 3),
            }
        )
    release_checks_df = pd.DataFrame(_rows)

    mo.accordion(
        {
            "Advanced: check-level detail (not part of the case count above)": release_checks_df
        }
    )
    return


if __name__ == "__main__":
    app.run()
