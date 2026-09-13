"""Vapi webhook server: exposes our pipeline as two Vapi integration points.

- POST /llm/chat/completions -- OpenAI-compatible "custom LLM" endpoint.
    Vapi sends the full conversation so far. TypeSafe selects which caller
    response contains each intake field, then ordinary code normalizes the
    selected name, date of birth, and zip code. This demo intentionally does
    not perform patient matching or identity verification.

- POST /tts/synthesize -- Vapi's "custom voice" endpoint. Vapi sends
  plain text; we apply the already-lint-validated correction table and
  return raw LINEAR16 PCM. This is the zero-latency runtime path --
  pronunciation correctness is earned offline (scripts/lint_scripts.py),
  not re-verified on every live call.
"""
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from typesafe_sdk import Choice, TypeSafeClient

from src.config import MAX_RETRIES
from src.correction_table import CorrectionTable
from src.tts import build_ssml, synthesize_speech_pcm

app = FastAPI()
_correction_table = CorrectionTable()
_tts_text_events: list[dict[str, int | str]] = []
_tts_text_sequence = 0

_ESCALATION_MESSAGE = (
    "I still wasn't able to capture that information. "
    "Let's try once more."
)

_INTAKE_PROMPTS = (
    ("name", "What is your full name, please?"),
    ("dob", "Thanks. What is your date of birth?"),
    ("zip_code", "And what is the zip code on your account?"),
)


@dataclass
class IntakeFields:
    name: str | None = None
    dob: str | None = None
    zip_code: str | None = None
    prescription_was_shared: bool = False
    caller_is_done: bool = False


def _response_groups(messages: list[dict]) -> list[str]:
    """Group consecutive caller fragments into candidate spoken responses."""
    groups: list[str] = []
    fragments: list[str] = []
    for message in messages:
        role = message.get("role")
        content = str(message.get("content", "")).strip()
        if role == "user" and content:
            fragments.append(content)
        elif role == "assistant" and fragments:
            groups.append(" ".join(fragments))
            fragments = []
    if fragments:
        groups.append(" ".join(fragments))
    return groups


