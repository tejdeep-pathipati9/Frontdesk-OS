"""Contact formatting at the business boundary, independent of the database."""
import re


def normalize_phone(value, country_code=None):
    if value is None or not str(value).strip():
        return ""
    value = str(value).strip()
    if not re.fullmatch(r"[+\d\s().-]+", value):
        raise ValueError("Use digits with a country code, or omit the optional contact phone")
    compact = re.sub(r"[\s().-]", "", value)
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    if not compact.startswith("+") and str(country_code) == "1":
        if re.fullmatch(r"[2-9]\d{2}[2-9]\d{6}", compact):
            compact = "+1" + compact
        elif re.fullmatch(r"1[2-9]\d{2}[2-9]\d{6}", compact):
            compact = "+" + compact
    if not re.fullmatch(r"\+[1-9]\d{7,14}", compact):
        raise ValueError("Include the phone country code, or omit the optional contact phone")
    return compact
