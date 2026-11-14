"""Plain-code judgement: verify what the model read, then decide category and risk.

Everything here is deterministic and unit-tested, because it has to be right:
money maths, dates, the creator's rules and her exclusivity.
"""

from __future__ import annotations

import re
from datetime import date

from .models import Category, DealTerms, Deliverable, Extraction, Flag, Signals
from .money import fmt_inr
from .signals import is_scam

RATE_KEYS = {
    ("instagram", "reel"): "instagram_reel",
    ("instagram", "post"): "instagram_reel",  # closest item on the rate card
    ("instagram", "story"): "instagram_story",
    ("youtube", "integration"): "youtube_integration",
    ("youtube", "shoutout"): "youtube_integration",
    ("youtube", "dedicated_video"): "youtube_dedicated_video",
}

# Content rules from the profile, mapped to the words that trigger them.
CONTENT_RULE_KEYWORDS = {
    "gambling": [r"gambl", r"\bbet(ting)?\b", r"casino", r"fantasy", r"rummy", r"poker", r"satta", r"\bdream11\b"],
    "crypto": [r"crypto", r"bitcoin", r"\btoken\b", r"trading tip", r"forex", r"\bnft\b", r"stock tips?"],
}

CATEGORY_SYNONYMS = {
    "smartphones": [r"smart ?phones?", r"\bphones?\b", r"\bmobiles?\b", r"handset"],
}


class Profile:
    def __init__(self, data: dict):
        self.data = data
        self.today = date.fromisoformat(data["today"])
        self.rates: dict[str, float] = data["base_rates_inr"]
        self.usd_to_inr = 84.0
        m = re.search(r"1 USD = (\d+(?:\.\d+)?) INR", data.get("currency_note", ""))
        if m:
            self.usd_to_inr = float(m.group(1))
        self.exclusivity = [
            {**e, "until": date.fromisoformat(e["until"])} for e in data.get("current_exclusivity", [])
        ]
        self.content_rules: list[str] = data.get("content_rules", [])
        self.included_usage_months = 3
        m = re.search(r"Up to (\d+) months", data.get("usage_rights_policy", ""))
        if m:
            self.included_usage_months = int(m.group(1))

    def to_inr(self, amount: float | None, currency: str | None) -> float | None:
        if amount is None:
            return None
        return amount * self.usd_to_inr if currency == "USD" else amount

    def rate_card(self, deliverables: list[Deliverable]) -> float | None:
        """What the creator's own rates say this package is worth, or None if unknown."""
        if not deliverables:
            return None
        total = 0.0
        for d in deliverables:
            key = RATE_KEYS.get((d.platform, d.format))
            if key is None or key not in self.rates:
                return None
            total += self.rates[key] * d.count
        return total


# ---------------------------------------------------------------- verification


def verify_extraction(ex: Extraction, sig: Signals) -> list[Flag]:
    """Check the model's numbers and dates against the text; fix the year if it drifted."""
    flags: list[Flag] = []
    if ex.fee_amount is not None and sig.amounts and not any(abs(ex.fee_amount - a) < 1 for a in sig.amounts):
        flags.append(Flag(code="fee_unverified", severity="medium",
                          reason=f"Fee {ex.fee_amount:,.0f} could not be found in the email text; check it by hand."))
    # The model sometimes leaves the fee empty while its own summary quotes it. If the text
    # holds exactly one money amount and this is not a gifting offer, take it from the text.
    distinct = sorted(set(sig.amounts))
    if ex.fee_amount is None and ex.payment_type != "gifting" and ex.email_kind == "brand_outreach" and len(distinct) == 1:
        ex.fee_amount = distinct[0]
        flags.append(Flag(code="fee_from_text", severity="low",
                          reason=f"Fee {distinct[0]:,.0f} was read from the text by plain code; the model missed it."))
    if ex.fee_amount is not None and ex.payment_type == "unknown":
        ex.payment_type = "cash"
    for field in ("campaign_start", "deadline"):
        d: date | None = getattr(ex, field)
        if d is None:
            continue
        if not sig.dates:  # no date written anywhere: the model computed or guessed it
            setattr(ex, field, None)
            continue
        if d in sig.dates:
            continue
        same_day = [x for x in sig.dates if (x.month, x.day) == (d.month, d.day)]
        if same_day:  # model picked the wrong year; the text wins
            setattr(ex, field, same_day[0])
        else:
            flags.append(Flag(code="date_unverified", severity="low",
                              reason=f"{field.replace('_', ' ')} {d:%d %b} is not written in the email; check it."))
    return flags


