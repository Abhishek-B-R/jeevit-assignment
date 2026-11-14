"""Next step per deal (plain code) and a draft reply (model, with a template fallback).

The drafting model never sees the raw email. It gets a short brief built from the
verified terms, so instructions hidden in an email cannot reach it. Every draft is
checked before it is kept: no money figures we did not approve, no personal details.
"""

from __future__ import annotations

import re

from .llm import LLMClient, LLMError
from .models import Category, Deal, DealTerms, Flag
from .money import fmt_inr
from .rules import Profile, missing_details

NEGOTIATE_CODES = {"below_rate", "gifting_only", "perpetual_usage", "extended_usage", "whitelisting", "fee_reduced"}




def describe_deliverables(t: DealTerms) -> str:
    names = {"reel": "reel", "story": "story", "post": "post", "integration": "integration",
             "dedicated_video": "dedicated video", "shoutout": "shoutout", "other": "piece of content"}
    parts = []
    for d in t.deliverables:
        noun = names[d.format]
        if d.count > 1:
            noun = "stories" if noun == "story" else noun + "s"
        platform = {"instagram": "Instagram", "youtube": "YouTube"}.get(d.platform, "")
        parts.append(f"{d.count} {platform} {noun}".replace("  ", " "))
    return " + ".join(parts)


def choose_action(profile: Profile, cat: Category, t: DealTerms, flags: list[Flag]):
    """Returns (action, reason, counter_offer_inr, questions_to_ask)."""
    codes = {f.code for f in flags}
    questions: list[str] = []

    if cat == "scam_or_phishing":
        return "report", "Looks like a scam or phishing attempt. Do not reply, click links or pay.", None, []
    if cat == "spam":
        return "ignore", "Not a brand deal.", None, []
    if cat == "not_a_fit":
        why = next(f.reason for f in flags if f.code in ("exclusivity_conflict", "content_rule"))
        return "decline", why, None, []

    missing = missing_details(t)
    if cat == "needs_more_info":
        ask = {"deliverables": "which platforms and how many pieces of content",
               "fee": "the budget", "timeline": "the dates",
               "usage rights": "whether you plan to run the content as paid ads, and for how long"}
        questions = [ask[m] for m in missing]
        if "attachment_only" in codes:
            reason = "Details are only in an attachment. Ask for the key terms in writing."
        else:
            reason = "Possibly real, but missing " + ", ".join(missing) + "."
        return "ask_for_details", reason, None, questions

    # deal_offer
    if "timeline" in missing:
        questions.append("the dates the content should go live")
    if "usage rights" in missing:
        questions.append("whether you plan to run the content as paid ads, and for how long")
    if "third_party_agency" in codes and t.brand:
        questions.append(f"a quick confirmation from {t.brand} that you are running this campaign for them")

    if "deadline_passed" in codes:
        questions.insert(0, "whether the go-live date can move, since it has already passed")
        return "ask_for_details", "The deadline has already passed. Only worth it if they can move the date.", None, questions

    counter = None
    if codes & NEGOTIATE_CODES:
        reasons = [f.reason for f in flags if f.code in NEGOTIATE_CODES]
        if t.rate_card_inr and ("below_rate" in codes or "gifting_only" in codes or "fee_reduced" in codes):
            best_offer = max(t.fee_history_inr) if t.fee_history_inr else 0
            counter = max(t.rate_card_inr, best_offer)
        return "negotiate", " ".join(reasons), counter, questions

    return "accept", "Fee meets her rates and the terms fit her rules.", None, questions


# ---------------------------------------------------------------- drafts

DRAFT_SYSTEM = """You write short, warm, professional email replies for an Indian creator named Riya.
Write only the email body (no subject line). 50-110 words. Start with the exact greeting in the brief.
Write as Riya herself, in the first person singular ("I", never "we" or "us"). Sign off as "Riya".
No placeholders in square brackets.
Use only the facts in the brief. Never invent numbers, dates, links or personal details.
Never include a phone number, address or bank details. Match the language noted in the brief:
for Hinglish, reply in simple, polite Hinglish (Latin script)."""


def _exclusivity_end(profile: Profile) -> str:
    ends = [e["until"] for e in profile.exclusivity if e["until"] >= profile.today]
    return f"{max(ends):%-d %B %Y}" if ends else "the end of my current contract"


def _brief(profile: Profile, deal: Deal) -> str:
    t = deal.terms
    lines = [f"Action: {deal.action}", f"Brand: {t.brand or 'unknown'}", f"Greeting: {_greeting(t)}"]
    if t.deliverables:
        lines.append(f"Deliverables: {describe_deliverables(t)}")
    if t.fee_amount is not None:
        lines.append(f"Their offer: {t.currency} {fmt_inr(t.fee_amount) if t.currency == 'INR' else f'{t.fee_amount:,.0f}'}")
    if t.payment_type == "gifting":
        lines.append("They offer free product only, no fee.")
    if deal.counter_offer_inr:
        lines.append(f"Counter-offer to propose: INR {fmt_inr(deal.counter_offer_inr)} for the same deliverables")
    codes = {f.code for f in deal.flags}
    if codes & {"perpetual_usage", "extended_usage", "whitelisting"}:
        lines.append("Usage: her fee includes 3 months of paid-ad usage. Perpetual usage and whitelisting "
                     "are priced separately; ask what duration they need so she can quote.")
    if deal.action == "decline":
        if "exclusivity_conflict" in codes:
            lines.append("Reason to give: an existing category exclusivity with another brand until "
                         f"{_exclusivity_end(profile)} (do not name that brand). Offer to reconnect after that.")
        else:
            lines.append("Reason to give: the category does not fit her content guidelines. Keep it brief and polite.")
    if "attachment_only" in codes:
        lines.append("Say: please share the key terms in the email body as well as the attachment.")
    if deal.questions:
        lines.append("Ask them for: " + "; ".join(deal.questions))
    lines.append(f"Language: {t.language}")
    return "\n".join(lines)


