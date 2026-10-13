"""Contact formatting at the business boundary, independent of the database.

Phones are stored and compared in E.164 form so booking/order verification matches exactly.
"""
import re


def spoken_digits(value):
    """Expand only unambiguous digit words; leave malformed numbers for validation."""
    words = {'zero': '0', 'oh': '0', 'one': '1', 'two': '2', 'three': '3',
             'four': '4', 'five': '5', 'six': '6', 'seven': '7', 'eight': '8',
             'nine': '9', 'plus': '+'}
    tokens = str(value).lower().split()
    if tokens and all(token in words or token.isdecimal() for token in tokens):
        return ''.join(words.get(token, token) for token in tokens)
    return value


def normalize_phone(value, country_code=None):
    """Normalize to E.164 (+digits) or "" when blank.

    `country_code` is the location's default; only "1" (NANP) allows national-format input.
    Raises ValueError (callers map to PHONE_FORMAT_REQUIRED) for bad characters or no country code.
    """
    if value is None or not str(value).strip():
        return ""
    value = str(value).strip()
    # A phone spoken as separate digits is unambiguous. Never infer digits
    # from homophones such as 'by', 'for', 'ate', or truncated speech.
    value = spoken_digits(value)
    error = ("I didn't catch a complete phone number. Use all ten digits for a US number, "
             "or an international number with its country code; you can also omit it"
             if str(country_code) == '1' else
             "Include the phone country code, or omit the optional contact phone")
    if not re.fullmatch(r"[+\d\s().-]+", value):
        raise ValueError(error)
    compact = re.sub(r"[\s().-]", "", value)
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    if not compact.startswith("+") and str(country_code) == "1":
        if re.fullmatch(r"[2-9]\d{2}[2-9]\d{6}", compact):
            compact = "+1" + compact
        elif re.fullmatch(r"1[2-9]\d{2}[2-9]\d{6}", compact):
            compact = "+" + compact
    if not re.fullmatch(r"\+[1-9]\d{7,14}", compact):
        raise ValueError(error)
    if compact.startswith('+1') and not re.fullmatch(r'\+1[2-9]\d{2}[2-9]\d{6}', compact):
        raise ValueError(error)
    return compact
