"""
US-only phone validation and normalization.

Country code +1 covers the US, Canada and ~20 Caribbean nations, so a naive
"+1 means American" check is wrong. We resolve the region explicitly and
require exactly "US".
"""
from typing import Optional, Tuple

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberType, geocoder

# Types we will text. FIXED_LINE_OR_MOBILE is included because US carriers
# frequently report legitimate mobiles that way. VOIP is allowed but flagged.
SMS_CAPABLE_TYPES = {
    PhoneNumberType.MOBILE,
    PhoneNumberType.FIXED_LINE_OR_MOBILE,
    PhoneNumberType.VOIP,
}
FLAGGED_TYPES = {PhoneNumberType.VOIP}

# Obvious fakes/test numbers people type into forms.
BLOCKED_PREFIXES = ("555555", "1234567", "0000000")


class PhoneResult:
    """Outcome of validating one submitted phone number."""

    def __init__(self, ok: bool, e164: Optional[str] = None,
                 reason: Optional[str] = None, risk_flag: Optional[str] = None,
                 number_type: Optional[str] = None):
        self.ok = ok
        self.e164 = e164
        self.reason = reason            # internal reason code, never shown raw
        self.risk_flag = risk_flag      # e.g. "voip"
        self.number_type = number_type

    @property
    def masked(self) -> str:
        if not self.e164:
            return "***"
        return f"***-***-{self.e164[-4:]}"


# Single safe message for every failure — never explain which rule tripped.
USER_ERROR = "Enter a valid U.S. mobile phone number."


def validate_us_mobile(raw: str) -> PhoneResult:
    """Validate, normalize and classify a US phone number."""
    if not raw or not raw.strip():
        return PhoneResult(False, reason="empty")
    try:
        parsed = phonenumbers.parse(raw, "US")
    except NumberParseException:
        return PhoneResult(False, reason="unparseable")

    if not phonenumbers.is_possible_number(parsed):
        return PhoneResult(False, reason="not_possible")
    if not phonenumbers.is_valid_number(parsed):
        return PhoneResult(False, reason="not_valid")
    if parsed.country_code != 1:
        return PhoneResult(False, reason="not_nanp")

    # Region check rejects Canada (+1 604 …) and Caribbean (+1 876 …).
    region = phonenumbers.region_code_for_number(parsed)
    if region != "US":
        return PhoneResult(False, reason=f"region_{region or 'unknown'}")

    national = str(parsed.national_number)
    if len(national) != 10:
        return PhoneResult(False, reason="bad_length")
    if national.startswith(BLOCKED_PREFIXES) or len(set(national)) == 1:
        return PhoneResult(False, reason="test_number")

    ntype = phonenumbers.number_type(parsed)
    type_names = {
        PhoneNumberType.MOBILE: "mobile",
        PhoneNumberType.FIXED_LINE: "landline",
        PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed_or_mobile",
        PhoneNumberType.VOIP: "voip",
        PhoneNumberType.TOLL_FREE: "toll_free",
        PhoneNumberType.PREMIUM_RATE: "premium",
        PhoneNumberType.PAGER: "pager",
    }
    type_name = type_names.get(ntype, "unknown")
    if ntype not in SMS_CAPABLE_TYPES:
        return PhoneResult(False, reason=f"type_{type_name}", number_type=type_name)

    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    return PhoneResult(
        True, e164=e164, number_type=type_name,
        risk_flag="voip" if ntype in FLAGGED_TYPES else None,
    )


def national_format(e164: str) -> str:
    """(206) 944-0030 — for display in the admin."""
    try:
        return phonenumbers.format_number(
            phonenumbers.parse(e164, "US"),
            phonenumbers.PhoneNumberFormat.NATIONAL)
    except NumberParseException:
        return e164


def mask(e164: Optional[str]) -> str:
    return f"***-***-{e164[-4:]}" if e164 else "***"
