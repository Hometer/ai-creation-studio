"""Local CNY estimates and planning allocations, never permission to spend."""

from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from api.schemas.assets import Contract, Id, ProjectId
from api.schemas.generation_plans import Digest, digest
from api.schemas.generation_tasks import Amount

Time = Annotated[int, Field(gt=0, le=4_102_444_800)]
Rate = Annotated[int, Field(gt=0, le=1_000_000)]
Model = Literal["wan2.7-i2v", "wan2.7-i2v-2026-04-25"]


class ClosedContract(Contract):
    @model_validator(mode="before")
    @classmethod
    def exact_boundaries(cls, value):
        if isinstance(value, dict):
            for key, expected in (("preparation_only", True), ("real_execution_allowed", False),
                                  ("real_money_reserved", False), ("account_price_verified", False)):
                if key in value and value[key] is not expected:
                    raise ValueError("Invalid preparation boundary")
            if "submit_attempts" in value and (type(value["submit_attempts"]) is not int or value["submit_attempts"] != 0):
                raise ValueError("Premature submission")
        return value


class PriceInput(ClosedContract):
    model: Model
    source_kind: Literal["manual_account", "offline_fixture"]
    rate_720p_minor: Rate
    rate_1080p_minor: Rate
    expires_at: Time


class Price(PriceInput):
    price_id: Id
    created_at: Time
    account_price_verified: Literal[False] = False


class PriceView(Price):
    current: bool
    expired: bool
    real_execution_allowed: Literal[False] = False


class BudgetInput(ClosedContract):
    source_kind: Literal["manual_setting", "offline_fixture"]
    single_limit_minor: Amount
    daily_limit_minor: Amount
    monthly_limit_minor: Amount
    preparation_only: Literal[True]

    @model_validator(mode="after")
    def ordered_limits(self):
        if not self.single_limit_minor <= self.daily_limit_minor <= self.monthly_limit_minor:
            raise ValueError("Invalid limit hierarchy")
        return self


class BudgetPolicy(BudgetInput):
    budget_id: Id
    created_at: Time


class BudgetView(ClosedContract):
    policy: BudgetPolicy | None
    day: str
    month: str
    daily_planning_held_minor: Amount
    monthly_planning_held_minor: Amount
    daily_available_minor: Amount | None
    monthly_available_minor: Amount | None
    actual_charged_minor: None = None
    real_money_reserved: Literal[False] = False
    real_execution_allowed: Literal[False] = False


class ReviewInput(ClosedContract):
    content_hash: Digest
    rights_confirmed: bool
    non_sensitive_confirmed: bool
    mainland_only: bool
    preparation_only: Literal[True]


class SampleReview(ReviewInput):
    review_id: Id
    plan_id: Id
    reviewed_at: Time
    accepted: bool

    @model_validator(mode="after")
    def attestation(self):
        if self.accepted != (self.rights_confirmed and self.non_sensitive_confirmed and self.mainland_only):
            raise ValueError("Invalid sample attestation")
        return self


class ReviewView(SampleReview):
    current: bool
    real_execution_allowed: Literal[False] = False


class QuoteInput(Contract):
    plan_id: Id


class QuoteLine(Contract):
    segment_id: ProjectId
    duration: int = Field(ge=1, le=60)


class QuoteTerms(Contract):
    plan_id: Id
    project_id: ProjectId
    content_hash: Digest
    model: str = Field(min_length=1, max_length=128)
    resolution: str = Field(min_length=1, max_length=16)
    lines: list[QuoteLine] = Field(min_length=1, max_length=20)
    price_id: Id | None
    pricing_kind: Literal["unavailable", "manual_estimate", "offline_fixture"]
    rate_minor: Rate | None
    amount_minor: Amount | None
    currency: Literal["CNY"] | None
    expires_at: Time

    @model_validator(mode="after")
    def price_math(self):
        if len({line.segment_id for line in self.lines}) != len(self.lines):
            raise ValueError("Duplicate quote target")
        if self.pricing_kind == "unavailable":
            if any(v is not None for v in (self.price_id, self.rate_minor, self.amount_minor, self.currency)):
                raise ValueError("Unknown price cannot be zero")
        elif (self.price_id is None or self.rate_minor is None or self.currency != "CNY"
              or self.amount_minor != sum(line.duration for line in self.lines) * self.rate_minor):
            raise ValueError("Invalid estimate")
        return self


class Quote(Contract):
    quote_id: Id
    created_at: Time
    terms: QuoteTerms
    quote_hash: Digest

    @model_validator(mode="after")
    def correct_hash(self):
        if self.quote_hash != digest(self.terms.model_dump(mode="json")):
            raise ValueError("Quote digest mismatch")
        return self


class QuoteView(Quote, ClosedContract):
    current: bool
    expired: bool
    blockers: list[str]
    account_price_verified: Literal[False] = False
    real_execution_allowed: Literal[False] = False


class PreparationInput(ClosedContract):
    quote_id: Id
    quote_hash: Digest
    review_id: Id
    budget_id: Id
    request_id: Id
    preparation_only: Literal[True]


