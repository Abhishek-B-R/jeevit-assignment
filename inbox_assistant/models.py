"""Data shapes shared across the pipeline."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Category = Literal["deal_offer", "needs_more_info", "not_a_fit", "scam_or_phishing", "spam"]
Action = Literal["accept", "negotiate", "ask_for_details", "decline", "ignore", "report"]
Severity = Literal["high", "medium", "low"]


class Email(BaseModel):
    id: str
    received_at: datetime
    sender: str = Field(alias="from")
    subject: str
    body: str

    model_config = {"populate_by_name": True}

    @property
    def sender_domain(self) -> str:
        return self.sender.rsplit("@", 1)[-1].lower()


class Deliverable(BaseModel):
    platform: Literal["instagram", "youtube", "other"] = "other"
    format: Literal["reel", "story", "post", "integration", "dedicated_video", "shoutout", "other"] = "other"
    count: int = 1

    @field_validator("platform", mode="before")
    @classmethod
    def _platform(cls, v):
        v = (v or "other").lower()
        if "insta" in v:
            return "instagram"
        if "you" in v or v == "yt":
            return "youtube"
        return v if v in ("instagram", "youtube") else "other"

    @field_validator("format", mode="before")
    @classmethod
    def _format(cls, v):
        v = (v or "other").lower().replace(" ", "_")
        for key in ("reel", "story", "post", "integration", "shoutout"):
            if key in v:
                return key
        if "dedicated" in v:
            return "dedicated_video"
        return "other"

    @field_validator("count", mode="before")
    @classmethod
    def _count(cls, v):
        try:
            return max(1, int(v))
        except (TypeError, ValueError):
            return 1


class UsageRights(BaseModel):
    paid_ads_months: int | None = None
    perpetual: bool = False
    whitelisting: bool = False
    description: str | None = None


class Extraction(BaseModel):
    """What the model reads out of one email. Facts only, no judgement."""

    email_kind: Literal[
        "brand_outreach", "account_or_platform_notice", "newsletter_or_marketing", "other"
    ] = "other"
    brand: str | None = None
    on_behalf_of_brand: str | None = None  # set when an agency writes for a brand
    sender_role: Literal["brand", "agency", "unknown"] = "unknown"
    agency_name: str | None = None
    contact_name: str | None = None
    product: str | None = None
    product_category: str | None = None
    deliverables: list[Deliverable] = Field(default_factory=list)
    refers_to_previous_terms: bool = False  # "same deliverables", "following up"
    adds_to_previous_deliverables: bool = False  # "add 2 stories to the reel"
    fee_amount: float | None = None
    currency: Literal["INR", "USD"] | None = None
    payment_type: Literal["cash", "gifting", "commission", "unknown"] = "unknown"
    gift_value_inr: float | None = None
    campaign_start: date | None = None
    deadline: date | None = None
    usage_rights: UsageRights = Field(default_factory=UsageRights)
    asks_creator_to_pay: bool = False
    asks_for_personal_info: bool = False
    info_requested_from_creator: list[str] = Field(default_factory=list)
    mentions_attachment: bool = False
    language: str = "English"
    one_line_summary: str = ""

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, v):
        if not v:
            return None
        v = str(v).upper()
        if v in ("RS", "RS.", "₹", "INR", "RUPEES"):
            return "INR"
        if v in ("$", "USD", "US$"):
            return "USD"
        return None

    @field_validator("campaign_start", "deadline", mode="before")
    @classmethod
    def _date(cls, v):
        if not v or (isinstance(v, str) and v.strip().lower() in ("null", "none", "")):
            return None
        try:
            return date.fromisoformat(str(v)[:10])
        except ValueError:
            return None

    @field_validator("payment_type", "email_kind", "sender_role", mode="before")
    @classmethod
    def _lower(cls, v):
        return (v or "unknown").lower() if isinstance(v, str) else v

    @field_validator("info_requested_from_creator", mode="before")
    @classmethod
    def _list(cls, v):
        if v is None:
            return []
        return [v] if isinstance(v, str) else v


class Flag(BaseModel):
    code: str
    severity: Severity
    reason: str


class Signals(BaseModel):
    """Deterministic checks on the raw text. The model cannot switch these off."""

    prompt_injection: bool = False
    pay_to_join: bool = False
    phishing_link: bool = False
    lookalike_sender: bool = False
    asks_personal_info: bool = False
    urgency: bool = False
    newsletter: bool = False
    free_email_sender: bool = False
    mentions_attachment: bool = False
    links: list[str] = Field(default_factory=list)
    amounts: list[float] = Field(default_factory=list)
    dates: list[date] = Field(default_factory=list)
    evidence: dict[str, str] = Field(default_factory=dict)


class DealTerms(BaseModel):
    """Terms after merging every email in a thread, latest wins."""

    brand: str | None = None
    agency: str | None = None
    contact: str | None = None
    contact_email: str | None = None
    product: str | None = None
    product_category: str | None = None
    deliverables: list[Deliverable] = Field(default_factory=list)
    fee_amount: float | None = None
    currency: Literal["INR", "USD"] | None = None
    fee_inr: float | None = None
    payment_type: str = "unknown"
    gift_value_inr: float | None = None
    campaign_start: date | None = None
    deadline: date | None = None
    usage_rights: UsageRights = Field(default_factory=UsageRights)
    info_requested_from_creator: list[str] = Field(default_factory=list)
    rate_card_inr: float | None = None
    fee_history_inr: list[float] = Field(default_factory=list)
    language: str = "English"


class Deal(BaseModel):
    deal_id: str
    email_ids: list[str]
    category: Category
    terms: DealTerms
    flags: list[Flag]
    action: Action
    action_reason: str
    counter_offer_inr: float | None = None
    questions: list[str] = Field(default_factory=list)
    draft_reply: str | None = None
    draft_source: Literal["llm", "template", "none"] = "none"
    summary: str = ""


class EmailResult(BaseModel):
    id: str
    deal_id: str
    category: Category
    extraction: Extraction
    signals: Signals
    flags: list[Flag]
    seconds: float
