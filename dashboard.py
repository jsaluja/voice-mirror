import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium", auto_download=["html"])


@app.cell
def _():
    import base64
    import os
    import pandas as pd
    import marimo as mo
    import weave
    import wandb

    return base64, mo, os, pd, wandb, weave


@app.cell
def _(mo):
    mo.md("""
    # Pronunciation Lint Report
    Reads lint-time self-listen traces from Weave: pass/fail rate per script
    line, retries used, and before/after audio for each catch. The live
    runtime path never runs this loop -- see `src.pipeline.speak`.
    """)
    return


@app.cell
def _(mo):
    wandb_key_input = mo.ui.text(label='W&B API Key', kind='password', placeholder='paste your WANDB_API_KEY')
    project_input = mo.ui.text(label='Weave project (entity/project)', value='jaspal-singh-saluja/voice-self-correct-loop')
    mo.vstack([wandb_key_input, project_input])
    return project_input, wandb_key_input


@app.cell
def _(mo):
    connect_button = mo.ui.run_button(label='Connect to Weave')
    connect_button
    return (connect_button,)


@app.cell
def _(connect_button, mo, os, project_input, wandb, wandb_key_input, weave):
    client = None
    _status = mo.md('_not connected yet_')
    if connect_button.value and wandb_key_input.value:
        os.environ['WANDB_API_KEY'] = wandb_key_input.value
        wandb.login(key=wandb_key_input.value, relogin=True)
        client = weave.init(project_input.value)
        _status = mo.md(f'Connected to **{project_input.value}**')
    _status
    return (client,)


@app.cell
def _(mo):
    fetch_button = mo.ui.run_button(label='Refresh lint traces')
    fetch_button
    return (fetch_button,)


@app.cell
def _(base64, client, fetch_button, pd):
    lines_data = []
    if client is not None and fetch_button.value:
        calls = list(client.get_calls(limit=500, sort_by=[{'field': 'started_at', 'direction': 'desc'}]))
        pt_calls = [c for c in calls if '/process_turn:' in c.op_name]
        tts_calls = [c for c in calls if '/synthesize_speech:' in c.op_name]
        tts_by_trace = {}
        for t in tts_calls:
            tts_by_trace.setdefault(t.trace_id, []).append(t)

        def _audio_bytes(call):
            # call.output may come back as a Content object or a plain dict
            # (base64-encoded) depending on how weave deserializes it.
            out = call.output
            if out is None:
                return None
            data = out.data if hasattr(out, 'data') else out.get('data') if isinstance(out, dict) else None
            if isinstance(data, str):
                try:
                    return base64.b64decode(data)
                except Exception:
                    return None
            return data

        for c in pt_calls:
            out = c.output
            if out is None:
                continue
            attempts = list(out.attempts) if out.attempts else []
            trace_tts = sorted(tts_by_trace.get(c.trace_id, []), key=lambda t: t.started_at)
            before_audio = _audio_bytes(trace_tts[0]) if trace_tts else None
            after_audio = _audio_bytes(trace_tts[-1]) if trace_tts else None
            lines_data.append({
                'trace_id': c.trace_id,
                'started_at': c.started_at,
                'intended_text': out.intended_text,
                'success': out.success,
                'num_attempts': len(attempts),
                'final_wer': attempts[-1].wer if attempts else None,
                'before_audio': before_audio,
                'after_audio': after_audio,
            })

    # calls are newest-first, so keep only each line's most recent lint run --
    # otherwise repeated test runs of the same script line (e.g. trap words
    # tested many times during development) clutter the report with duplicates.
    _seen_texts = set()
    _deduped = []
    for _r in lines_data:
        if _r['intended_text'] in _seen_texts:
            continue
        _seen_texts.add(_r['intended_text'])
        _deduped.append(_r)
    lines_data = _deduped

    calls_df = pd.DataFrame([{k: v for k, v in r.items() if k not in ('before_audio', 'after_audio')} for r in lines_data])
    calls_df
    return calls_df, lines_data


@app.cell
def _(mo):
    mo.md("""
    ## Lint Metrics
    """)
    return


@app.cell
def _(calls_df, mo):
    import altair as alt

    if not calls_df.empty:
        success_counts = calls_df['success'].value_counts().rename_axis('success').reset_index(name='count')
        success_chart = mo.ui.altair_chart(
            alt.Chart(success_counts).mark_arc().encode(
                theta='count', color='success:N', tooltip=['success', 'count']
            ).properties(title='Line pass rate')
        )
        attempts_chart = mo.ui.altair_chart(
            alt.Chart(calls_df).mark_bar().encode(
                x=alt.X('num_attempts:O', title='Attempts used'),
                y=alt.Y('count()', title='Lines'),
                color='success:N',
                tooltip=['num_attempts', 'count()'],
            ).properties(title='Attempts per line')
        )
        charts = mo.hstack([success_chart, attempts_chart])
    else:
        charts = mo.md('_No data yet -- click Refresh lint traces above._')
    charts
    return


@app.cell
def _(mo):
    mo.md("""
    ## Before / After Audio
    """)
    return


@app.cell
def _(mo, lines_data):
    line_options = {
        f"[{i}] {r['intended_text'][:55]} ({'PASS' if r['success'] else 'FAIL'}, {r['num_attempts']} attempt(s), {'audio' if r['before_audio'] else 'no audio'})": i
        for i, r in enumerate(lines_data)
    }
    line_picker = mo.ui.dropdown(options=line_options, label='Pick a script line') if line_options else None
    line_picker
    return (line_picker,)


@app.cell
def _(lines_data, line_picker, mo):
    if line_picker is not None and line_picker.value is not None:
        row = lines_data[line_picker.value]
        before = mo.audio(row['before_audio']) if row['before_audio'] else mo.md('_no audio_')
        after = mo.audio(row['after_audio']) if row['after_audio'] else mo.md('_no audio_')
        audio_ui = mo.vstack([
            mo.md(f"**Script line:** {row['intended_text']}"),
            mo.hstack([mo.vstack([mo.md('**Before (attempt 0)**'), before]), mo.vstack([mo.md('**After (final attempt)**'), after])]),
        ])
    else:
        audio_ui = mo.md('_Pick a script line above to compare audio._')
    audio_ui
    return


if __name__ == "__main__":
    app.run()
