"""Trip validator — deterministic checks for baggage, fare, and FBC consistency.

Compares SMS_HOLD response (what we asked to book) against booking response
(what actually got booked). No LLM — pure data comparison.

Data sources:
  "Expected" (our side):  SMS_HOLD response → fareDetails (JSON)
  "Actual" (booked):      BOOK-BOOK response → body.fareDetails (JSON, generic)
                          or SUPPLIER_BOOK response (OTA SOAP, Air Arabia)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from oncall_rca.schemas.trip_index import CallNode, Journey
from oncall_rca.stages.evidence.call_tree import build_call_tree, detect_supplier, split_journeys
from oncall_rca.stages.evidence.index_normaliser import normalise_trip_index
from oncall_rca.tools.cache import TripCache
from oncall_rca.tools.log_api import parse_payload

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

    # ── Load hold response (try SMS_HOLD then HOLD_CORE) ─────
    sms_hold = _find_and_parse(calls, "SMS_HOLD", "res", trip_id, cache)
    sms_hold_file = _find_file_name(calls, "SMS_HOLD", "res")
    if sms_hold is None:
        sms_hold = _find_and_parse(calls, "HOLD_CORE", "res", trip_id, cache)
        sms_hold_file = _find_file_name(calls, "HOLD_CORE", "res")

    # ── Load book response: try BOOK-BOOK (JSON, generic) first ──
    book_json = _find_and_parse_pair(calls, "BOOK", "BOOK", "res", trip_id, cache)
    book_json_file = _find_file_name_pair(calls, "BOOK", "BOOK", "res")
    # Fall back to SUPPLIER_BOOK (SOAP, Air Arabia)
    supplier_book = _find_and_parse(calls, "SUPPLIER_BOOK", "res", trip_id, cache)
    sb_file = _find_file_name(calls, "SUPPLIER_BOOK", "res")

    book_file = book_json_file or sb_file

    # ── Load SUPPLIER_PRICE_QUOTE (SOAP, optional) ─────────────
    supplier_pq = _find_and_parse(calls, "SUPPLIER_PRICE_QUOTE", "res", trip_id, cache)
    pq_file = _find_file_name(calls, "SUPPLIER_PRICE_QUOTE", "res")

    # ── Check 1: Hold price vs Supplier price quote ──────────────
    if sms_hold and supplier_pq:
        jv.checks.append(_check_hold_vs_supplier_price(
            sms_hold, supplier_pq, sms_hold_file, pq_file,
        ))
    elif sms_hold and book_json:
        # No SUPPLIER_PRICE_QUOTE — compare hold vs book fare directly
        jv.checks.append(_check_fare_json(
            sms_hold, book_json, sms_hold_file, book_json_file,
            "Hold price vs Book price",
        ))
    else:
        jv.checks.append(ValidationCheck(
            name="Hold price vs Supplier price",
            status="skip",
            expected="", actual="",
            source_expected=sms_hold_file, source_actual=pq_file or book_file,
            detail="SMS_HOLD or pricing source not available",
        ))

    # ── Check 2: Booked fare consistency ───────────────────────
    if sms_hold and book_json:
        jv.checks.append(_check_fare_json(
            sms_hold, book_json, sms_hold_file, book_json_file,
            "Booked fare consistency",
        ))
    elif sms_hold and supplier_book:
        jv.checks.append(_check_booked_fare(
            sms_hold, supplier_book, sms_hold_file, sb_file,
        ))
    else:
        jv.checks.append(ValidationCheck(
            name="Booked fare consistency",
            status="skip",
            expected="", actual="",
            source_expected=sms_hold_file, source_actual=book_file,
            detail="SMS_HOLD or booking response not available",
        ))

    # ── Check 3: FBC consistency ─────────────────────────────────
    if sms_hold and book_json:
        jv.checks.append(_check_fbc_json(
            sms_hold, book_json, sms_hold_file, book_json_file,
        ))
    elif sms_hold and supplier_book:
        jv.checks.append(_check_fbc(
            sms_hold, supplier_book, sms_hold_file, sb_file,
        ))
    else:
        jv.checks.append(ValidationCheck(
            name="Fare basis code match",
            status="skip",
            expected="", actual="",
            source_expected=sms_hold_file, source_actual=book_file,
            detail="SMS_HOLD or booking response not available",
        ))

    # ── Check 4: Baggage consistency ─────────────────────────────
    if sms_hold and book_json:
        jv.checks.append(_check_baggage_json(
            sms_hold, book_json, sms_hold_file, book_json_file,
        ))
    elif sms_hold and supplier_book:
        jv.checks.append(_check_baggage(
            sms_hold, supplier_book, sms_hold_file, sb_file,
        ))
    else:
        jv.checks.append(ValidationCheck(
            name="Baggage consistency",
            status="skip",
            expected="", actual="",
            source_expected=sms_hold_file, source_actual=book_file,
            detail="SMS_HOLD or booking response not available",
        ))

    # ── Check 5: SS1 display vs booked baggage ────────────────────
    ss1 = _find_and_parse(
        calls, "SINGLE_SOLUTION_SEARCH", "res", trip_id, cache,
    )
    ss1_file = _find_file_name(
        calls, "SINGLE_SOLUTION_SEARCH", "res",
    )
    hold_fbc = _get_json_fbc(sms_hold) if sms_hold else ""
    if ss1 and hold_fbc:
        jv.checks.append(_check_ss1_vs_hold_baggage(
            ss1, sms_hold, hold_fbc, ss1_file, sms_hold_file,
        ))
    else:
        jv.checks.append(ValidationCheck(
            name="Display vs booked baggage",
            status="skip",
            expected="", actual="",
            source_expected=ss1_file, source_actual=sms_hold_file,
            detail="SS1 or hold FBC not available",
        ))

    # ── Check 6: GDS PNR baggage vs hold ──────────────────────────
    pnr = _find_last_pnr_retrieve(calls, trip_id, cache)
    pnr_file = _find_last_pnr_file(calls)
    if pnr is not None and sms_hold:
        jv.checks.append(_check_gds_vs_hold_baggage(
            pnr, sms_hold, pnr_file, sms_hold_file,
        ))
    else:
        jv.checks.append(ValidationCheck(
            name="GDS vs hold baggage",
            status="skip",
            expected="", actual="",
            source_expected=pnr_file, source_actual=sms_hold_file,
            detail="PNR_RETRIEVE or hold not available",
        ))

    # ── Check 7: Hold status ────────────────────────────────────
    if sms_hold:
        jv.checks.append(_check_hold_status(sms_hold, sms_hold_file))

    # ── Check 8: Booking status ──────────────────────────────────
    if book_json is not None:
        jv.checks.append(_check_book_json_status(book_json, book_json_file))
    elif supplier_book is not None:
        jv.checks.append(_check_booking_status(supplier_book, sb_file))

    return jv


# ── SS1 display vs booking check ───────────────────────────────────


def _check_ss1_vs_hold_baggage(
    ss1: dict,
    hold: dict,
    hold_fbc: str,
    ss1_file: str,
    hold_file: str,
) -> ValidationCheck:
    """Compare SS1 displayed baggage with hold/book baggage.

    Matches hold FBC to an SS1 fare family, then checks whether the
    baggage the fare family promised matches what was actually held.
    """
    ss1_bag = _get_ss1_baggage_for_fbc(ss1, hold_fbc)
    hold_has_bag = _get_json_baggage(hold)

    if ss1_bag is None:
        return ValidationCheck(
            name="Display vs booked baggage",
            status="skip",
            expected="", actual="",
            source_expected=ss1_file,
            source_actual=hold_file,
            detail=f"FBC {hold_fbc} not found in SS1 fare families",
        )

    ss1_has_checkin = ss1_bag["has_checkin"]
    ss1_detail = ss1_bag["detail"]

    if hold_has_bag is None:
        hold_desc = "unknown"
    elif hold_has_bag:
        hold_desc = "CHECK_IN_BAGGAGE included"
    else:
        hold_desc = "no CHECK_IN_BAGGAGE"

    if ss1_has_checkin == (hold_has_bag is True):
        return ValidationCheck(
            name="Display vs booked baggage",
            status="pass",
            expected=f"SS1: {ss1_detail} ({hold_fbc})",
            actual=f"Hold: {hold_desc}",
            source_expected=ss1_file,
            source_actual=hold_file,
        )

    return ValidationCheck(
        name="Display vs booked baggage",
        status="fail",
        expected=f"SS1: {ss1_detail} ({hold_fbc})",
        actual=f"Hold: {hold_desc}",
        source_expected=ss1_file,
        source_actual=hold_file,
        detail=(
            f"DISPLAY MISMATCH: SS1 fare family shows "
            f"{ss1_detail} but hold has {hold_desc}"
        ),
    )


def _get_ss1_baggage_for_fbc(
    ss1: Any, target_fbc: str,
) -> dict | None:
    """Find SS1 fare family matching FBC and extract baggage info.

    Handles two SS1 formats:
    - SIS format: {flightSolutions[].fareFamilies[]}
    - SUPPLY_CORE format: [{fareFamilyDTO[]}] (list wrapper)

    Returns dict with 'has_checkin' (bool) and 'detail' (str),
    or None if FBC not found.
    """
    fare_families = _collect_ss1_fare_families(ss1)

    for ff in fare_families:
        combo_fbc = ff.get("comboFbc", "")
        fbcs = [f.strip() for f in combo_fbc.split(",")]
        if target_fbc not in fbcs:
            continue

        # Found matching fare family — check fareBenefits
        for b in ff.get("fareBenefits", []):
            ba = b.get("baggageAllowance", {})
            if ba.get("baggageType") == "BAGGAGE_CHECK_IN":
                info = ba.get("baggageInfoText", "check-in baggage")
                return {"has_checkin": True, "detail": info}

        # Check journeyFareSummarymap (SUPPLY_CORE DTO format)
        jfsm = ff.get("journeyFareSummarymap", {})
        for route_data in jfsm.values():
            bft = route_data.get("baggageFareType", "")
            if bft == "BAGGAGE_FARE":
                # Extract weight from baggageResponseDTOList
                for pfi in route_data.get("paxFareInfoDTOList", []):
                    for br in pfi.get("baggageResponseDTOList", []):
                        if br.get("baggageType") == "BAGGAGE_CHECK_IN":
                            ba = br.get("baggageAllowanceDTO", {})
                            qty = ba.get("qty", 0)
                            unit = ba.get("baggageUnit", "KG")
                            return {
                                "has_checkin": True,
                                "detail": f"{qty} {unit}",
                            }
                return {"has_checkin": True, "detail": bft}

        # Check journeyFareInfo (SIS format)
        jfi = ff.get("journeyFareInfo", {})
        for route_data in jfi.values():
            bft = route_data.get("baggageFareType", "")
            if bft:
                return {
                    "has_checkin": bft == "BAGGAGE_FARE",
                    "detail": bft,
                }

        return {"has_checkin": False, "detail": "NO_BAGGAGE"}

    return None


def _collect_ss1_fare_families(ss1: Any) -> list[dict]:
    """Extract fare families from either SS1 format."""
    families: list[dict] = []

    # Unwrap list wrapper (SUPPLY_CORE format)
    data = ss1
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                families.extend(item.get("fareFamilyDTO", []))
        if families:
            return families
        data = data[0] if data else {}

    if not isinstance(data, dict):
        return []

    # SIS format: flightSolutions[].fareFamilies[]
    for sol in data.get("flightSolutions", []):
        families.extend(sol.get("fareFamilies", []))

    # Direct fareFamilyDTO
    if not families:
        families.extend(data.get("fareFamilyDTO", []))

    return families


# ── GDS PNR check ──────────────────────────────────────────────────


def _find_last_pnr_retrieve(
    calls: list[CallNode], trip_id: str, cache: TripCache,
) -> Any | None:
    """Find and parse the last AMADEUS-PNR_RETRIEVE response (final GDS state)."""
    target = None
    for call in calls:
        if call.api == "AMADEUS-PNR_RETRIEVE":
            target = call
    if target is None:
        return None
    ref = target.res_file
    if ref is None:
        return None
    if not cache.has_file(trip_id, ref.file_name):
        return None
    try:
        content = cache.read_file(trip_id, ref.file_name)
        return parse_payload(content)
    except Exception:
        return None


def _find_last_pnr_file(calls: list[CallNode]) -> str:
    """Get file name for last PNR_RETRIEVE."""
    target = None
    for call in calls:
        if call.api == "AMADEUS-PNR_RETRIEVE":
            target = call
    if target and target.res_file:
        return target.res_file.file_name
    return ""


def _get_pnr_baggage(pnr: Any) -> list[str]:
    """Extract baggageAllowance from Amadeus PNR_RETRIEVE SOAP.

    Returns list of baggage strings like ['25K', '25K'].
    """
    allowances: list[str] = []
    try:
        for el in pnr.iter():
            tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
            if tag == "baggageAllowance" and el.text:
                allowances.append(el.text.strip())
    except Exception:
        pass
    return allowances


def _check_gds_vs_hold_baggage(
    pnr: Any, hold: dict, pnr_file: str, hold_file: str,
) -> ValidationCheck:
    """Compare GDS PNR baggage with hold baggage."""
    gds_bags = _get_pnr_baggage(pnr)
    hold_has_bag = _get_json_baggage(hold)

    if not gds_bags:
        gds_desc = "no baggage in PNR"
        gds_has_bag = False
    else:
        gds_desc = ", ".join(gds_bags)
        gds_has_bag = True

    if hold_has_bag is None:
        hold_desc = "unknown"
    elif hold_has_bag:
        hold_desc = "CHECK_IN_BAGGAGE included"
    else:
        hold_desc = "no CHECK_IN_BAGGAGE"

    match = gds_has_bag == (hold_has_bag is True)

    return ValidationCheck(
        name="GDS vs hold baggage",
        status="pass" if match else "fail",
        expected=f"Hold: {hold_desc}",
        actual=f"GDS PNR: {gds_desc}",
        source_expected=hold_file,
        source_actual=pnr_file,
        detail="" if match else (
            f"GDS MISMATCH: Hold={hold_desc}, "
            f"GDS PNR={gds_desc}"
        ),
    )


# ── JSON checks (generic — work for all suppliers) ─────────────────


def _check_fare_json(
    hold: dict, book: dict, hold_file: str, book_file: str, name: str,
) -> ValidationCheck:
    """Compare fare between two JSON responses using fareDetails."""
    hold_fare = _get_json_fare(hold)
    book_fare = _get_json_fare(book)

    if hold_fare and book_fare:
        match = abs(float(hold_fare) - float(book_fare)) < 0.01
        return ValidationCheck(
            name=name,
            status="pass" if match else "fail",
            expected=f"{hold_fare} (SMS_HOLD)",
            actual=f"{book_fare} (BOOK)",
            source_expected=hold_file,
            source_actual=book_file,
            detail="" if match else f"Fare changed: hold={hold_fare}, booked={book_fare}",
        )

    return ValidationCheck(
        name=name,
        status="skip",
        expected=hold_fare or "not found",
        actual=book_fare or "not found",
        source_expected=hold_file, source_actual=book_file,
        detail="Could not extract fare from one or both sources",
    )


def _check_fbc_json(
    hold: dict, book: dict, hold_file: str, book_file: str,
) -> ValidationCheck:
    """Compare FBC between two JSON responses."""
    hold_fbc = _get_json_fbc(hold)
    book_fbc = _get_json_fbc(book)

    if hold_fbc and book_fbc:
        match = hold_fbc == book_fbc
        return ValidationCheck(
            name="Fare basis code match",
            status="pass" if match else "fail",
            expected=f"{hold_fbc} (SMS_HOLD)",
            actual=f"{book_fbc} (BOOK)",
            source_expected=hold_file,
            source_actual=book_file,
            detail="" if match else (
                f"FBC mismatch: hold={hold_fbc}, booked={book_fbc}"
            ),
        )

    return ValidationCheck(
        name="Fare basis code match",
        status="skip",
        expected=hold_fbc or "not found",
        actual=book_fbc or "not found",
        source_expected=hold_file, source_actual=book_file,
    )


def _check_baggage_json(
    hold: dict, book: dict, hold_file: str, book_file: str,
) -> ValidationCheck:
    """Compare baggage inclusions between two JSON responses."""
    hold_baggage = _get_json_baggage(hold)
    book_baggage = _get_json_baggage(book)

    if hold_baggage is not None and book_baggage is not None:
        if hold_baggage and book_baggage:
            status = "pass"
            detail = ""
        elif hold_baggage and not book_baggage:
            status = "fail"
            detail = "BAGGAGE MISMATCH: Hold includes CHECK_IN_BAGGAGE but booking does not"
        elif not hold_baggage and book_baggage:
            status = "pass"
            detail = "Paid baggage added (not in fare inclusions)"
        else:
            status = "pass"
            detail = ""

        return ValidationCheck(
            name="Baggage consistency",
            status=status,
            expected=f"CHECK_IN_BAGGAGE={'incl' if hold_baggage else 'not incl'} (SMS_HOLD)",
            actual=f"CHECK_IN_BAGGAGE={'incl' if book_baggage else 'not incl'} (BOOK)",
            source_expected=hold_file,
            source_actual=book_file,
            detail=detail,
        )

    return ValidationCheck(
        name="Baggage consistency",
        status="skip",
        expected=str(hold_baggage) if hold_baggage is not None else "not found",
        actual=str(book_baggage) if book_baggage is not None else "not found",
        source_expected=hold_file, source_actual=book_file,
    )


def _check_book_json_status(book: dict, book_file: str) -> ValidationCheck:
    """Check booking status from BOOK-BOOK JSON response."""
    bs = book.get("body", {}).get("bookingStatus", {})
    if not isinstance(bs, dict):
        return ValidationCheck(
            name="Booking status",
            status="skip",
            expected="Success", actual="No bookingStatus found",
            source_expected=book_file, source_actual=book_file,
        )

    successful = bs.get("successful", False)
    message = bs.get("message", "")
    code = bs.get("code", "")

    return ValidationCheck(
        name="Booking status",
        status="pass" if successful else "fail",
        expected="Success",
        actual=f"{code}: {message}",
        source_expected=book_file, source_actual=book_file,
        detail="" if successful else f"Booking failed: {code} {message}",
    )


# ── SOAP checks (Air Arabia / OTA specific) ───────────────────────


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
            detail="" if match else (
                f"FBC mismatch: hold={hold_fbc}, booked={book_fbc}"
            ),
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
            detail = (
                "BAGGAGE MISMATCH: Hold includes CHECK_IN_BAGGAGE "
                "but supplier booking has no baggage"
            )
        elif not hold_baggage and has_booked_baggage:
            status = "pass"  # extra baggage is not a problem
            detail = "Paid baggage added (not in fare inclusions)"
        else:
            status = "pass"
            detail = ""

        return ValidationCheck(
            name="Baggage consistency",
            status=status,
            expected=f"CHECK_IN_BAGGAGE={'incl' if hold_baggage else 'not incl'} (SMS_HOLD)",
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

    for _el in supplier_book.iter(f"{{{_OTA_NS}}}Success"):
        has_success = True
        break
    for err in supplier_book.iter(f"{{{_OTA_NS}}}Error"):
        has_error = True
        error_msg = err.get("Code", "") + ": " + (err.text or err.get("ShortText", ""))
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
        detail=(
            f"Supplier booking failed: {error_msg}"
            if has_error else "Missing success indicator"
        ),
    )


# ── Generic JSON field extraction ──────────────────────────────────


def _get_fare_details(data: dict) -> dict | None:
    """Extract fareDetails from any JSON response.

    Handles paths:
    - holdContext.updatePromise.fareDetails (Air Arabia SMS_HOLD)
    - holdResponse.updatedPromise.fareDetails (Amadeus SMS_HOLD)
    - body.fareDetails (BOOK-BOOK response)
    - fareDetails (direct)
    """
    paths = [
        lambda d: d["holdContext"]["updatePromise"]["fareDetails"],
        lambda d: d["holdResponse"]["updatedPromise"]["fareDetails"],
        lambda d: d["body"]["fareDetails"],
        lambda d: d["fareDetails"],
    ]
    for path in paths:
        try:
            result = path(data)
            if isinstance(result, dict):
                return result
        except (KeyError, TypeError):
            continue
    return None


def _get_json_fare(data: dict) -> str:
    """Extract total fare from any JSON response with fareDetails."""
    fd = _get_fare_details(data)
    if fd:
        try:
            return str(fd["tripFareSummary"]["totalAmount"])
        except (KeyError, TypeError):
            pass
    return ""


def _get_json_fbc(data: dict) -> str:
    """Extract fare basis code from any JSON response with fareDetails."""
    fd = _get_fare_details(data)
    if not fd:
        return ""
    try:
        jfs = fd["journeyFareSummary"]
        if isinstance(jfs, list) and jfs:
            sf = jfs[0].get("segmentFares", [])
            if sf:
                ptf = sf[0].get("passengerTypeFareBasisDetailsList", [])
                if ptf:
                    return ptf[0]["fareBasisDetails"]["fareBasisCode"]
    except (KeyError, TypeError, IndexError):
        pass
    return ""


def _get_json_baggage(data: dict) -> bool | None:
    """Check if fareDetails includes CHECK_IN_BAGGAGE."""
    fd = _get_fare_details(data)
    if not fd:
        return None
    try:
        jfs = fd["journeyFareSummary"]
        if isinstance(jfs, list) and jfs:
            sf = jfs[0].get("segmentFares", [])
            if sf:
                inclusions = sf[0].get("inclusions", [])
                return "CHECK_IN_BAGGAGE" in inclusions
    except (KeyError, TypeError, IndexError):
        pass
    return None


# ── Legacy hold extractors (delegate to generic) ──────────────────
# Kept for backward compatibility with existing tests.


def _get_sms_hold_fare(sms_hold: dict) -> str:
    """Extract total fare from SMS_HOLD response."""
    return _get_json_fare(sms_hold)


def _get_sms_hold_fbc(sms_hold: dict) -> str:
    """Extract fare basis code from SMS_HOLD response."""
    return _get_json_fbc(sms_hold)


def _get_sms_hold_baggage(sms_hold: dict) -> bool | None:
    """Check if SMS_HOLD fare includes CHECK_IN_BAGGAGE."""
    return _get_json_baggage(sms_hold)


# ── SOAP field extraction (Air Arabia / OTA specific) ─────────────


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
    """Find a call by API name (checks both api and api_type), load and parse."""
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


def _find_and_parse_pair(
    calls: list[CallNode], api_type: str, api: str,
    req_or_res: str, trip_id: str, cache: TripCache,
) -> Any | None:
    """Find a call by exact (api_type, api) pair, load and parse."""
    file_name = _find_file_name_pair(calls, api_type, api, req_or_res)
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
    """Find the file name for a specific API call.

    Searches both call.api and call.api_type for a match.
    Uses last matching call (skip retries).
    """
    target_call = None
    for call in calls:
        if call.api == api or call.api_type == api:
            target_call = call

    if target_call is None:
        return ""

    ref = target_call.res_file if req_or_res == "res" else target_call.req_file
    return ref.file_name if ref else ""


def _find_file_name_pair(
    calls: list[CallNode], api_type: str, api: str, req_or_res: str,
) -> str:
    """Find file name by exact (api_type, api) match."""
    target_call = None
    for call in calls:
        if call.api_type == api_type and call.api == api:
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
