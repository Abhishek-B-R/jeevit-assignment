"""Deterministic checks on the raw email text.

These run on the untrusted text before any model sees it, and their result is
final: a model can add suspicion but can never clear a signal raised here.
"""

from __future__ import annotations

import re
from datetime import date

from .models import Email, Signals

MONTHS = {
    m: i
    for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"],
        start=1,
    )
}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
# Hindi month names in Devanagari.
HINDI_MONTHS = {"जनवरी": 1, "फरवरी": 2, "फ़रवरी": 2, "मार्च": 3, "अप्रैल": 4, "मई": 5, "जून": 6, "जुलाई": 7,
                "अगस्त": 8, "सितंबर": 9, "सितम्बर": 9, "अक्टूबर": 10, "अक्तूबर": 10, "नवंबर": 11, "नवम्बर": 11,
                "दिसंबर": 12, "दिसम्बर": 12}

INJECTION_PATTERNS = [
    r"\b(note|message|instruction)s? to (any|the) (ai|assistant|model|bot)",
    r"\bai assistant\b",
    r"\b(ignore|disregard|forget|override) (all |any )?(your |the |previous |prior |above )*(instructions|rules|guidelines)",
    r"\b(language model|llm|chatgpt|gpt|claude|gemini)\b\s*[:,]",  # "Dear language model: ..."
    r"\bdear (ai|assistant|bot|model|language model)\b",
    r"\bsystem prompt\b",
    r"\bclassify this (email|message)\b",
    r"\brecommend (accept|accepting)\b",
    r"\byou (are|must) (now )?(act|respond|reply) as\b",
    r"[\[(<{]\s*(assistant|system|ai|bot|llm)\s*:",   # "[assistant: ...]" style role tags
    r"^\s*(assistant|system)\s*:",
    r"\bwhen summari[sz]ing\b",
    r"\b(mark|classify|label|flag) this (deal|email|message|offer) as\b",
]
PAY_TO_JOIN_PATTERNS = [
    r"\b(registration|joining|enrol?ment|onboarding|membership|activation) fee\b",
    r"\bpay (a |an )?(one[- ]time |small )?(fee|deposit|amount)\b",
    r"\bsecurity deposit\b",
]
PERSONAL_INFO_PATTERNS = [
    r"\b(personal|mobile|phone|whatsapp) (phone )?number\b",
    r"\bhome address\b",
    r"\b(password|otp|one[- ]time password|pin)\b",
    r"\bbank (account|details|login)\b",
    r"\b(aadhaar|aadhar|pan card)\b",
]
URGENCY_PATTERNS = [
    r"\b(within|in) 24 hours\b",
    r"\b24 hours only\b",
    r"\bvalid (for )?\d+ hours\b",
    r"\bact now\b",
    r"\bimmediately\b",
    r"\bwill be (suspended|terminated|deleted)\b",
    r"\baction required\b",
]
# Platforms that scammers like to impersonate, and their real domains.
OFFICIAL_DOMAINS = {
    "youtube": ("youtube.com", "google.com"),
    "google": ("google.com",),
    "instagram": ("instagram.com", "facebookmail.com", "meta.com"),
    "facebook": ("facebook.com", "facebookmail.com", "meta.com"),
    "meta": ("meta.com", "facebookmail.com"),
    "paypal": ("paypal.com",),
}
FREE_MAIL = ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "rediffmail.com", "icloud.com")


def _search(patterns: list[str], text: str, flags: int = re.IGNORECASE) -> str | None:
    for p in patterns:
        m = re.search(p, text, flags)
        if m:
            return m.group(0)
    return None


def _is_lookalike(domain: str) -> str | None:
    """Return the impersonated platform if `domain` uses its name but is not its domain."""
    domain = domain.lower()
    for name, real in OFFICIAL_DOMAINS.items():
        if name in domain and not any(domain == r or domain.endswith("." + r) for r in real):
            return name
    return None


