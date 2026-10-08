"""Trip validator — deterministic checks for baggage, fare, and FBC consistency.

Compares SMS_HOLD response (what we asked to book) against supplier APIs
(what actually got booked). No LLM — pure data comparison.

Data sources:
  "Expected" (our side):  SMS_HOLD response → holdContext.updatePromise.fareDetails
  "Actual" (supplier):    SUPPLIER_PRICE_QUOTE response (SOAP), SUPPLIER_BOOK response (SOAP)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lxml import etree

from oncall_rca.schemas.trip_index import CallNode, Journey, TripIndex
from oncall_rca.stages.evidence.call_tree import build_call_tree, detect_supplier, split_journeys
from oncall_rca.stages.evidence.index_normaliser import normalise_trip_index
from oncall_rca.tools.cache import TripCache
from oncall_rca.tools.log_api import detect_format, parse_payload

_OTA_NS = "http://www.opentravel.org/OTA/2003/05"


@dataclass
class ValidationCheck:
    """A single validation check result."""

    name: str
    status: str  # "pass", "fail", "skip"
    expected: str
    actual: str
    source_expected: str  # file name
    source_actual: str  # file name
    detail: str = ""


@dataclass
class JourneyValidation:
    """Validation results for one journey."""

    journey_index: int
    route: str
    supplier: str
    checks: list[ValidationCheck] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.status == "pass")

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if c.status == "fail")


@dataclass
class ValidationReport:
    """Full trip validation report."""

    trip_id: str
    journeys: list[JourneyValidation] = field(default_factory=list)

    @property
    def total_passed(self) -> int:
        return sum(j.passed for j in self.journeys)

    @property
    def total_failed(self) -> int:
        return sum(j.failed for j in self.journeys)

    @property
    def has_issues(self) -> bool:
        return self.total_failed > 0


def validate_trip(
    trip_id: str,
    raw_data: dict[str, Any],
    cache: TripCache,
) -> ValidationReport:
    """Run all validation checks on a trip.

    Args:
        trip_id: Trip reference.
        raw_data: Raw trip index data.
        cache: File cache for payloads.

    Returns:
        ValidationReport with per-journey checks.
    """
    # Normalise and split
    trip_index = normalise_trip_index(raw_data)
    trip_index = build_call_tree(trip_index)
    trip_index = split_journeys(trip_index)
    trip_index = detect_supplier(trip_index)

    report = ValidationReport(trip_id=trip_id)

    for journey in trip_index.journeys:
        jv = _validate_journey(journey, trip_id, cache)
        report.journeys.append(jv)

    return report


def _validate_journey(
    journey: Journey,
    trip_id: str,
    cache: TripCache,
) -> JourneyValidation:
    """Run checks for one journey."""
    route = f"{journey.origin}→{journey.destination}" if journey.origin else "unknown"
    jv = JourneyValidation(
        journey_index=journey.journey_index,
        route=route,
        supplier=journey.supplier,
    )

    calls = journey.calls or []

    # Load SMS_HOLD response
    sms_hold = _find_and_parse(calls, "SMS_HOLD", "res", trip_id, cache)
    # Load SUPPLIER_PRICE_QUOTE response
    supplier_pq = _find_and_parse(calls, "SUPPLIER_PRICE_QUOTE", "res", trip_id, cache)
    # Load SUPPLIER_BOOK response
    supplier_book = _find_and_parse(calls, "SUPPLIER_BOOK", "res", trip_id, cache)

    sms_hold_file = _find_file_name(calls, "SMS_HOLD", "res")
    pq_file = _find_file_name(calls, "SUPPLIER_PRICE_QUOTE", "res")
    sb_file = _find_file_name(calls, "SUPPLIER_BOOK", "res")

    # ── Check 1: Hold price vs Supplier price quote ──────────────
    if sms_hold and supplier_pq:
        jv.checks.append(_check_hold_vs_supplier_price(
            sms_hold, supplier_pq, sms_hold_file, pq_file,
        ))
    else:
        jv.checks.append(ValidationCheck(
            name="Hold price vs Supplier price",
            status="skip",
            expected="", actual="",
            source_expected=sms_hold_file, source_actual=pq_file,
            detail="SMS_HOLD or SUPPLIER_PRICE_QUOTE not available",
        ))

    # ── Check 2: Booked fare (SMS_HOLD vs SUPPLIER_BOOK) ────────
    if sms_hold and supplier_book:
        jv.checks.append(_check_booked_fare(
            sms_hold, supplier_book, sms_hold_file, sb_file,
        ))
    else:
        jv.checks.append(ValidationCheck(
            name="Booked fare consistency",
            status="skip",
            expected="", actual="",
            source_expected=sms_hold_file, source_actual=sb_file,
            detail="SMS_HOLD or SUPPLIER_BOOK not available",
        ))

    # ── Check 3: FBC consistency ─────────────────────────────────
    if sms_hold and supplier_book:
        jv.checks.append(_check_fbc(
            sms_hold, supplier_book, sms_hold_file, sb_file,
        ))
    else:
        jv.checks.append(ValidationCheck(
            name="Fare basis code match",
            status="skip",
            expected="", actual="",
            source_expected=sms_hold_file, source_actual=sb_file,
            detail="SMS_HOLD or SUPPLIER_BOOK not available",
        ))

    # ── Check 4: Baggage consistency ─────────────────────────────
    if sms_hold and supplier_book:
        jv.checks.append(_check_baggage(
            sms_hold, supplier_book, sms_hold_file, sb_file,
        ))
    else:
        jv.checks.append(ValidationCheck(
            name="Baggage consistency",
            status="skip",
            expected="", actual="",
            source_expected=sms_hold_file, source_actual=sb_file,
            detail="SMS_HOLD or SUPPLIER_BOOK not available",
        ))

    # ── Check 5: Hold status ────────────────────────────────────
    if sms_hold:
        jv.checks.append(_check_hold_status(sms_hold, sms_hold_file))

    # ── Check 6: Booking status ──────────────────────────────────
    if supplier_book is not None:
        jv.checks.append(_check_booking_status(supplier_book, sb_file))

    return jv


# ── Individual checks ───────────────────────────────────────────────


def _check_hold_vs_supplier_price(
    sms_hold: dict, supplier_pq: Any, hold_file: str, pq_file: str,
) -> ValidationCheck:
    """Compare SMS_HOLD fare total with SUPPLIER_PRICE_QUOTE fare total."""
    hold_fare = _get_sms_hold_fare(sms_hold)
    pq_fare = _get_supplier_pq_fare(supplier_pq)

    if hold_fare and pq_fare:
        match = abs(float(hold_fare) - float(pq_fare)) < 0.01
        return ValidationCheck(
            name="Hold price vs Supplier price",
            status="pass" if match else "fail",
            expected=f"{hold_fare} (SMS_HOLD)",
            actual=f"{pq_fare} (SUPPLIER_PRICE_QUOTE)",
            source_expected=hold_file,
            source_actual=pq_file,
            detail="" if match else f"Price mismatch: hold={hold_fare}, supplier={pq_fare}",
        )

    return ValidationCheck(
        name="Hold price vs Supplier price",
        status="skip",
        expected=hold_fare or "not found",
        actual=pq_fare or "not found",
        source_expected=hold_file, source_actual=pq_file,
        detail="Could not extract fare from one or both sources",
    )


def _check_booked_fare(
    sms_hold: dict, supplier_book: Any, hold_file: str, sb_file: str,
) -> ValidationCheck:
    """Compare SMS_HOLD fare with SUPPLIER_BOOK fare."""
    hold_fare = _get_sms_hold_fare(sms_hold)
    book_fare = _get_supplier_book_fare(supplier_book)

    if hold_fare and book_fare:
        match = abs(float(hold_fare) - float(book_fare)) < 0.01
        return ValidationCheck(
            name="Booked fare consistency",
            status="pass" if match else "fail",
            expected=f"{hold_fare} (SMS_HOLD)",
            actual=f"{book_fare} (SUPPLIER_BOOK)",
            source_expected=hold_file,
            source_actual=sb_file,
            detail="" if match else f"Fare changed: hold={hold_fare}, booked={book_fare}",
        )

    return ValidationCheck(
        name="Booked fare consistency",
        status="skip",
        expected=hold_fare or "not found",
        actual=book_fare or "not found",
        source_expected=hold_file, source_actual=sb_file,
    )


def _check_fbc(
    sms_hold: dict, supplier_book: Any, hold_file: str, sb_file: str,
) -> ValidationCheck:
    """Compare fare basis code between SMS_HOLD and SUPPLIER_BOOK."""
    hold_fbc = _get_sms_hold_fbc(sms_hold)
    book_fbc = _get_supplier_book_fbc(supplier_book)

    if hold_fbc and book_fbc:
        match = hold_fbc == book_fbc
        return ValidationCheck(
            name="Fare basis code match",
            status="pass" if match else "fail",
            expected=f"{hold_fbc} (SMS_HOLD)",
            actual=f"{book_fbc} (SUPPLIER_BOOK)",
            source_expected=hold_file,
            source_actual=sb_file,
            detail="" if match else f"FBC mismatch: hold={hold_fbc}, booked={book_fbc}. Wrong fare class booked!",
        )

    return ValidationCheck(
        name="Fare basis code match",
        status="skip",
        expected=hold_fbc or "not found",
        actual=book_fbc or "not found",
        source_expected=hold_file, source_actual=sb_file,
    )


def _check_baggage(
    sms_hold: dict, supplier_book: Any, hold_file: str, sb_file: str,
) -> ValidationCheck:
    """Compare baggage between SMS_HOLD inclusions and SUPPLIER_BOOK BaggageRequest."""
    hold_baggage = _get_sms_hold_baggage(sms_hold)
    book_baggage = _get_supplier_book_baggage(supplier_book)

    if hold_baggage is not None and book_baggage:
        # hold_baggage: True/False (has CHECK_IN_BAGGAGE inclusion)
        # book_baggage: list of baggage codes from SUPPLIER_BOOK
        has_booked_baggage = any(b and b != "0" and "0 Kg" not in b for b in book_baggage)

        if hold_baggage and has_booked_baggage:
            status = "pass"
            detail = ""
        elif hold_baggage and not has_booked_baggage:
            status = "fail"
            detail = "BAGGAGE MISMATCH: Hold includes CHECK_IN_BAGGAGE but supplier booking has no baggage"
        elif not hold_baggage and has_booked_baggage:
            status = "pass"  # extra baggage is not a problem
            detail = "Paid baggage added (not in fare inclusions)"
        else:
            status = "pass"
            detail = ""

        return ValidationCheck(
            name="Baggage consistency",
            status=status,
            expected=f"CHECK_IN_BAGGAGE={'included' if hold_baggage else 'not included'} (SMS_HOLD)",
            actual=f"Booked: {', '.join(book_baggage)} (SUPPLIER_BOOK)",
            source_expected=hold_file,
            source_actual=sb_file,
            detail=detail,
        )

    return ValidationCheck(
        name="Baggage consistency",
        status="skip",
        expected=str(hold_baggage) if hold_baggage is not None else "not found",
        actual=str(book_baggage) if book_baggage else "not found",
        source_expected=hold_file, source_actual=sb_file,
    )


def _check_hold_status(sms_hold: dict, hold_file: str) -> ValidationCheck:
    """Check SMS_HOLD response status."""
    hr = sms_hold.get("holdResponse", {})
    desc = hr.get("description", "") if isinstance(hr, dict) else ""
    success = hr.get("success", False) if isinstance(hr, dict) else False

    return ValidationCheck(
        name="Hold status",
        status="pass" if success else "fail",
        expected="HOLD_SUCCESS",
        actual=desc,
        source_expected=hold_file,
        source_actual=hold_file,
        detail="" if success else f"Hold failed: {desc}",
    )


def _check_booking_status(supplier_book: Any, sb_file: str) -> ValidationCheck:
    """Check SUPPLIER_BOOK response for success."""
    has_success = False
    has_error = False
    error_msg = ""

    for el in supplier_book.iter(f"{{{_OTA_NS}}}Success"):
        has_success = True
        break
    for el in supplier_book.iter(f"{{{_OTA_NS}}}Error"):
        has_error = True
        error_msg = el.get("Code", "") + ": " + (el.text or el.get("ShortText", ""))
        break

    if has_success and not has_error:
        return ValidationCheck(
            name="Booking status",
            status="pass",
            expected="Success",
            actual="Success",
            source_expected=sb_file, source_actual=sb_file,
        )

    return ValidationCheck(
        name="Booking status",
        status="fail",
        expected="Success",
        actual=error_msg or "No <Success/> found",
        source_expected=sb_file, source_actual=sb_file,
        detail=f"Supplier booking failed: {error_msg}" if has_error else "Missing success indicator",
    )


# ── Field extraction helpers ────────────────────────────────────────


def _get_sms_hold_fare(sms_hold: dict) -> str:
    """Extract total fare from SMS_HOLD response."""
    try:
        tfs = sms_hold["holdContext"]["updatePromise"]["fareDetails"]["tripFareSummary"]
        return str(tfs["totalAmount"])
    except (KeyError, TypeError):
        # Also check holdResponse.updatedPromise path
        try:
            up = sms_hold["holdResponse"]["updatedPromise"]
            tfs = up["fareDetails"]["tripFareSummary"]
            return str(tfs["totalAmount"])
        except (KeyError, TypeError):
            return ""


def _get_sms_hold_fbc(sms_hold: dict) -> str:
    """Extract fare basis code from SMS_HOLD response."""
    try:
        jfs = sms_hold["holdContext"]["updatePromise"]["fareDetails"]["journeyFareSummary"]
        if isinstance(jfs, list) and jfs:
            sf = jfs[0].get("segmentFares", [])
            if sf:
                ptf = sf[0].get("passengerTypeFareBasisDetailsList", [])
                if ptf:
                    return ptf[0]["fareBasisDetails"]["fareBasisCode"]
    except (KeyError, TypeError, IndexError):
        pass
    return ""


def _get_sms_hold_baggage(sms_hold: dict) -> bool | None:
    """Check if SMS_HOLD fare includes CHECK_IN_BAGGAGE."""
    try:
        jfs = sms_hold["holdContext"]["updatePromise"]["fareDetails"]["journeyFareSummary"]
        if isinstance(jfs, list) and jfs:
            sf = jfs[0].get("segmentFares", [])
            if sf:
                inclusions = sf[0].get("inclusions", [])
                return "CHECK_IN_BAGGAGE" in inclusions
    except (KeyError, TypeError, IndexError):
        pass
    return None


def _get_supplier_pq_fare(supplier_pq: Any) -> str:
    """Extract total fare from SUPPLIER_PRICE_QUOTE SOAP response."""
    try:
        for tf in supplier_pq.iter(f"{{{_OTA_NS}}}TotalFare"):
            return tf.get("Amount", "")
    except Exception:
        pass
    return ""


def _get_supplier_book_fare(supplier_book: Any) -> str:
    """Extract total fare from SUPPLIER_BOOK SOAP response."""
    try:
        for tf in supplier_book.iter(f"{{{_OTA_NS}}}TotalFare"):
            return tf.get("Amount", "")
    except Exception:
        pass
    return ""


def _get_supplier_book_fbc(supplier_book: Any) -> str:
    """Extract FBC from SUPPLIER_BOOK SOAP response."""
    try:
        for fbc in supplier_book.iter(f"{{{_OTA_NS}}}FareBasisCode"):
            if fbc.text:
                return fbc.text
    except Exception:
        pass
    return ""


def _get_supplier_book_baggage(supplier_book: Any) -> list[str]:
    """Extract baggage codes from SUPPLIER_BOOK SOAP response."""
    codes: list[str] = []
    try:
        for br in supplier_book.iter(f"{{{_OTA_NS}}}BaggageRequest"):
            code = br.get("baggageCode", "")
            if code:
                codes.append(code)
    except Exception:
        pass
    return codes


# ── Utility ─────────────────────────────────────────────────────────


def _find_and_parse(
    calls: list[CallNode], api: str, req_or_res: str, trip_id: str, cache: TripCache,
) -> Any | None:
    """Find a call by API name, load and parse its payload from cache."""
    file_name = _find_file_name(calls, api, req_or_res)
    if not file_name:
        return None
    if not cache.has_file(trip_id, file_name):
        return None
    try:
        content = cache.read_file(trip_id, file_name)
        return parse_payload(content)
    except Exception:
        return None


def _find_file_name(calls: list[CallNode], api: str, req_or_res: str) -> str:
    """Find the file name for a specific API call."""
    # Find the last successful call (skip retries — use last one)
    target_call = None
    for call in calls:
        if call.api == api:
            target_call = call

    if target_call is None:
        return ""

    ref = target_call.res_file if req_or_res == "res" else target_call.req_file
    return ref.file_name if ref else ""


def format_report(report: ValidationReport) -> str:
    """Format validation report for terminal output."""
    lines: list[str] = []
    lines.append(f"Trip {report.trip_id} — Validation Report\n")

    for jv in report.journeys:
        lines.append(f"Journey {jv.journey_index}: {jv.route} ({jv.supplier})")
        for check in jv.checks:
            icon = {"pass": "✅", "fail": "❌", "skip": "⏭️"}[check.status]
            lines.append(f"  {icon} {check.name}")
            if check.status != "skip":
                lines.append(f"     Expected: {check.expected}")
                lines.append(f"     Actual:   {check.actual}")
            if check.detail:
                lines.append(f"     → {check.detail}")
        lines.append(f"  Result: {jv.passed} passed, {jv.failed} failed\n")

    summary = "ISSUES DETECTED" if report.has_issues else "ALL CHECKS PASSED"
    lines.append(f"Summary: {report.total_passed} passed, {report.total_failed} failed — {summary}")
    return "\n".join(lines)