# ---------------------------------------------------------------- checks


def content_rule_conflict(text: str, product_category: str | None) -> tuple[str, str] | None:
    haystack = f"{text} {product_category or ''}".lower()
    for rule_key, patterns in CONTENT_RULE_KEYWORDS.items():
        for p in patterns:
            if m := re.search(p, haystack):
                return rule_key, m.group(0)
    return None


def exclusivity_conflict(profile: Profile, text: str, terms: DealTerms) -> dict | None:
    # Synonyms like "phone" only count in what the brand sells, not anywhere in the text
    # ("share your phone number" is not a smartphone campaign).
    product = f"{terms.product_category or ''} {terms.product or ''}".lower()
    text = text.lower()
    for ex in profile.exclusivity:
        if ex["until"] < profile.today:
            continue
        if terms.brand and ex["brand"].lower() in terms.brand.lower():
            continue  # it is the exclusive partner itself
        patterns = CATEGORY_SYNONYMS.get(ex["category"], [re.escape(ex["category"].rstrip("s"))])
        if any(re.search(p, product) for p in patterns) or re.search(patterns[0], text):
            return ex
    return None


def scam_flags(sig: Signals, ex: Extraction | None = None) -> list[Flag]:
    f: list[Flag] = []
    ev = sig.evidence
    if sig.prompt_injection:
        f.append(Flag(code="prompt_injection", severity="high",
                      reason=f'Contains hidden instructions aimed at AI tools ("{ev["prompt_injection"]}"). Real brands do not do this.'))
    if sig.pay_to_join:
        f.append(Flag(code="pay_to_join", severity="high",
                      reason="Asks the creator to pay to take part. Her rule: never pays to join programmes."))
    if sig.phishing_link:
        f.append(Flag(code="phishing_link", severity="high", reason=ev["phishing_link"] + "; do not click it."))
    if sig.lookalike_sender:
        f.append(Flag(code="lookalike_sender", severity="high", reason=ev["lookalike_sender"] + "."))
    if sig.asks_personal_info:
        f.append(Flag(code="asks_personal_info", severity="high",
                      reason=f'Asks for personal details ("{ev["asks_personal_info"]}"). Never share these with a brand.'))
    if sig.urgency and f:
        f.append(Flag(code="pressure_tactics", severity="medium",
                      reason=f'Uses pressure ("{ev.get("urgency", "urgent")}") to rush a decision.'))
    return f


