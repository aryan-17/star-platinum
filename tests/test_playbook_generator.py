"""Tests for playbook generator — using MockLLMClient."""

from oncall_rca.llm.client import MockLLMClient
from oncall_rca.tools.playbook_generator import (
    GeneratedPlaybook,
    PlaybookStep,
    generate_playbook,
    playbook_to_yaml,
)


class TestPlaybookGenerator:
    def test_generate_returns_playbook(self) -> None:
        mock = MockLLMClient()
        mock.set_response("GeneratedPlaybook", GeneratedPlaybook(
            incident_type="fare_mismatch",
            description="Fare shown to user differs from what was charged",
            chain=[
                PlaybookStep(
                    step_name="SS1 displayed fare",
                    api_name="SINGLE_SOLUTION_SEARCH",
                    check_file="res",
                    payload_format="json",
                    field_path="fareFamilyDTO[0].segmentFares[0].totalFare",
                    what_to_check="Fare amount shown to user",
                    invariant="Must match SIS-HOLD fare",
                ),
                PlaybookStep(
                    step_name="SIS-HOLD fare",
                    api_name="SUPPLY_CORE_APP_LAYER",
                    check_file="req",
                    payload_format="json",
                    field_path="bookedPromise.fareDetails.totalAmount",
                    what_to_check="Fare in hold request",
                    invariant="Must match SS1 fare",
                ),
            ],
            red_herrings=["Currency conversion rounding differences < 1 unit"],
        ))

        result = generate_playbook("Fare shown differs from charged", mock)
        assert result.incident_type == "fare_mismatch"
        assert len(result.chain) == 2
        assert result.chain[0].api_name == "SINGLE_SOLUTION_SEARCH"

    def test_playbook_to_yaml(self) -> None:
        playbook = GeneratedPlaybook(
            incident_type="booking_failure",
            description="Booking failed at supplier",
            chain=[
                PlaybookStep(
                    step_name="SUPPLIER_BOOK response",
                    api_name="SUPPLIER_BOOK",
                    check_file="res",
                    payload_format="soap_xml",
                    field_path="OTA_AirBookRS/Success",
                    what_to_check="Booking success indicator",
                    invariant="Must contain <Success/>",
                ),
            ],
            red_herrings=["Session expiry retry is expected"],
            notes="Check for inline ticketing",
        )

        yaml_str = playbook_to_yaml(playbook)
        assert "booking_failure" in yaml_str
        assert "SUPPLIER_BOOK" in yaml_str
        assert "Session expiry" in yaml_str
        assert "incident_type:" in yaml_str
