"""Tests for the optional GCF output encoding."""
import json

from gcf import decode_generic
from mcp.server import types

from zendesk_mcp_server.gcf_output import apply_gcf, gcf_enabled, to_gcf


def _tickets(n: int) -> str:
    """A representative list-tool result: an array of uniform ticket records."""
    rows = [
        {
            "id": 30500 + i,
            "subject": ["Cannot log in", "Refund request", "Billing question"][i % 3],
            "status": ["open", "pending", "solved"][i % 3],
            "priority": ["low", "normal", "high"][i % 3],
            "requester_id": 900000 + i,
            "assignee_id": 700000 + (i % 5),
            "created_at": "2026-08-01T09:15:00Z",
        }
        for i in range(n)
    ]
    return json.dumps(rows)


class TestGcfEnabled:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("ZENDESK_OUTPUT_FORMAT", raising=False)
        assert gcf_enabled() is False

    def test_on_when_gcf(self, monkeypatch):
        for value in ("gcf", "GCF", " gcf "):
            monkeypatch.setenv("ZENDESK_OUTPUT_FORMAT", value)
            assert gcf_enabled() is True

    def test_off_for_json(self, monkeypatch):
        monkeypatch.setenv("ZENDESK_OUTPUT_FORMAT", "json")
        assert gcf_enabled() is False


class TestToGcf:
    def test_reencodes_record_array_smaller_and_lossless(self):
        original = _tickets(20)
        wire = to_gcf(original)

        assert wire != original
        assert wire.startswith("GCF profile=generic")
        assert len(wire) < len(original)
        # decodes back to exactly the same value
        assert decode_generic(wire) == json.loads(original)

    def test_passes_through_non_json_text(self):
        text = "Error: ticket 123 not found"
        assert to_gcf(text) == text

    def test_passes_through_malformed_json(self):
        text = '[{"id": 1, "subject":'
        assert to_gcf(text) == text

    def test_passes_through_when_not_smaller(self):
        text = json.dumps({"message": "Ticket created successfully"})
        assert to_gcf(text) == text

    def test_declines_integer_outside_int64_domain(self):
        # 2**63 is above the canonical int64 max; encode_generic raises, so JSON is kept.
        text = json.dumps([{"id": 2**63, "n": 1}, {"id": 2**63 + 1, "n": 2}])
        assert to_gcf(text) == text


class TestApplyGcf:
    """The seam: apply_gcf is what the call_tool wrapper runs over a tool's results."""

    def test_reencodes_text_record_array(self):
        original = _tickets(20)
        out = apply_gcf([types.TextContent(type="text", text=original)])

        assert len(out) == 1
        assert out[0].type == "text"
        assert out[0].text.startswith("GCF profile=generic")
        assert decode_generic(out[0].text) == json.loads(original)

    def test_passes_through_prose_text(self):
        item = types.TextContent(type="text", text="Error: ticket 123 not found")
        out = apply_gcf([item])
        assert out[0].text == "Error: ticket 123 not found"

    def test_passes_through_non_text_content(self):
        # A non-text content block (e.g. an image) is returned untouched.
        image = types.ImageContent(type="image", data="aGVsbG8=", mimeType="image/png")
        out = apply_gcf([image])
        assert out[0] is image

    def test_preserves_text_content_across_mixed_list(self):
        prose = types.TextContent(type="text", text="Created ticket 456")
        records = types.TextContent(type="text", text=_tickets(20))
        out = apply_gcf([prose, records])

        assert out[0].text == "Created ticket 456"
        assert out[1].text.startswith("GCF profile=generic")
