"""Inbox in, deals out: signals -> extraction -> verification -> grouping -> rules -> actions."""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from typing import Callable

from .actions import choose_action, write_draft
from .llm import LLMClient
from .models import Deal, DealTerms, Email, EmailResult, Extraction, Flag, Signals
from .rules import Profile, decide_category, deal_flags, scam_flags, verify_extraction
from .signals import compute_signals, is_scam

Extractor = Callable[[Email], Extraction]


def brand_key(name: str | None) -> str | None:
    if not name:
        return None
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    for suffix in ("official", "india", "inc", "pvtltd", "ltd", "co"):
        if key.endswith(suffix) and len(key) > len(suffix) + 2:
            key = key[: -len(suffix)]
    return key or None


def merge_terms(profile: Profile, items: list[tuple[Email, Extraction]]) -> DealTerms:
    """Fold a thread into one set of terms. Later emails override earlier ones."""
    t = DealTerms()
    for email, ex in sorted(items, key=lambda p: p[0].received_at):
        t.brand = ex.on_behalf_of_brand or ex.brand or t.brand
        if ex.agency_name:
            t.agency = ex.agency_name
        t.contact = ex.contact_name or t.contact
        t.contact_email = email.sender
        t.product = ex.product or t.product
        t.product_category = ex.product_category or t.product_category
        if ex.deliverables and ex.adds_to_previous_deliverables and t.deliverables:
            t.deliverables = t.deliverables + ex.deliverables
        elif ex.deliverables:
            t.deliverables = ex.deliverables
        if ex.fee_amount is not None:
            t.fee_amount, t.currency = ex.fee_amount, ex.currency or "INR"
            t.fee_inr = profile.to_inr(t.fee_amount, t.currency)
            t.fee_history_inr.append(t.fee_inr)
        if ex.payment_type != "unknown":
            t.payment_type = ex.payment_type
        t.gift_value_inr = ex.gift_value_inr or t.gift_value_inr
        t.campaign_start = ex.campaign_start or t.campaign_start
        t.deadline = ex.deadline or t.deadline
        u = ex.usage_rights
        if u.paid_ads_months is not None or u.perpetual or u.whitelisting or u.description:
            t.usage_rights = u
        if ex.info_requested_from_creator:
            t.info_requested_from_creator = ex.info_requested_from_creator
        t.language = ex.language or t.language
    if t.payment_type == "unknown" and t.fee_inr is not None:
        t.payment_type = "cash"
    t.rate_card_inr = profile.rate_card(t.deliverables)
    return t


def _agency_unverified(items: list[tuple[Email, Extraction]]) -> bool:
    # Needs a named agency or a brand it writes for; the model's bare "agency" guess is not enough.
    agency_domains = {e.sender_domain for e, x in items if x.agency_name or x.on_behalf_of_brand}
    return bool(agency_domains)


def _thread_text(items: list[tuple[Email, Extraction]]) -> str:
    return "\n".join(f"{e.subject}\n{e.body}" for e, _ in items)


class Pipeline:
    def __init__(self, profile: Profile, llm: LLMClient | None, extractor: Extractor | None = None,
                 llm_drafts: bool = True):
        self.profile = profile
        self.llm = llm
        self.llm_drafts = llm_drafts and llm is not None
        if extractor is None:
            from .extract import extract

            assert llm is not None
            extractor = lambda e: extract(e, llm)  # noqa: E731
        self.extractor = extractor

    def run(self, emails: list[Email], progress: Callable[[str], None] | None = None):
        log = progress or (lambda _m: None)
        parsed: list[tuple[Email, Extraction, Signals, list[Flag], float]] = []
        for email in sorted(emails, key=lambda e: e.received_at):
            t0 = time.perf_counter()
            sig = compute_signals(email)
            ex = self.extractor(email)
            vflags = verify_extraction(ex, sig)
            secs = time.perf_counter() - t0
            parsed.append((email, ex, sig, vflags, secs))
            log(f"  {email.id}  read in {secs:5.1f}s  {ex.one_line_summary[:70]}")

        # Group. Scams never join a real deal: an impersonator must not inherit its trust.
        groups: "OrderedDict[str, list[int]]" = OrderedDict()
        for i, (email, ex, sig, _vf, _s) in enumerate(parsed):
            scammy = is_scam(sig)
            key = None if scammy else brand_key(ex.on_behalf_of_brand or ex.brand)
            if key is None and not scammy and email.subject.lower().startswith("re:"):
                key = next((k for k, idx in groups.items()
                            if any(parsed[j][0].sender == email.sender for j in idx)), None)
            groups.setdefault(key or f"email-{email.id}", []).append(i)

        deals: list[Deal] = []
        results: list[EmailResult] = []
        for key, idxs in groups.items():
            members = [parsed[i] for i in idxs]
            deal_id = f"D-{key}" if not key.startswith("email-") else f"D-{members[0][0].id}"
            # Per-email category reflects the thread as it stood when that email arrived.
            for n in range(1, len(members) + 1):
                email, ex, sig, vflags, secs = members[n - 1]
                cat, flags, _terms = self._judge(members[:n])
                results.append(EmailResult(id=email.id, deal_id=deal_id, category=cat, extraction=ex,
                                           signals=sig, flags=flags, seconds=round(secs, 2)))
            cat, flags, terms = self._judge(members)
            action, reason, counter, questions = choose_action(self.profile, cat, terms, flags)
            deal = Deal(deal_id=deal_id, email_ids=[m[0].id for m in members], category=cat, terms=terms,
                        flags=flags, action=action, action_reason=reason, counter_offer_inr=counter,
                        questions=questions, summary=members[-1][1].one_line_summary)
            deal.draft_reply, deal.draft_source = write_draft(self.profile, deal, self.llm if self.llm_drafts else None)
            deals.append(deal)
        results.sort(key=lambda r: r.id)
        return deals, results

    def _judge(self, members):
        items = [(m[0], m[1]) for m in members]
        email, ex, sig = members[-1][0], members[-1][1], members[-1][2]
        terms = merge_terms(self.profile, items)
        flags = scam_flags(sig, ex)
        if not flags:
            flags = deal_flags(self.profile, terms, _thread_text(items), sig, _agency_unverified(items))
        for m in members:
            flags += [f for f in m[3] if f.code not in {x.code for x in flags}]
        cat = decide_category(ex, sig, terms, flags)
        if cat == "spam":
            flags = []
        elif cat == "scam_or_phishing":
            flags = scam_flags(sig, ex) or [Flag(
                code="suspicious_notice", severity="high",
                reason="An urgent account warning with a link, not from the platform's real domain. "
                       "Real platforms warn you inside the app. Do not click.")]
        return cat, flags, terms
