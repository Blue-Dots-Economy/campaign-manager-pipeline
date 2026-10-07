"""Unit tests for transform_purple. No network, no credentials.

The call fixtures below are synthetic: shaped like Raya's /api/call/{uuid}
response, but every name, number and id is made up. Real call data must never
be committed here; it carries disability status against a profile id.
"""

import json

from transform_purple import (
    make_connections,
    make_row,
    profile_from_tools,
    str_list,
    yes_no,
)

# Personal details the bot sends to update_profile. None of these may ever
# reach a row: the table's whole design is ids only.
PERSONAL = {
    "beneficiary_name": "Testname Fakeperson",
    "phone": "9000000001",
    "age": 47,
    "gender": "Female",
    "address": "12 Example Lane, Basti",
    # Distinct from the coded disability_category_mapped below, which the row
    # does keep: this is the profile tool's own free-text value.
    "disability_type": "Orthopaedic, both legs, since birth",
    "disability_percentage": "60",
    "documents_available": "UDID card",
}


def tool_call(name, arguments):
    return {"function": {"name": name, "arguments": arguments}}


def a_call(**overrides):
    call = {
        "uuid": "00000000-0000-4000-8000-000000000001",
        "agent_id": "11111111-1111-4111-8111-111111111111",
        "outcome": "Completed",
        "call_start_time": "2026-10-01T18:45:00.000Z",
        "call_duration": "212",
        "to_number": "9000000002",
        "agent_args": {
            "persona": "Beneficiary",
            "contact_reference_type": "Support centre",
            "contact_reference": "Example Centre",
            "contact_name": "Testname Fakeperson",
            "contact_phone": "9000000002",
        },
        "call_output": {
            "call_answered": "Yes",
            "call_engaged": "yes",
            "full_journey_completed": "No",
            "abandoned_at_stage": "NA",
            "disability_category_mapped": "['Locomotor Disability']",
            "matching_providers_found": "3",
            "call_value_score": "n/a",
            "disabilities_discussed": "locomotor disabled in both legs",
        },
        "call_transcript": [
            {"role": "assistant", "content": "Namaste"},
            {"role": "user", "content": "Haan"},
            {
                "role": "assistant",
                "tool_calls": [
                    tool_call(
                        "update_profile",
                        json.dumps({"item_id": "item-1", "user_id": "user-1", **PERSONAL}),
                    )
                ],
            },
        ],
    }
    call.update(overrides)
    return call


class TestNoPersonalData:
    def test_row_carries_no_personal_value(self):
        row = make_row(a_call(), agent_name="Purple-dots-with-APIs-V2-Latest")
        flat = json.dumps(row, default=str)
        for field, value in PERSONAL.items():
            assert str(value) not in flat, f"{field} leaked into the row"
        # phone numbers from the call record and agent_args too
        assert "9000000002" not in flat
        assert "locomotor disabled in both legs" not in flat

    def test_row_has_no_personal_columns(self):
        row = make_row(a_call())
        forbidden = {
            "name",
            "phone",
            "age",
            "gender",
            "address",
            "transcript",
            "recording",
            "contact_name",
            "contact_phone",
        }
        assert not {
            k
            for k in row
            if k in forbidden
            or "phone" in k
            or k.endswith("_name")
            and k not in ("agent_name", "campaign_name")
        }

    def test_profile_keeps_ids_only(self):
        prof = profile_from_tools(a_call()["call_transcript"])
        assert prof == {"item_id": "item-1", "user_id": "user-1"}


class TestValues:
    def test_placeholders_become_none(self):
        row = make_row(a_call())
        assert row["abandoned_at_stage"] is None  # "NA"
        assert row["call_value_score"] is None  # "n/a"

    def test_yes_no_keeps_unanswered_distinct_from_no(self):
        assert yes_no("Yes") is True
        assert yes_no("no") is False
        assert yes_no("NA") is None
        assert yes_no(None) is None

    def test_python_repr_list_is_parsed(self):
        assert str_list("['Hearing Impairment', 'Low Vision']") == [
            "Hearing Impairment",
            "Low Vision",
        ]
        assert str_list("A | B") == ["A", "B"]
        assert str_list("[]") is None

    def test_utc_is_converted_to_ist(self):
        row = make_row(a_call())
        # 18:45 UTC on 1 Oct is 00:15 IST on 2 Oct
        assert row["call_date_ist"] == "2026-10-02"
        assert row["call_datetime_ist"] == "2026-10-02T00:15:00"

    def test_numbers(self):
        row = make_row(a_call())
        assert row["call_duration_seconds"] == 212.0
        assert row["matching_providers_found"] == 3


class TestStatusAndChannel:
    def test_failure_reads_as_unanswered(self):
        assert make_row(a_call(outcome="Failure"))["call_status"] == "Unanswered"

    def test_contact_status_wins_over_call_outcome(self):
        row = make_row(a_call(outcome="Failure"), contact={"status": "Busy"})
        assert row["call_status"] == "Busy"

    def test_inbound_is_inferred(self):
        inbound = a_call(caller_no="9000000003", to_number=None)
        assert make_row(inbound)["channel"] == "Inbound"
        assert make_row(a_call())["channel"] == "Outbound"

    def test_batch_and_campaign(self):
        row = make_row(a_call(), batch={"id": 3031, "name": "Tmf_Basti_1oct"})
        assert row["batch_id"] == "3031"
        assert row["campaign_name"] == "Tmf_Basti_1oct"
        assert make_row(a_call())["batch_id"] is None


class TestProfile:
    def test_last_non_empty_value_wins(self):
        transcript = [
            {"tool_calls": [tool_call("update_profile", {"item_id": "first", "user_id": "u"})]},
            {"tool_calls": [tool_call("update_profile", {"item_id": "second", "user_id": ""})]},
        ]
        assert profile_from_tools(transcript) == {"item_id": "second", "user_id": "u"}

    def test_python_repr_item_state(self):
        transcript = [
            {
                "tool_calls": [
                    tool_call(
                        "update_profile",
                        {"item_state": "{'item_id': 'from-state', 'address': 'Somewhere'}"},
                    )
                ]
            }
        ]
        assert profile_from_tools(transcript) == {"item_id": "from-state"}


class TestConnections:
    def connect(self, provider_id):
        return tool_call(
            "connect_provider",
            {
                "source_item": {"item_id": "seeker-1"},
                "target_item": {"item_id": provider_id, "item_instance_url": "https://example.org"},
                "consent": {"acknowledged": True, "version": "2"},
            },
        )

    def test_one_row_per_provider_deduplicated(self):
        call = a_call(
            call_transcript=[
                {"tool_calls": [self.connect("prov-1"), self.connect("prov-1")]},
                {"tool_calls": [self.connect("prov-2")]},
            ]
        )
        rows = make_connections(call, "call-1")
        assert [r["provider_item_id"] for r in rows] == ["prov-1", "prov-2"]
        assert rows[0]["consent_acknowledged"] is True
        assert rows[0]["consent_version"] == 2

    def test_empty_provider_is_skipped(self):
        call = a_call(call_transcript=[{"tool_calls": [self.connect("")]}])
        assert make_connections(call, "call-1") == []
