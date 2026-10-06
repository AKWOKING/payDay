"""XAF money arithmetic — whole-franc discipline (M1 / LB-9).

The CFA franc has **no minor unit**. XAF is ISO 4217 exponent 0: there is no
centime in circulation, and a payment API that receives `1000.55` has to do
*something* with the half franc — round it, reject it, or drop it silently.

Before this module the codebase had three answers at once:

- `schemas/transaction.py` accepted any positive Decimal, so `1000.55` was a
  valid deposit request;
- `mtn_momo.py` sent `str(amount)` → `"1000.55"` to an API that documents an
  integer-valued amount for a zero-decimal currency;
- `orange_money.py` sent `int(amount)` → `1000`, **truncating** the franc, while
  the ledger credited `995.55` net. The operator collected 1 000 and PayDay's
  books recorded 1 000.55 of value in.

This module is the single authority for turning a computed amount into francs:

    whole_xaf(Decimal("61.72")) → Decimal("62")

Fee policy (`XAF_ROUNDING_MODE`) is configuration rather than a hard-coded
`.quantize("0.01")`, because the direction of rounding is a commercial decision
(D26 in `docs/MVP_EXECUTION_ROADMAP.md` §7) that the source code should not make
silently. The default is `HALF_UP`, the ordinary accounting convention.
"""
from __future__ import annotations

from decimal import (
    Decimal,
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    InvalidOperation,
)
from typing import Union

from payday.core.config import settings

Number = Union[Decimal, int, float, str]

#: Rounding modes selectable by configuration, mapped to `decimal` constants.
ROUNDING_MODES = {
    "HALF_UP": ROUND_HALF_UP,
    "HALF_EVEN": ROUND_HALF_EVEN,
    "DOWN": ROUND_DOWN,
    "UP": ROUND_UP,
}


def _as_decimal(amount: Number) -> Decimal:
    """Coerce to Decimal without going through binary float noise.

    `Decimal(0.1)` is 0.1000000000000000055511151231257827; `Decimal("0.1")` is
    0.1. Amounts arrive from JSON as float (pydantic) or str (tests), so route
    floats through their string form.
    """
    if isinstance(amount, Decimal):
        return amount
    if isinstance(amount, float):
        return Decimal(str(amount))
    return Decimal(amount)


def whole_xaf(amount: Number, rounding: str | None = None) -> Decimal:
    """Round an amount to a whole number of XAF.

    Returns an integral `Decimal` (e.g. `Decimal("62")`) so it stays exact in
    the database column and serialises without a fractional part on the wire.
    """
    mode = ROUNDING_MODES.get(rounding or settings.XAF_ROUNDING_MODE)
    if mode is None:
        raise ValueError(
            f"Unknown XAF rounding mode {rounding or settings.XAF_ROUNDING_MODE!r}. "
            f"Expected one of {sorted(ROUNDING_MODES)}."
        )
    return _as_decimal(amount).quantize(Decimal("1"), rounding=mode)


def is_whole_xaf(amount: Number) -> bool:
    """True when the amount is representable in francs exactly."""
    try:
        return _as_decimal(amount) == _as_decimal(amount).to_integral_value()
    except (InvalidOperation, ValueError):
        return False


def to_wire_amount(amount: Number) -> int:
    """The integer value to send to an operator API.

    Operators differ on the *JSON type* (MTN documents a string, Orange's
    examples are numeric), so each adapter serialises this int in its own
    documented type. The **value** is identical, which is what reconciliation
    depends on — that guarantee is pinned by the golden-payload tests.
    """
    return int(whole_xaf(amount))


def to_wire_amount_string(amount: Number) -> str:
    """Whole francs as a string, for APIs that document a string amount."""
    return str(to_wire_amount(amount))
