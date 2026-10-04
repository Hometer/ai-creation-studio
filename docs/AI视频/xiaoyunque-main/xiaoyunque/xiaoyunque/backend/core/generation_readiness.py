"""Atomic local planning allocations; no transport, currency settlement or SDK."""

import time
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from api.schemas.generation_plans import digest
from api.schemas.generation_readiness import (
    BudgetPolicy, BudgetView, Preparation, PreparationView, Price, PriceView, Quote,
    QuoteLine, QuoteTerms, QuoteView, ReadinessDocument, ReviewView, SampleReview,
)
from core.asset_library import AssetLibraryError
from core.media_archive import sync_directory
from core.model_preflight import inspect_target
from core.persistence import atomic_write_session
from core.task_provider import read_document


def periods(timestamp):
    date = datetime.fromtimestamp(timestamp, ZoneInfo("Asia/Shanghai"))
    return date.strftime("%Y-%m-%d"), date.strftime("%Y-%m")


def conflict(code, message="准备条件未通过，请核对当前报价、额度、样例和计划"):
    return AssetLibraryError(409, code, message)


class GenerationReadiness:
    def __init__(self, plans, connection, clock=time.time):
        self.plans, self.connection, self.clock = plans, connection, clock
        self.path = plans.library.directory / "generation-readiness.json"

    def _read(self):
        try:
            return read_document(self.path, ReadinessDocument)
        except (OSError, ValueError):
            raise AssetLibraryError(503, "READINESS_STORE_UNAVAILABLE", "调用准备记录不可用，请保留原文件") from None

    def _save(self, data):
        try:
            value = data.model_dump(mode="json")
            ReadinessDocument.model_validate(value)
            atomic_write_session(self.path, value)
            sync_directory(self.path.parent)
        except (OSError, ValueError):
            raise AssetLibraryError(503, "READINESS_STORE_UNAVAILABLE", "调用准备记录不可用，请保留原文件") from None

    @staticmethod
    def _get(records, identifier):
        record = records.get(identifier)
        if record is None:
            raise AssetLibraryError(404, "READINESS_NOT_FOUND", "报价、预算、审核或准备记录不存在")
        return record

    def _price_view(self, data, price):
        return PriceView(**price.model_dump(), current=price.price_id == data.current_price_id,
                         expired=int(self.clock()) >= price.expires_at)

    def price(self, payload):
        with self.plans._transaction():
            if payload.expires_at <= int(self.clock()):
                raise conflict("PRICE_EXPIRED", "价格依据已过期，请核对有效期")
            data = self._read()
            price = Price(**payload.model_dump(), price_id=uuid4().hex, created_at=int(self.clock()))
            data.prices[price.price_id], data.current_price_id = price, price.price_id
            self._save(data)
            return self._price_view(data, price)

    def get_price(self, identifier):
        with self.plans._transaction():
            data = self._read()
            return self._price_view(data, self._get(data.prices, identifier))

    def _held(self, data, exclude=None):
        day, month = periods(self.clock())
        active = [p for p in data.preparations.values() if p.state == "prepared" and p.preparation_id != exclude]
        return (sum(p.planning_held_minor for p in active if p.period_day == day),
                sum(p.planning_held_minor for p in active if p.period_month == month))

    def _budget_view(self, data):
        policy = data.budgets.get(data.current_budget_id)
        daily, monthly = self._held(data)
        day, month = periods(self.clock())
        if policy and (daily > policy.daily_limit_minor or monthly > policy.monthly_limit_minor):
            raise AssetLibraryError(503, "READINESS_STORE_UNAVAILABLE", "本地额度分配关系异常，请保留数据")
        return BudgetView(policy=policy, day=day, month=month, daily_planning_held_minor=daily,
            monthly_planning_held_minor=monthly, daily_available_minor=policy.daily_limit_minor - daily if policy else None,
            monthly_available_minor=policy.monthly_limit_minor - monthly if policy else None)

    def budget(self, payload):
        with self.plans._transaction():
            data = self._read()
            daily, monthly = self._held(data)
            if payload.daily_limit_minor < daily or payload.monthly_limit_minor < monthly:
                raise conflict("BUDGET_BELOW_HELD", "新上限低于本日或本月已分配额度，请先核对并取消本地准备")
            policy = BudgetPolicy(**payload.model_dump(), budget_id=uuid4().hex, created_at=int(self.clock()))
            data.budgets[policy.budget_id], data.current_budget_id = policy, policy.budget_id
            self._save(data)
            return policy

    def get_budget(self):
        with self.plans._transaction():
            return self._budget_view(self._read())

    def _plan_view(self, plans, library, plan_id):
        plan = self.plans._get(plans, plan_id)
        view = self.plans._view(plan, library, self.plans.engine.get_status_snapshot(plan.content.project_id))
        return plan, view

    def _review_view(self, data, review, plans, library):
        plan, view = self._plan_view(plans, library, review.plan_id)
        return ReviewView(**review.model_dump(), current=(data.current_reviews.get(review.plan_id) == review.review_id
                            and plan.content_hash == review.content_hash and view.current))

    def review(self, plan_id, payload):
        with self.plans._transaction() as (library, plans):
            plan, view = self._plan_view(plans, library, plan_id)
            if plan.content_hash != payload.content_hash or not view.current:
                raise conflict("PLAN_SOURCE_CHANGED", "计划摘要或来源已变化，请查看当前素材后重新审核")
            data = self._read()
            review = SampleReview(**payload.model_dump(), review_id=uuid4().hex, plan_id=plan_id,
                reviewed_at=int(self.clock()), accepted=payload.rights_confirmed and payload.non_sensitive_confirmed and payload.mainland_only)
            data.reviews[review.review_id], data.current_reviews[plan_id] = review, review.review_id
            self._save(data)
            return self._review_view(data, review, plans, library)

    def get_review(self, identifier):
        with self.plans._transaction() as (library, plans):
            data = self._read()
            return self._review_view(data, self._get(data.reviews, identifier), plans, library)

    def _quote_view(self, data, quote, plans, library):
        plan, view = self._plan_view(plans, library, quote.terms.plan_id)
        price = data.prices.get(quote.terms.price_id)
        blockers = []
        if plan.confirmation is None: blockers.append("PLAN_NOT_CONFIRMED")
        if not view.current or plan.content_hash != quote.terms.content_hash: blockers.append("PLAN_SOURCE_CHANGED")
        checks = [inspect_target(plan.content, target, self.plans.archive, False)[0] for target in plan.content.targets]
        if not all(c.input_protocol_ready for c in checks): blockers.append("INPUT_PROTOCOL_INVALID")
        if price is None: blockers.append("PRICE_UNAVAILABLE")
        elif price.price_id != data.current_price_id: blockers.append("PRICE_CHANGED")
        if price is not None and int(self.clock()) >= price.expires_at: blockers.append("PRICE_EXPIRED")
        expired = int(self.clock()) >= quote.terms.expires_at
        if expired: blockers.append("QUOTE_EXPIRED")
        current = view.current and plan.content_hash == quote.terms.content_hash and (price is not None and price.price_id == data.current_price_id)
        return QuoteView(**quote.model_dump(), current=bool(current), expired=expired, blockers=blockers)

    def quote(self, payload):
        with self.plans._transaction() as (library, plans):
            plan, _ = self._plan_view(plans, library, payload.plan_id)
            data = self._read()
            price = data.prices.get(data.current_price_id)
            options = plan.content.options
            supported = (price is not None and price.expires_at > int(self.clock()) and price.model == options.model
                and options.resolution in {"720P", "1080P"} and options.mode in {"first_frame", "start_end_frame"}
                and options.sound == "on"
                and all(2 <= t.duration <= 15 for t in plan.content.targets))
            rate = (price.rate_720p_minor if options.resolution == "720P" else price.rate_1080p_minor) if supported else None
            lines = [QuoteLine(segment_id=t.segment_id, duration=t.duration) for t in plan.content.targets]
            terms = QuoteTerms(plan_id=plan.plan_id, project_id=plan.content.project_id, content_hash=plan.content_hash,
                model=options.model, resolution=options.resolution, lines=lines, price_id=price.price_id if supported else None,
                pricing_kind=("offline_fixture" if price.source_kind == "offline_fixture" else "manual_estimate") if supported else "unavailable",
                rate_minor=rate, amount_minor=sum(t.duration for t in plan.content.targets) * rate if supported else None,
                currency="CNY" if supported else None, expires_at=min(int(self.clock()) + 1200, price.expires_at) if supported else int(self.clock()) + 1200)
            quote = Quote(quote_id=uuid4().hex, created_at=int(self.clock()), terms=terms, quote_hash=digest(terms.model_dump(mode="json")))
            data.quotes[quote.quote_id] = quote
            self._save(data)
            return self._quote_view(data, quote, plans, library)

    def get_quote(self, identifier):
        with self.plans._transaction() as (library, plans):
            data = self._read()
            return self._quote_view(data, self._get(data.quotes, identifier), plans, library)

    def _checks(self, data, quote, review, budget, plans, library, exclude=None):
        blockers = self._quote_view(data, quote, plans, library).blockers
        if (not review.accepted or review.plan_id != quote.terms.plan_id or review.content_hash != quote.terms.content_hash):
            blockers.append("SAMPLE_REVIEW_REQUIRED")
        if data.current_reviews.get(quote.terms.plan_id) != review.review_id: blockers.append("SAMPLE_REVIEW_CHANGED")
        if data.current_budget_id != budget.budget_id: blockers.append("BUDGET_CHANGED")
        if (quote.terms.pricing_kind == "offline_fixture") != (budget.source_kind == "offline_fixture"):
            blockers.append("PRICING_SOURCE_MISMATCH")
        daily, monthly = self._held(data, exclude)
        amount = quote.terms.amount_minor
        if amount is not None:
            if amount > budget.single_limit_minor: blockers.append("BUDGET_SINGLE_EXCEEDED")
            if daily + amount > budget.daily_limit_minor: blockers.append("BUDGET_DAILY_EXCEEDED")
            if monthly + amount > budget.monthly_limit_minor: blockers.append("BUDGET_MONTHLY_EXCEEDED")
        return blockers

    def _preparation_view(self, data, record, plans, library):
        quote, review, budget = data.quotes[record.quote_id], data.reviews[record.review_id], data.budgets[record.budget_id]
        blockers = self._checks(data, quote, review, budget, plans, library, record.preparation_id)
        if record.state == "cancelled": blockers.append("PREPARATION_CANCELLED")
        # Planning allocations from an old period must be renewed before any execution.
        if (record.period_day, record.period_month) != periods(self.clock()): blockers.append("PREPARATION_PERIOD_CHANGED")
        execution = ["REAL_EXECUTION_DISABLED", "REAL_TRANSPORT_UNAVAILABLE", "AUTHENTICATION_UNVERIFIED", "REAL_BILLING_UNCONNECTED"]
        if quote.terms.pricing_kind == "offline_fixture": execution.append("FIXTURE_ONLY")
        if not self.connection.credential_present: execution.append("KEY_MISSING")
        if not self.connection.endpoint_allowed: execution.append("ENDPOINT_NOT_MAINLAND")
        return PreparationView(**record.model_dump(), local_preparation_ready=not blockers,
            active_planning_held_minor=record.planning_held_minor if record.state == "prepared" else 0,
            pricing_kind=quote.terms.pricing_kind, blockers=blockers, execution_blockers=execution)

    def prepare(self, payload):
        with self.plans._transaction() as (library, plans):
            data = self._read()
            for old in data.preparations.values():
                if old.request_id == payload.request_id:
                    if old.model_dump(include=set(type(payload).model_fields)) != payload.model_dump():
                        raise conflict("PREPARATION_REQUEST_CONFLICT", "该请求编号已经用于另一份准备")
                    return self._preparation_view(data, old, plans, library)
            quote = self._get(data.quotes, payload.quote_id)
            if quote.quote_hash != payload.quote_hash:
                raise conflict("QUOTE_HASH_CHANGED", "报价摘要不一致，请重新核对")
            if any(p.quote_id == quote.quote_id for p in data.preparations.values()):
                raise conflict("QUOTE_ALREADY_PREPARED", "这份报价已有准备记录，请使用原记录或新建报价")
            review, budget = self._get(data.reviews, payload.review_id), self._get(data.budgets, payload.budget_id)
            blockers = self._checks(data, quote, review, budget, plans, library)
            if blockers: raise conflict(blockers[0])
            stamp = int(self.clock())
            day, month = periods(stamp)
            record = Preparation(**payload.model_dump(), preparation_id=uuid4().hex, created_at=stamp, updated_at=stamp,
                revision=1, planning_held_minor=quote.terms.amount_minor, period_day=day, period_month=month)
            data.preparations[record.preparation_id] = record
            self._save(data)
            return self._preparation_view(data, record, plans, library)

    def get_preparation(self, identifier):
        with self.plans._transaction() as (library, plans):
            data = self._read()
            return self._preparation_view(data, self._get(data.preparations, identifier), plans, library)

    def cancel(self, identifier, payload):
        with self.plans._transaction() as (library, plans):
            data = self._read()
            record = self._get(data.preparations, identifier)
            if record.state != "cancelled":
                if payload.expected_revision != record.revision:
                    raise conflict("PREPARATION_REVISION_CHANGED", "准备记录已变化，请刷新后核对")
                record.state, record.revision, record.updated_at = "cancelled", 2, int(self.clock())
                self._save(data)
            return self._preparation_view(data, record, plans, library)
