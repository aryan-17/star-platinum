"""PDF mail intake — extract incident data from exported Gmail PDF.

Handles the format: Gmail print/export as PDF containing email thread.
Extracts trip ref from subject, symptom from body, classifies incident type.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from oncall_rca.schemas.incident import Incident, IncidentType

# Trip ref: 12-13 digit number in subject line
_TRIP_REF_PATTERN = re.compile(r"\b(\d{12,13})\b")

# Incident keywords for classification
_BAGGAGE_KEYWORDS = [
    "baggage", "luggage", "checked bag", "cabin bag", "kg",
    "piece", "allowance", "no baggage", "baggage mismatch",
    "paid at airport", "denied baggage",
]


def extract_from_pdf(pdf_path: Path) -> Incident:
    """Extract incident data from a Gmail PDF export.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Incident with trip ref, type, and symptom extracted.

    Raises:
        ValueError: If trip ref cannot be found.
    """
    text = _read_pdf_text(pdf_path)
    trip_ref = _extract_trip_ref(text, pdf_path.name)
    incident_type = _classify_incident(text)
    symptom = _extract_symptom(text)
    subject = _extract_subject(text, pdf_path.name)

    return Incident(
        message_id=f"pdf:{pdf_path.name}",
        thread_id=f"pdf:{pdf_path.name}",
        trip_ref=trip_ref,
        incident_type=incident_type,
        reported_symptom=symptom,
        received_at=datetime.now(),
    )


def _read_pdf_text(pdf_path: Path) -> str:
    """Extract text from PDF using pymupdf."""
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    text_parts: list[str] = []
    for page in doc:
        text_parts.append(page.get_text())
    doc.close()
    return "\n".join(text_parts)


def _extract_trip_ref(text: str, filename: str) -> str:
    """Extract trip reference (12-13 digit number) from subject or filename.

    Checks filename first (often contains the trip ref), then scans text.
    """
    # Try filename first
    match = _TRIP_REF_PATTERN.search(filename)
    if match:
        return match.group(1)

    # Scan first few lines (subject area)
    for line in text.split("\n")[:20]:
        match = _TRIP_REF_PATTERN.search(line)
        if match:
            return match.group(1)

    # Full text scan
    match = _TRIP_REF_PATTERN.search(text)
    if match:
        return match.group(1)

    raise ValueError(f"No trip reference (12-13 digit number) found in {filename}")


def _classify_incident(text: str) -> IncidentType:
    """Classify incident type from mail body keywords."""
    text_lower = text.lower()
    baggage_score = sum(1 for kw in _BAGGAGE_KEYWORDS if kw in text_lower)

    if baggage_score >= 2:
        return IncidentType.BAGGAGE_MISMATCH

    return IncidentType.UNKNOWN


def _extract_symptom(text: str) -> str:
    """Extract the core symptom description from the mail thread.

    Looks for the first substantive message (not reminders, not signatures).
    """
    lines = text.split("\n")
    symptom_lines: list[str] = []
    in_body = False

    for line in lines:
        stripped = line.strip()

        # Skip empty, headers, signatures, quoted text markers
        if not stripped:
            if in_body and symptom_lines:
                break  # end of first paragraph
            continue
        if stripped.startswith(("Regards", "----", "[Quoted", "To unsubscribe", "You received")):
            if symptom_lines:
                break
            continue
        if stripped.startswith(("Dear", "Dears", "Hi ", "Hello")):
            in_body = True
            continue
        if stripped.startswith(("Reminder", "+air-supplier", "+Karim", "+Hussien", "@")):
            continue

        # Skip email metadata lines
        if any(x in stripped for x in ["@flyin.com>", "wrote:", "Cc:", "To:", "From:", "Reply-To:"]):
            continue

        if in_body or any(kw in stripped.lower() for kw in _BAGGAGE_KEYWORDS):
            in_body = True
            symptom_lines.append(stripped)

    if symptom_lines:
        return " ".join(symptom_lines)[:500]

    # Fallback: return first 200 chars of text
    return text[:200].replace("\n", " ").strip()


def _extract_subject(text: str, filename: str) -> str:
    """Extract subject line from PDF text or filename."""
    # Gmail PDF format: "Re: <trip_ref>" appears early
    for line in text.split("\n")[:5]:
        if "Re:" in line or "Fwd:" in line:
            return line.strip()
    # Fallback from filename
    name = filename.replace(".pdf", "").replace("Flyin.com Mail - ", "")
    return name
