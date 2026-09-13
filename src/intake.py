"""Patient intake: extract identity fields from the caller's utterance,
match against the patient DB, and use TypeSafe as a confidence gate before
proceeding -- ambiguous/partial matches get sent back for clarification
instead of silently guessing which patient record to use.
"""
import json
import os
from dataclasses import dataclass

import weave
from google import genai

from src.config import GOOGLE_CLOUD_LOCATION, GOOGLE_CLOUD_PROJECT, LLM_MODEL, PATIENTS_DB_PATH

try:
    from typesafe_sdk import Choice, TypeSafeClient
except ImportError:  # pragma: no cover
    TypeSafeClient = None

_client = genai.Client(vertexai=True, project=GOOGLE_CLOUD_PROJECT, location=GOOGLE_CLOUD_LOCATION)

_EXTRACTION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "name": {"type": "STRING", "description": "Caller's full name, or null if not stated"},
        "dob": {"type": "STRING", "description": "YYYY-MM-DD, or null if not stated"},
        "zip_code": {"type": "STRING", "description": "5-digit zip, or null if not stated"},
        "doctor_name": {"type": "STRING", "description": "e.g. 'Dr. Patel', or null if not stated"},
    },
}


@dataclass
class PatientQuery:
    name: str | None
    dob: str | None
    zip_code: str | None
    doctor_name: str | None

    def merge(self, other: "PatientQuery") -> "PatientQuery":
        """Fill in still-empty slots from a newly-extracted utterance."""
        return PatientQuery(
            name=self.name or other.name,
            dob=self.dob or other.dob,
            zip_code=self.zip_code or other.zip_code,
            doctor_name=self.doctor_name or other.doctor_name,
        )


# Fixed order the agent asks for identity slots -- one question per turn,
# like a real IVR, instead of expecting the caller to volunteer everything.
INTAKE_PROMPTS = [
    ("name", "Can I get your full name, please?"),
    ("dob", "Thanks. What is your date of birth?"),
    ("zip_code", "And what is the zip code on your account?"),
]


def next_intake_prompt(query: PatientQuery) -> str | None:
    """Return the next unanswered identity question, or None once name, dob,
    and zip code have all been collected.
    """
    for field, prompt in INTAKE_PROMPTS:
        if not getattr(query, field):
            return prompt
    return None


def load_patients(path: str = PATIENTS_DB_PATH) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@weave.op()
def extract_patient_query(utterance: str) -> PatientQuery:
    """Pull identity slots out of the caller's spoken utterance."""
    response = _client.models.generate_content(
        model=LLM_MODEL,
        contents=utterance,
        config={"response_mime_type": "application/json", "response_schema": _EXTRACTION_SCHEMA},
    )
    fields = json.loads(response.text)

    def _clean(value) -> str | None:
        # Gemini's structured output sometimes emits the literal string
        # "null" (not a JSON null) for fields it couldn't find -- treat
        # that the same as actually missing.
        if not value or str(value).strip().lower() == "null":
            return None
        return value

    return PatientQuery(
        name=_clean(fields.get("name")),
        dob=_clean(fields.get("dob")),
        zip_code=_clean(fields.get("zip_code")),
        doctor_name=_clean(fields.get("doctor_name")),
    )


def find_candidate_records(query: PatientQuery, patients: list[dict]) -> list[dict]:
    """Name match is required; other fields narrow further but aren't required."""
    if not query.name:
        return []
    candidates = [p for p in patients if p["name"].lower() == query.name.lower()]
    for field in ("dob", "zip_code", "doctor_name"):
        value = getattr(query, field)
        if value and len(candidates) > 1:
            narrowed = [p for p in candidates if str(p.get(field, "")).lower() == str(value).lower()]
            if narrowed:
                candidates = narrowed
    return candidates


@weave.op()
def verify_patient_match(query: PatientQuery, candidates: list[dict]) -> dict | None:
    """Confirm a single confident match, or return None if the identity is
    ambiguous/underspecified and the caller needs to provide more detail.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        # Name alone already uniquely identifies a record -- no ambiguity to
        # resolve, so there's nothing for the judge to decide. Only reach for
        # TypeSafe below when multiple same-named candidates need disambiguation.
        return candidates[0]
    if TypeSafeClient is None:
        return None  # ambiguous and no judge available -- ask the caller for more detail

    with TypeSafeClient() as client:
        response = client.system_one(
            state={
                "caller_provided": {
                    "name": query.name,
                    "dob": query.dob,
                    "zip_code": query.zip_code,
                    "doctor_name": query.doctor_name,
                },
                "candidate_records": candidates,
            },
            questions={
                "match": Choice(
                    instructions=(
                        "A caller identified themselves with `caller_provided` fields. "
                        "`candidate_records` lists patient record(s) whose name matches. "
                        "Decide whether the caller-provided fields confidently and "
                        "uniquely identify exactly one of the candidate records, or "
                        "whether more information is needed before proceeding "
                        "(e.g. two candidates share a name and the caller didn't give "
                        "enough distinguishing detail like DOB or doctor)."
                    ),
                    criteria={
                        "confirmed": "Exactly one candidate is confidently identified by the provided fields.",
                        "needs_clarification": "Ambiguous, or too little information to safely pick one record.",
                    },
                ),
            },
        )
    if response.choices["match"].choice != "confirmed":
        return None
    # Re-derive which single candidate matched best now that we know it's confirmed.
    for field in ("dob", "zip_code", "doctor_name"):
        value = getattr(query, field)
        if value:
            narrowed = [p for p in candidates if str(p.get(field, "")).lower() == str(value).lower()]
            if len(narrowed) == 1:
                return narrowed[0]
    return candidates[0]