def _normalize_name(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"^(?:yeah|yes|sure|okay|ok)[.,]?\s+", "", value.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(
        r"^(?:my name is|this is|that is|i am|i'm)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[^A-Za-z' -]", "", cleaned).strip()
    return cleaned.title() if cleaned else None


def _normalize_dob(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"\b(19|20)\s+(\d{2})(?=\D*$)", r"\1\2", value)
    cleaned = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", cleaned, flags=re.IGNORECASE)
    for fmt in ("%B %d %Y", "%b %d %Y", "%m %d %Y", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(cleaned.strip(" .,"), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _normalize_zip(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    return digits if len(digits) == 5 else None


def _selected_text(answer, candidates: dict[str, str]) -> str | None:
    return None if answer.choice == "none" else candidates.get(answer.choice)


def _extract_intake_fields(messages: list[dict]) -> IntakeFields:
    """Use TypeSafe to select source responses, then normalize without generation."""
    groups = _response_groups(messages)
    if not groups:
        return IntakeFields()

    candidates = {f"response_{index}": text for index, text in enumerate(groups)}
    criteria = {
        key: {"spoken_response": value}
        for key, value in candidates.items()
    } | {"none": "No candidate response contains this complete field."}
    transcript = [
        {"role": message.get("role"), "content": message.get("content", "")}
        for message in messages
        if message.get("role") in {"assistant", "user"}
    ]

    with TypeSafeClient() as client:
        response = client.system_one(
            state={"transcript": transcript, "candidate_responses": candidates},
            questions={
                "name": Choice(
                    instructions="Which candidate response contains the caller's stated full name?",
                    criteria=criteria,
                ),
                "dob": Choice(
                    instructions=(
                        "Which candidate response contains a complete date of birth, "
                        "including month, day, and year? Choose none for fragments."
                    ),
                    criteria=criteria,
                ),
                "zip_code": Choice(
                    instructions=(
                        "Which candidate response contains the complete five-digit zip code? "
                        "Digits may be spoken separately."
                    ),
                    criteria=criteria,
                ),
                "call_intent": Choice(
                    instructions=(
                        "Based on the caller's latest response, are they ending the call "
                        "because their request is complete, or do they still need help?"
                    ),
                    criteria={
                        "finish": "The caller says that is all, thanks the assistant, or says the problem is solved.",
                        "continue": "The caller provides intake information, asks a question, or still needs help.",
                    },
                ),
                "prescription_status": Choice(
                    instructions=(
                        "Based on the complete transcript, has the assistant already told "
                        "the caller the status of their prescription?"
                    ),
                    criteria={
                        "shared": "The assistant has already communicated the prescription status to the caller.",
                        "not_shared": "The assistant has not yet communicated the prescription status to the caller.",
                    },
                ),
            },
        )

    return IntakeFields(
        name=_normalize_name(_selected_text(response.choices["name"], candidates)),
        dob=_normalize_dob(_selected_text(response.choices["dob"], candidates)),
        zip_code=_normalize_zip(_selected_text(response.choices["zip_code"], candidates)),
        prescription_was_shared=response.choices["prescription_status"].choice == "shared",
        caller_is_done=response.choices["call_intent"].choice == "finish",
    )


def _resolve_reply_text(messages: list[dict]) -> str:
    """Re-derive intake state from the conversation so far and decide what
    the assistant should say next. Stateless by design: every turn Vapi
    sends the whole message history, so we never need to persist anything
    ourselves between requests.
    """
    if not any(m.get("role") == "user" and str(m.get("content", "")).strip() for m in messages):
        return "Thanks for calling CVS Pharmacy. What is your full name, please?"

    fields = _extract_intake_fields(messages)
    for field, prompt in _INTAKE_PROMPTS:
        if getattr(fields, field):
            continue
        # Stateless retry cap: count how many times we've already asked this
        # exact question in the transcript so far (mirrors MAX_RETRIES used
        # by the outbound TTS self-correct loop). Caller with persistently
        # unrecognizable speech (e.g. STT can't parse an unusual name) gets
        # escalated instead of being asked forever.
        times_asked = sum(
            1 for m in messages if m.get("role") == "assistant" and m.get("content") == prompt
        )
        if times_asked > MAX_RETRIES:
            return _ESCALATION_MESSAGE
        return prompt

    first_name = fields.name.split()[0]
    if fields.prescription_was_shared:
        if fields.caller_is_done:
            return f"You're very welcome, {first_name}. Have a great day. Goodbye."
        return f"Of course, {first_name}. What else can I help you with?"

    return (
        f"Thanks, {first_name}. I found your prescription. "
        "Your Farxiga is ready for pickup. Is there anything else I can help you with?"
    )


@app.post("/llm/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    stream = bool(body.get("stream", False))
    model_name = body.get("model", "pharmacy-assistant")

    reply_text = _resolve_reply_text(messages)
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    if not stream:
        return JSONResponse(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": model_name,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": reply_text},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    def sse_chunks():
        content_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": reply_text}, "finish_reason": None}],
        }
        stop_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        import json

        yield f"data: {json.dumps(content_chunk)}\n\n"
        yield f"data: {json.dumps(stop_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse_chunks(), media_type="text/event-stream")


@app.post("/tts/synthesize")
async def synthesize(request: Request):
    global _tts_text_sequence

    body = await request.json()
    message = body.get("message", {})
    text = message.get("text", "")
    sample_rate = message.get("sampleRate", 24000)

    rendered, used_ssml = build_ssml(text, _correction_table)
    audio = synthesize_speech_pcm(rendered, used_ssml, sample_rate)
    if text and (not _tts_text_events or _tts_text_events[-1]["text"] != text):
        _tts_text_sequence += 1
        duration_ms = max(0, round((len(audio) - 44) / (sample_rate * 2) * 1000))
        _tts_text_events.append(
            {
                "id": _tts_text_sequence,
                "timestamp": int(time.time() * 1000),
                "text": text,
                "duration_ms": duration_ms,
            }
        )
        del _tts_text_events[:-100]
    return Response(content=audio, media_type="application/octet-stream")


@app.get("/demo/tts-text")
async def demo_tts_text(request: Request):
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return JSONResponse(
        {"events": _tts_text_events},
        headers={"Access-Control-Allow-Origin": "http://127.0.0.1:8765"},
    )