def _allowed_amounts(deal: Deal) -> set[int]:
    t = deal.terms
    vals = {t.fee_amount, t.fee_inr, deal.counter_offer_inr, t.rate_card_inr, t.gift_value_inr}
    return {int(round(v)) for v in vals if v}


def check_draft(draft: str, deal: Deal) -> list[str]:
    """Reasons a draft must be rejected (empty list means it is fine)."""
    problems = []
    if re.search(r"(\+91[\s-]?)?\b[6-9]\d{9}\b", draft):
        problems.append("contains a phone number")
    if re.search(r"\b(home address|my address|bank account|ifsc|password|otp)\b", draft, re.I):
        problems.append("mentions personal or bank details")
    if re.search(r"https?://", draft):
        problems.append("contains a link")
    allowed = _allowed_amounts(deal)
    for m in re.finditer(r"(?:₹|rs\.?|inr|usd|\$)\s*(\d[\d,]*)(\s*(?:k|lakh))?", draft, re.I):
        value = int(m.group(1).replace(",", ""))
        unit = (m.group(2) or "").strip().lower()
        value *= 1000 if unit == "k" else 100_000 if unit == "lakh" else 1
        if value not in allowed:
            problems.append(f"mentions an amount ({m.group(0)}) that is not in the approved terms")
    # Required content: the draft must carry the point of the reply.
    if deal.counter_offer_inr and fmt_inr(deal.counter_offer_inr) not in draft:
        problems.append("does not state the counter-offer")
    codes = {f.code for f in deal.flags}
    if codes & {"perpetual_usage", "extended_usage", "whitelisting"} and "usage" not in draft.lower():
        problems.append("does not address usage rights")
    if re.search(r"\[[^\]]{2,40}\]", draft):
        problems.append("has a placeholder in brackets")
    if len(draft.split()) > 220:
        problems.append("too long")
    return problems


def _greeting(t: DealTerms) -> str:
    return f"Hi {t.contact.split()[0]}," if t.contact else f"Hi {t.brand or 'there'} team,"


def template_draft(profile: Profile, deal: Deal) -> str:
    t = deal.terms
    hi = _greeting(t)
    what = describe_deliverables(t) or "this collaboration"
    ask = ""
    if deal.questions:
        ask = " Could you also share " + "; ".join(deal.questions) + "?"
    codes = {f.code for f in deal.flags}
    if deal.action == "accept":
        body = f"Thanks for reaching out! I'd be happy to do {what} for {t.brand}. Please send over the brief and contract and we can lock in dates.{ask}"
    elif deal.action == "negotiate":
        parts = [f"Thanks for thinking of me for {t.brand}! I'd love to work on {what}."]
        if deal.counter_offer_inr:
            parts.append(f"For this scope my rate is INR {fmt_inr(deal.counter_offer_inr)}, which includes 3 months of paid-ad usage.")
        elif "gifting_only" in codes:
            parts.append("I only take on paid collaborations for deliverables like these.")
        if codes & {"perpetual_usage", "extended_usage", "whitelisting"}:
            parts.append("My fee covers 3 months of paid-ad usage; perpetual usage and whitelisting are priced separately. "
                         "How long would you need them? I'll send a quote.")
        body = " ".join(parts) + ask
    elif deal.action == "ask_for_details":
        body = f"Thanks for getting in touch about {t.brand or 'a collaboration'}! Before I can confirm, could you share {'; '.join(deal.questions) or 'a few more details'}?"
        if "attachment_only" in codes:
            body = "Thanks for sending this over! Could you also share the key terms in the email itself: " + "; ".join(deal.questions) + "?"
    elif deal.action == "decline":
        if "exclusivity_conflict" in codes:
            body = ("Thank you for the offer! I currently have a category exclusivity with another brand until "
                    f"{_exclusivity_end(profile)}, so I can't take this on right now. I'd be glad to reconnect after that.")
        else:
            body = "Thank you for thinking of me. This category isn't a fit for my content, so I'll pass this time. Wishing you the best with the campaign."
    else:
        return ""
    return f"{hi}\n\n{body}\n\nBest,\nRiya"


def write_draft(profile: Profile, deal: Deal, llm: LLMClient | None) -> tuple[str | None, str]:
    if deal.action in ("ignore", "report"):
        return None, "none"
    if llm is not None:
        try:
            draft = llm.chat_text(DRAFT_SYSTEM, _brief(profile, deal))
            if draft and not check_draft(draft, deal):
                return draft, "llm"
        except LLMError:
            pass
    return template_draft(profile, deal), "template"
