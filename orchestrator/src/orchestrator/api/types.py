"""Shared request-field types for the REST API.

Every free-text field a client can send goes through `text()`: surrounding whitespace is
stripped, NUL bytes are rejected (Postgres text columns cannot hold them, so they used to
surface as a 500), and a maximum length is enforced so one request cannot store megabytes.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, StringConstraints

# Limits are deliberately generous; they exist to stop abuse, not to shape content.
TITLE_MAX = 200
BRIEF_MAX = 20_000
OUTPUT_TYPE_MAX = 64
CRITERIA_MAX = 5_000
MESSAGE_MAX = 20_000
REASON_MAX = 1_000
LABEL_MAX = 100


def _no_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("must not contain NUL (0x00) characters")
    return value


def text(max_length: int, *, min_length: int = 1):
    """A stripped, NUL-free string of `min_length`..`max_length` characters."""
    return Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=min_length, max_length=max_length),
        AfterValidator(_no_nul),
    ]