AMOUNT_RE = re.compile(
    r"(?P<cur>inr|rs\.?|₹|usd|us\$|\$)?\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>lakhs?|lacs?|k\b|crores?)?",
    re.IGNORECASE,
)


def parse_amounts(text: str) -> list[float]:
    """Money-looking numbers: needs a currency marker or a unit (25k, 3 lakh)."""
    out: list[float] = []
    for m in AMOUNT_RE.finditer(text):
        cur, num, unit = m.group("cur"), m.group("num"), (m.group("unit") or "").lower()
        if not cur and not unit:
            continue
        try:
            value = float(num.replace(",", ""))
        except ValueError:
            continue
        if unit.startswith("lakh") or unit.startswith("lac"):
            value *= 100_000
        elif unit.startswith("crore"):
            value *= 10_000_000
        elif unit == "k":
            value *= 1_000
        out.append(value)
    return out


DAY_MONTH_RE = re.compile(
    r"\b(?P<d1>\d{1,2})(?:st|nd|rd|th)?"
    r"(?:\s*(?:-|–|to|and)\s*(?P<d2>\d{1,2})(?:st|nd|rd|th)?)?"
    r"\s+(?:of\s+)?(?P<month>january|february|march|april|may|june|july|august|september|"
    r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b",
    re.IGNORECASE,
)


HINDI_DATE_RE = re.compile(r"(\d{1,2})\s+(" + "|".join(HINDI_MONTHS) + ")")


def parse_dates(text: str, year: int) -> list[date]:
    """Dates like '10 December', '15-25 November', 'between 15 and 25 November', '30 नवंबर'."""
    out: list[date] = []
    for m in HINDI_DATE_RE.finditer(text):
        try:
            out.append(date(year, HINDI_MONTHS[m.group(2)], int(m.group(1))))
        except ValueError:
            pass
    for m in DAY_MONTH_RE.finditer(text):
        month = MONTHS.get(m.group("month").lower()[:3])
        if not month:
            continue
        for key in ("d1", "d2"):
            if m.group(key):
                try:
                    out.append(date(year, month, int(m.group(key))))
                except ValueError:
                    pass
    return out


def compute_signals(email: Email) -> Signals:
    text = f"{email.subject}\n{email.body}"
    s = Signals()

    if hit := _search(INJECTION_PATTERNS, text, re.IGNORECASE | re.MULTILINE):
        s.prompt_injection = True
        s.evidence["prompt_injection"] = hit
    if hit := _search(PAY_TO_JOIN_PATTERNS, text):
        s.pay_to_join = True
        s.evidence["pay_to_join"] = hit
    if hit := _search(PERSONAL_INFO_PATTERNS, text):
        s.asks_personal_info = True
        s.evidence["asks_personal_info"] = hit
    if hit := _search(URGENCY_PATTERNS, text):
        s.urgency = True
        s.evidence["urgency"] = hit

    s.links = re.findall(r"https?://[^\s)>\]]+", text)
    for link in s.links:
        host = re.sub(r"^https?://", "", link).split("/")[0].split(":")[0]
        if name := _is_lookalike(host):
            s.phishing_link = True
            s.evidence["phishing_link"] = f"{link} is not an official {name} address"

    if name := _is_lookalike(email.sender_domain):
        s.lookalike_sender = True
        s.evidence["lookalike_sender"] = f"{email.sender_domain} is not an official {name} domain"

    if re.search(r"\bunsubscribe\b", text, re.I) or email.sender.lower().startswith(("newsletter@", "news@", "digest@")):
        s.newsletter = True
    s.free_email_sender = email.sender_domain in FREE_MAIL
    s.mentions_attachment = bool(re.search(r"\b(attached|attachment|enclosed|pfa)\b", text, re.I))
    s.amounts = parse_amounts(text)
    s.dates = parse_dates(text, email.received_at.year)
    return s


def is_scam(s: Signals) -> bool:
    return s.prompt_injection or s.pay_to_join or s.phishing_link or s.lookalike_sender or s.asks_personal_info
