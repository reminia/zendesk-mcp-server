"""Optional GCF (Graph Compact Format, https://gcformat.com) output for tool results.

When ``ZENDESK_OUTPUT_FORMAT=gcf`` is set, each tool's JSON result is re-encoded as a
GCF generic wire. The list tools here (tickets, comments, search results) return arrays
of uniform records where JSON repeats every field name on every record; GCF factors those
names into a single header, so a model spends fewer tokens reading the result.

The substitution is strictly conservative, so enabling it can only ever help:

* never larger: a result is re-encoded only when the GCF wire is smaller than the JSON,
* never lossy: the wire is decoded and compared back to the input; on any parse, encode,
  or mismatch the original JSON is returned unchanged,
* JSON-only: non-JSON text (error messages, base64 blobs) is passed through untouched.
"""
from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

from gcf import decode_generic, encode_generic

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp.server import types

logger = logging.getLogger(__name__)


def gcf_enabled() -> bool:
    """True when GCF output is requested via the environment."""
    return os.getenv("ZENDESK_OUTPUT_FORMAT", "").strip().lower() == "gcf"


def _canonical(value: object) -> str:
    """Order-insensitive, type-aware canonical form for a lossless comparison."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def to_gcf(text: str) -> str:
    """Return a GCF encoding of a JSON text when it is smaller and lossless.

    Returns the original ``text`` unchanged when it is not JSON, when GCF would not be
    smaller, or when the wire does not round-trip to the same value. Any failure in the
    encoder falls back to the JSON, so enabling GCF can never grow or garble a result.
    """
    stripped = text.lstrip()
    if not stripped or stripped[0] not in "[{":
        return text  # not a JSON object/array; nothing to gain

    try:
        data = json.loads(text)  # Python ints are exact, so no precision is lost here
    except ValueError:
        return text

    try:
        wire = encode_generic(data)  # raises on e.g. an integer outside the int64 domain
        if len(wire) >= len(text):
            return text  # never grow
        if _canonical(decode_generic(wire)) != _canonical(data):
            return text  # never corrupt
    except Exception:
        # Any encoder failure falls back to JSON; the fallback is always safe, so a GCF
        # problem can never grow, drop, or garble a tool result.
        logger.debug("GCF encoding skipped; keeping JSON", exc_info=True)
        return text

    return wire


def apply_gcf(results: Sequence[types.TextContent]) -> list[types.TextContent]:
    """Re-encode the text of each ``TextContent`` result as GCF when beneficial.

    Non-text content blocks are passed through untouched, and each text block is only
    rewritten when :func:`to_gcf` returns a smaller, lossless wire.
    """
    from mcp.server import types  # imported lazily so the helper is import-light

    return [
        item.model_copy(update={"text": to_gcf(item.text)})
        if isinstance(item, types.TextContent) else item
        for item in results
    ]
