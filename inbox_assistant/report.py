"""The creator-facing summary. Built by plain code from the deal objects so it never
says anything the pipeline did not decide."""

from __future__ import annotations

from datetime import date

from .actions import describe_deliverables
from .money import fmt_inr
from .models import Deal
from .rules import Profile

ACTION_LABEL = {
    "accept": "Accept",
    "negotiate": "Negotiate",
    "ask_for_details": "Ask for details",
    "decline": "Decline",
    "ignore": "Ignore",
    "report": "Report as scam",
}


def _money(deal: Deal) -> str:
    t = deal.terms
    if t.fee_amount is None:
        if t.payment_type == "gifting":
            return f"free product (worth Rs {fmt_inr(t.gift_value_inr)})" if t.gift_value_inr else "free product only"
        return "no fee stated"
    if t.currency == "USD":
        return f"USD {t.fee_amount:,.0f} (about Rs {fmt_inr(t.fee_inr)})"
    s = f"Rs {fmt_inr(t.fee_amount)}"
    if len(t.fee_history_inr) > 1 and t.fee_history_inr[0] != t.fee_history_inr[-1]:
        s += f" (first offered Rs {fmt_inr(t.fee_history_inr[0])})"
    return s


def _dates(deal: Deal) -> str:
    t = deal.terms
    if t.campaign_start and t.deadline and t.campaign_start != t.deadline:
        return f"live {t.campaign_start:%-d}–{t.deadline:%-d %b}"
    if t.deadline:
        return f"live by {t.deadline:%-d %b}"
    return "no dates yet"


def _urgency(deal: Deal, today: date) -> int:
    t = deal.terms
    d = t.campaign_start or t.deadline
    return (d - today).days if d else 999


def _title(deal: Deal) -> str:
    t = deal.terms
    name = t.brand or deal.terms.contact_email or deal.deal_id
    if t.agency:
        name += f" (via {t.agency})"
    return name


def _deal_block(deal: Deal, today: date, with_draft: bool = True) -> list[str]:
    t = deal.terms
    lines = [f"### {_title(deal)}  ·  {ACTION_LABEL[deal.action]}"]
    facts = [x for x in (describe_deliverables(t), _money(deal), _dates(deal)) if x]
    lines.append(f"{' · '.join(facts)}  ")
    lines.append(f"Emails: {', '.join(deal.email_ids)}")
    if deal.terms.rate_card_inr:
        lines.append(f"Your rate card for this: Rs {fmt_inr(deal.terms.rate_card_inr)}")
    lines.append("")
    for f in deal.flags:
        icon = {"high": "🔴", "medium": "🟠", "low": "⚪"}[f.severity]
        lines.append(f"- {icon} {f.reason}")
    lines.append(f"- **Next step:** {ACTION_LABEL[deal.action]}. {deal.action_reason if deal.action != 'negotiate' else ''}".rstrip())
    if deal.counter_offer_inr:
        lines.append(f"- **Suggested counter:** Rs {fmt_inr(deal.counter_offer_inr)}")
    if with_draft and deal.draft_reply:
        lines += ["", "<details><summary>Draft reply (review before sending)</summary>", "",
                  *[f"> {ln}" if ln else ">" for ln in deal.draft_reply.splitlines()], "", "</details>"]
    lines.append("")
    return lines


def render_summary(profile: Profile, deals: list[Deal]) -> str:
    today = profile.today
    name = profile.data.get("name", "").split()[0] or "there"
    by = lambda *cats: [d for d in deals if d.category in cats]  # noqa: E731
    live = sorted([d for d in by("deal_offer")], key=lambda d: _urgency(d, today))
    info = by("needs_more_info")
    no = by("not_a_fit")
    scams = by("scam_or_phishing")
    spam = by("spam")
    n_emails = sum(len(d.email_ids) for d in deals)

    out = [f"# Your brand inbox, {today:%-d %B %Y}", ""]
    def n(count: int, one: str, many: str) -> str:
        return f"{count} {one if count == 1 else many}"

    out.append(f"Hi {name}. {n(n_emails, 'email', 'emails')}, {n(len(live) + len(info), 'possible deal', 'possible deals')}. "
               f"{n(len(live), 'needs', 'need')} a decision, {n(len(info), 'needs', 'need')} more details, "
               f"{len(no)} to turn down, {n(len(scams), 'scam', 'scams')}, {len(spam)} you can ignore.")
    out.append("")

    soon = [d for d in live if _urgency(d, today) <= 7]
    if soon:
        out.append("**Today:** " + "; ".join(
            f"{_title(d)}: {ACTION_LABEL[d.action].lower()} ({_dates(d)})" for d in soon) + ".")
        out.append("")
    if scams:
        verb = " is a scam or phishing." if len(scams) == 1 else " are scams or phishing."
        out.append("**Careful:** " + ", ".join(d.email_ids[0] for d in scams)
                   + verb + " Don't click links, pay anything or share personal details.")
        out.append("")

    if live:
        out += ["## Deals to decide", ""]
        for d in live:
            out += _deal_block(d, today)
    if info:
        out += ["## Need more details", ""]
        for d in info:
            out += _deal_block(d, today)
    if no:
        out += ["## Not a fit (polite no)", ""]
        for d in no:
            out += _deal_block(d, today)
    if scams:
        out += ["## Scams and phishing (report, don't reply)", ""]
        for d in scams:
            out.append(f"- **{d.email_ids[0]}** {d.terms.contact_email or ''}: " + " ".join(f.reason for f in d.flags))
        out.append("")
    if spam:
        out += ["## Ignore", ""]
        for d in spam:
            out.append(f"- **{d.email_ids[0]}** {d.summary or 'Newsletter or marketing email.'}")
        out.append("")
    out.append("_Drafts are suggestions. Nothing has been sent._")
    return "\n".join(out)
