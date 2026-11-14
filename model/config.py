"""Runtime settings contain no customer-specific defaults."""

from dataclasses import dataclass
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    database_path: Path
    catalog_path: Path
    retrieval_backend: str
    model: str
    specialist_model: str
    debounce_seconds: float

    @classmethod
    def from_environment(cls):
        return cls(
            Path(os.getenv("CRM_DATABASE_PATH", str(ROOT / "var/conversations.sqlite3"))),
            Path(os.getenv("CRM_CATALOG_PATH", str(ROOT / "bases/database.md"))),
            os.getenv("CRM_RETRIEVAL_BACKEND", "tfidf"),
            os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            os.getenv("OPENAI_SPECIALIST_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1-mini")),
            float(os.getenv("CRM_DEBOUNCE_SECONDS", "0.1")),
        )


SPECIALISTS = {
    "Needs_Identification_Specialist": "Identify one missing requirement and suggest one clarifying question.",
    "Objection_Handler": "Address stated concerns using supplied catalog evidence; acknowledge missing facts.",
    "Presentation_Specialist": "Explain relevant catalog options using only supplied evidence.",
    "Call_Schedule": "Offer human follow-up only with explicit consent. Never invent availability or contact details.",
    "Closure_Specialist": "Conclude the exchange concisely without creating promises or new commitments.",
}
EXTRACTORS = {
    "needs": "Identify expressed customer requirements.",
    "benefits": "Identify catalog benefits already explained in the dialogue.",
    "objections": "Identify concerns explicitly raised by the customer.",
    "resolved_objections": "Identify concerns explicitly addressed in prior replies.",
    "commercial_details": "Extract stated course, schedule and commercial constraints without inventing terms.",
}