def deal_flags(profile: Profile, terms: DealTerms, text: str, sig: Signals, agency_unverified: bool) -> list[Flag]:
    """Risk flags for a legitimate-looking deal, judged against the profile."""
    f: list[Flag] = []

    if hit := content_rule_conflict(text, terms.product_category):
        rule = next((r for r in profile.content_rules if hit[0][:5] in r.lower()), hit[0])
        f.append(Flag(code="content_rule", severity="high", reason=f'Mentions "{hit[1]}". Breaks her rule: {rule}'))

    if ex := exclusivity_conflict(profile, text, terms):
        f.append(Flag(code="exclusivity_conflict", severity="high",
                      reason=f"{terms.brand or 'This brand'} is a {ex['category']} brand. She is exclusive to "
                             f"{ex['brand']} for {ex['category']} until {ex['until']:%d %b %Y}."))

    # money
    rate = terms.rate_card_inr
    if terms.payment_type == "gifting" and terms.fee_inr is None:
        value = terms.gift_value_inr
        msg = "Product only, no cash fee"
        if value and rate:
            msg += f": gift worth Rs {fmt_inr(value)} for work she charges Rs {fmt_inr(rate)} for ({value / rate:.0%} of her rate)"
        f.append(Flag(code="gifting_only", severity="medium", reason=msg + "."))
    elif terms.fee_inr is not None and rate:
        if terms.fee_inr < rate:
            gap = 1 - terms.fee_inr / rate
            fee_txt = f"Rs {fmt_inr(terms.fee_inr)}"
            if terms.currency == "USD":
                fee_txt = f"USD {terms.fee_amount:,.0f} (Rs {fmt_inr(terms.fee_inr)})"
            f.append(Flag(code="below_rate", severity="high" if gap >= 0.3 else "medium",
                          reason=f"Offer {fee_txt} is {gap:.0%} below her rate card of Rs {fmt_inr(rate)} for these deliverables."))
    if len(terms.fee_history_inr) > 1 and terms.fee_history_inr[-1] < max(terms.fee_history_inr[:-1]):
        f.append(Flag(code="fee_reduced", severity="medium",
                      reason=f"The brand lowered its offer from Rs {fmt_inr(max(terms.fee_history_inr[:-1]))} "
                             f"to Rs {fmt_inr(terms.fee_history_inr[-1])} in a later email."))

    # usage rights
    u = terms.usage_rights
    if u.perpetual:
        f.append(Flag(code="perpetual_usage", severity="high",
                      reason=f"Wants perpetual usage rights. Only {profile.included_usage_months} months of paid-ad usage is included in her rate; more must be priced separately."))
    elif u.paid_ads_months and u.paid_ads_months > profile.included_usage_months:
        f.append(Flag(code="extended_usage", severity="medium",
                      reason=f"Wants {u.paid_ads_months} months of ad usage; {profile.included_usage_months} are included, the rest is extra."))
    if u.whitelisting:
        f.append(Flag(code="whitelisting", severity="medium",
                      reason="Wants whitelisting (running ads from her handle). That is a separate, paid permission with a time limit."))

    # dates
    if terms.deadline:
        days = (terms.deadline - profile.today).days
        if days < 0:
            f.append(Flag(code="deadline_passed", severity="high",
                          reason=f"Content had to be live by {terms.deadline:%d %b}, which is {-days} days ago."))
        elif days < 4:
            f.append(Flag(code="tight_deadline", severity="medium",
                          reason=f"Only {days} day(s) until the deadline on {terms.deadline:%d %b}."))
    if agency_unverified:
        f.append(Flag(code="third_party_agency", severity="low",
                      reason=f"{terms.agency or 'An agency'} writes from a different domain than the brand. "
                             f"Confirm with {terms.brand or 'the brand'} before sharing GST or bank details."))
    if sig.free_email_sender:
        f.append(Flag(code="personal_email_domain", severity="low",
                      reason="Sent from a personal Gmail-style address; normal for small brands, but verify them."))
    if sig.mentions_attachment and not terms.deliverables and terms.fee_inr is None:
        f.append(Flag(code="attachment_only", severity="low",
                      reason="All details are in an attachment this tool does not open. Ask for the key terms in the email."))
    return f


def missing_details(terms: DealTerms) -> list[str]:
    missing = []
    if not terms.deliverables:
        missing.append("deliverables")
    if terms.fee_inr is None and terms.payment_type != "gifting":
        missing.append("fee")
    if terms.deadline is None:
        missing.append("timeline")
    if terms.usage_rights.paid_ads_months is None and not terms.usage_rights.perpetual and not terms.usage_rights.description:
        missing.append("usage rights")
    return missing


def decide_category(ex: Extraction, sig: Signals, terms: DealTerms, flags: list[Flag]) -> Category:
    codes = {f.code for f in flags}
    # Only text evidence makes an email a scam. Letting the model's yes/no reads decide
    # was tried first and killed a real deal (see README), so they are shown but never decide.
    if is_scam(sig):
        return "scam_or_phishing"
    if ex.email_kind == "account_or_platform_notice" and (sig.links or sig.urgency):
        return "scam_or_phishing"  # real platforms do not email creators from look-alike domains with links
    if sig.newsletter or ex.email_kind == "newsletter_or_marketing" or (ex.email_kind == "other" and not terms.brand):
        return "spam"
    if codes & {"content_rule", "exclusivity_conflict"}:
        return "not_a_fit"
    missing = missing_details(terms)
    if "deliverables" in missing or "fee" in missing:
        return "needs_more_info"
    return "deal_offer"