class Preparation(PreparationInput):
    preparation_id: Id
    created_at: Time
    updated_at: Time
    revision: int = Field(ge=1, le=2)
    state: Literal["prepared", "cancelled"] = "prepared"
    planning_held_minor: Amount
    period_day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    period_month: str = Field(pattern=r"^\d{4}-\d{2}$")
    submit_attempts: Literal[0] = 0
    provider_task_id: None = None
    actual_charged_minor: None = None
    real_money_reserved: Literal[False] = False

    @model_validator(mode="after")
    def state_graph(self):
        if type(self.submit_attempts) is not int or self.submit_attempts != 0:
            raise ValueError("Premature submission")
        if self.period_day[:7] != self.period_month or self.revision != (1 if self.state == "prepared" else 2):
            raise ValueError("Invalid preparation history")
        return self


class PreparationView(Preparation):
    local_preparation_ready: bool
    active_planning_held_minor: Amount
    pricing_kind: Literal["manual_estimate", "offline_fixture"]
    blockers: list[str]
    execution_blockers: list[str]
    real_execution_allowed: Literal[False] = False


class CancelInput(Contract):
    expected_revision: int = Field(ge=1, le=2)


class ReadinessDocument(Contract):
    schema_version: Literal[1] = 1
    prices: dict[Id, Price] = Field(default_factory=dict)
    budgets: dict[Id, BudgetPolicy] = Field(default_factory=dict)
    reviews: dict[Id, SampleReview] = Field(default_factory=dict)
    quotes: dict[Id, Quote] = Field(default_factory=dict)
    preparations: dict[Id, Preparation] = Field(default_factory=dict)
    current_price_id: Id | None = None
    current_budget_id: Id | None = None
    current_reviews: dict[Id, Id] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def no_missing_checkpoints(cls, value):
        if isinstance(value, dict):
            for key, model in (("prices", Price), ("budgets", BudgetPolicy), ("reviews", SampleReview),
                               ("quotes", Quote), ("preparations", Preparation)):
                entries = value.get(key, {})
                if isinstance(entries, dict):
                    for entry in entries.values():
                        if isinstance(entry, dict) and set(entry) != set(model.model_fields):
                            raise ValueError("Incomplete preparation store")
        return value

    @model_validator(mode="after")
    def graph(self):
        for entries, field in ((self.prices, "price_id"), (self.budgets, "budget_id"), (self.reviews, "review_id"),
                               (self.quotes, "quote_id"), (self.preparations, "preparation_id")):
            if any(key != getattr(record, field) for key, record in entries.items()):
                raise ValueError("Record identity mismatch")
        if ((self.current_price_id is not None and self.current_price_id not in self.prices)
                or (self.current_budget_id is not None and self.current_budget_id not in self.budgets)):
            raise ValueError("Missing current settings")
        for plan_id, review_id in self.current_reviews.items():
            if review_id not in self.reviews or self.reviews[review_id].plan_id != plan_id:
                raise ValueError("Invalid current sample review")
        for quote in self.quotes.values():
            terms = quote.terms
            if terms.price_id is not None:
                price = self.prices.get(terms.price_id)
                if price is None or terms.model != price.model or terms.resolution not in {"720P", "1080P"}:
                    raise ValueError("Missing price basis")
                rate = price.rate_720p_minor if terms.resolution == "720P" else price.rate_1080p_minor
                kind = "offline_fixture" if price.source_kind == "offline_fixture" else "manual_estimate"
                if terms.rate_minor != rate or terms.pricing_kind != kind or terms.expires_at > price.expires_at:
                    raise ValueError("Price snapshot mismatch")
        request_ids, quote_ids = set(), set()
        for record in self.preparations.values():
            quote, review, budget = self.quotes.get(record.quote_id), self.reviews.get(record.review_id), self.budgets.get(record.budget_id)
            if (quote is None or review is None or budget is None or not review.accepted
                    or record.quote_hash != quote.quote_hash or quote.terms.amount_minor is None
                    or review.plan_id != quote.terms.plan_id or review.content_hash != quote.terms.content_hash
                    or record.planning_held_minor != quote.terms.amount_minor
                    or record.planning_held_minor > budget.single_limit_minor
                    or (quote.terms.pricing_kind == "offline_fixture") != (budget.source_kind == "offline_fixture")
                    or record.request_id in request_ids or record.quote_id in quote_ids):
                raise ValueError("Invalid preparation linkage")
            stamp = datetime.fromtimestamp(record.created_at, ZoneInfo("Asia/Shanghai"))
            if (record.period_day != stamp.strftime("%Y-%m-%d") or record.period_month != stamp.strftime("%Y-%m")
                    or record.created_at >= quote.terms.expires_at or record.created_at < quote.created_at
                    or record.created_at < review.reviewed_at or record.created_at < budget.created_at
                    or record.updated_at < record.created_at):
                raise ValueError("Invalid preparation time")
            request_ids.add(record.request_id)
            quote_ids.add(record.quote_id)
        return self
