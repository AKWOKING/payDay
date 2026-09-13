"""Cameroon MSISDN handling (M1 / LB-10).

Both adapters previously used the same one-liner:

    return phone.replace("+", "").strip()

so an Orange collection carried `237699123456`, where Orange Money's merchant
documentation describes the **9-digit national** form for `subscriber_msisdn`.
One transformation cannot be correct for two operators whose APIs document
different formats.

Two deliberate design choices, both to avoid breaking real payments:

1. **Formatting is per operator.** MTN wants E.164 without `+` (country code
   kept); Orange wants the national 9-digit number.

2. **Prefixes are advisory, not authoritative.** Cameroon has number
   portability: a prefix reflects the *original* allocation, not the network a
   number is on today. Published prefix tables also contradict each other (MTN
   and Orange are both credited with the `68x` range). Rejecting a payment
   because a stale table disagrees with a ported number would be worse than
   sending it and letting the operator answer — so a mismatch logs a warning,
   and hard rejection is opt-in via `TELCO_STRICT_OPERATOR_PREFIX`.

What *is* rejected outright: a national number beginning with `2`. Those are
fixed lines (or the special `22`/`23`/`24` blocks) and no mobile money account
can exist on one.
"""
from __future__ import annotations

import re
from typing import Optional

from payday.core.exceptions import PayDayException

#: National mobile numbers: 9 digits beginning with 6 (after the 2014 renumbering).
_NATIONAL_MOBILE = re.compile(r"^6\d{8}$")
#: National fixed-line numbers: 9 digits beginning with 2.
_NATIONAL_FIXED = re.compile(r"^2\d{8}$")
#: Country calling code for Cameroon.
CAMEROON_COUNTRY_CODE = "237"

#: Mobile-number blocks per operator, keyed by the first three national digits.
#: Sources: dialingcodes.me/en/cm.html and mycountrymobile.com (see M1 plan §7).
#: Where sources disagree (notably 680–683 vs 686–689) the 3-digit blocks are
#: listed explicitly rather than collapsed into a 2-digit rule, so a guess is
#: never presented as a fact. Anything absent from this table produces no
#: warning at all.
OPERATOR_PREFIX_BLOCKS: dict[str, tuple[str, ...]] = {
    "MTN": ("670", "671", "672", "673", "674", "675", "676", "677", "678", "679",
            "650", "651", "652", "653", "654",
            "680", "681", "682", "683"),
    "ORANGE": ("690", "691", "692", "693", "694", "695", "696", "697", "698", "699",
               "655", "656", "657", "658", "659",
               "686", "687", "688", "689"),
    "NEXTTEL": ("660", "661", "662", "663", "664", "665", "666", "667", "668", "669",
                "684", "685"),
    "CAMTEL": ("620", "621", "622", "623", "624", "625", "626", "627", "628", "629"),
}


def national_number(phone: str) -> str:
    """Return the 9-digit national form of a Cameroonian number.

    Accepts `6XXXXXXXX`, `2376XXXXXXXX`, `+2376XXXXXXXX` (spaces, hyphens and
    parentheses tolerated). Raises for fixed lines, malformed input, and any
    other country code.
    """
    cleaned = re.sub(r"[\s\-()]", "", phone or "")
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    if cleaned.startswith(CAMEROON_COUNTRY_CODE):
        cleaned = cleaned[len(CAMEROON_COUNTRY_CODE):]
    if cleaned.startswith("00"):  # 00237… international prefix form
        cleaned = cleaned[2:]
        if cleaned.startswith(CAMEROON_COUNTRY_CODE):
            cleaned = cleaned[len(CAMEROON_COUNTRY_CODE):]

    if _NATIONAL_FIXED.match(cleaned):
        raise PayDayException(
            status_code=400,
            detail=(
                f"'{phone}' is a fixed-line number. Mobile money requires a mobile "
                "number (9 digits beginning with 6, e.g. 6XXXXXXXX)."
            ),
            code="NOT_A_MOBILE_NUMBER",
            title="Invalid Phone Number",
        )
    if not _NATIONAL_MOBILE.match(cleaned):
        raise PayDayException(
            status_code=400,
            detail=(
                f"'{phone}' is not a valid Cameroonian mobile number. Expected 9 "
                "digits beginning with 6, with an optional +237 country code."
            ),
            code="INVALID_PHONE_NUMBER",
            title="Invalid Phone Number",
        )
    return cleaned


def operator_for(phone: str) -> Optional[str]:
    """Best-effort operator for a number, or None when the block is unmapped.

    Advisory only — see the module docstring on portability.
    """
    try:
        national = national_number(phone)
    except PayDayException:
        return None
    block = national[:3]
    for operator, blocks in OPERATOR_PREFIX_BLOCKS.items():
        if block in blocks:
            return operator
    return None


def format_for_operator(phone: str, operator: str) -> str:
    """Format a number the way `operator`'s API documents it.

    MTN  → `237XXXXXXXXX`  (E.164 without `+`)
    ORANGE → `6XXXXXXXX`   (national, as Orange Money's merchant API describes
                            `subscriber_msisdn`)

    Any other operator keeps the E.164 form, which is the least surprising
    default for a new integration.
    """
    national = national_number(phone)
    if operator.upper() == "ORANGE":
        return national
    return f"{CAMEROON_COUNTRY_CODE}{national}"
