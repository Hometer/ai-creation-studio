"""Atomic budget reservations and checkpointed local task execution."""

import time
from contextlib import contextmanager
from uuid import uuid4

from api.schemas.assets import TaskSource, VersionInput
from api.schemas.generation_plans import digest
from api.schemas.generation_tasks import (
    Approval, Billing, BudgetView, Observation, Offer, Quote, QuoteView, Result,
    Submission, Task, TaskError, TaskStoreDocument, TaskView,
)
from core.asset_library import AssetLibraryError, now
from core.generation_inputs import prepare_calls
from core.generation_plans import conflict
from core.media_archive import sync_directory
from core.persistence import atomic_write_session
from core.task_provider import read_document

LEASE_SECONDS = 30
CALL_TIMEOUT_SECONDS = 10
MESSAGES = {
    "PROVIDER_INIT_FAILED": "平台初始化失败；保留记录并核对，不重复提交",
    "SUBMISSION_UNCERTAIN": "提交结果不确定；仅查询原任务，不重新生成",
    "SUBMISSION_NOT_FOUND": "暂未查到原任务，请人工核对平台记录",
    "QUERY_FAILED": "查询失败，原任务及预算预留已保留",
    "QUERY_LIMIT_REACHED": "查询次数已到上限，请人工核对原任务",
    "PROVIDER_FAILED": "平台明确报告任务失败，请查看原任务",
    "PROVIDER_CONTRACT_INVALID": "平台任务或消耗信息不符合合同，请人工核对",
    "RESULT_FAILED": "结果读取或归档失败，原任务已保留",
    "SOURCE_CHANGED": "计划来源已变化，请新建计划并确认",
    "QUOTE_EXPIRED": "报价已过期，请重新查看报价并确认",
}
PROGRESS = {"blocked": 0, "queued": 5, "submitting": 10, "submitted": 20,
            "running": 50, "fetching": 85, "succeeded": 100, "failed": 100, "manual_review": 0}


class GenerationTasks:
    def __init__(self, plans, provider=None):
        self.plans, self.provider = plans, provider
        self.path = plans.library.directory / "generation-tasks.json"

    @contextmanager
    def _transaction(self):
        with self.plans._transaction() as (library, plans):
            try:
                data = read_document(self.path, TaskStoreDocument)
                yield library, plans, data
            except (OSError, ValueError) as exc:
                raise AssetLibraryError(503, "TASK_STORE_UNAVAILABLE", "任务与费用记录暂不可用，请保留原数据并重试") from exc

    def _save(self, data):
        value = data.model_dump(mode="json")
        TaskStoreDocument.model_validate(value)
        atomic_write_session(self.path, value)
        sync_directory(self.path.parent)

    @staticmethod
    def _quote(data, quote_id):
        quote = data.quotes.get(quote_id)
        if quote is None:
            raise AssetLibraryError(404, "QUOTE_NOT_FOUND", "报价不存在")
        return quote

    @staticmethod
    def _task(data, task_id):
        task = data.tasks.get(task_id)
        if task is None:
            raise AssetLibraryError(404, "TASK_NOT_FOUND", "任务不存在")
        return task

    def _current(self, quote, library, plans):
        plan = plans.plans.get(quote.offer.plan_id)
        return bool(plan and plan.confirmation and plan.content_hash == quote.offer.content_hash
                    and self.plans._view(plan, library, self.plans.engine.get_status_snapshot(plan.content.project_id)).current)

    def _quote_view(self, quote, library, plans):
        return QuoteView(**quote.model_dump(), current=self._current(quote, library, plans),
                         expired=quote.offer.expires_at <= int(time.time()))

    def _view(self, task, library, plans, data):
        quote = data.quotes[task.quote_id]
        blockers = []
        if task.status == "blocked":
            if quote.offer.amount_minor is None: blockers.append("quote_unavailable")
            if quote.approval is None: blockers.append("approval_required")
            if not self._current(quote, library, plans): blockers.append("source_changed")
            if quote.offer.expires_at <= int(time.time()): blockers.append("quote_expired")
            if quote.approval and quote.offer.amount_minor is not None:
                budget = self._budget(data, task.project_id)
                limit = min(budget.limit_minor, quote.approval.project_limit_minor)
                if budget.reserved_minor + budget.settled_minor + quote.offer.amount_minor > limit:
                    blockers.append("project_budget_exceeded")
        if self.provider is None: blockers.append("provider_unavailable")
        result_url = f"/api/assets/{task.result.asset_id}/versions/{task.result.version_id}/media" if task.result else None
        return TaskView(**task.model_dump(), progress=PROGRESS[task.status], blockers=blockers,
                        simulation_only=quote.offer.pricing_kind == "simulation", result_url=result_url)

    @staticmethod
    def _budget(data, project_id):
        tasks = [t for t in data.tasks.values() if t.project_id == project_id]
        reserved = sum(t.billing.reserved_minor for t in tasks if t.billing.state == "reserved")
        settled = sum(t.billing.billed_minor for t in tasks if t.billing.state == "settled")
        limit = data.budget_limits.get(project_id)
        return BudgetView(project_id=project_id, currency="SIM" if limit is not None else None,
            limit_minor=limit, reserved_minor=reserved, settled_minor=settled,
            available_minor=limit - reserved - settled if limit is not None else None)

    def quote(self, payload):
        with self._transaction() as (library, plans, data):
            plan = self.plans._get(plans, payload.plan_id)
            target = next((t for t in plan.content.targets if t.segment_id == payload.segment_id), None)
            if target is None:
                raise AssetLibraryError(404, "QUOTE_TARGET_NOT_FOUND", "计划中的片段不存在")
            if not plan.confirmation:
                raise conflict("PLAN_NOT_CONFIRMED", "请先查看计划并确认本地内容")
            if not self.plans._view(plan, library, self.plans.engine.get_status_snapshot(plan.content.project_id)).current:
                raise conflict()
            amount = self.provider.amount(plan.content.options.model, target.duration) if self.provider else None
            offer = Offer(plan_id=plan.plan_id, segment_id=target.segment_id, project_id=plan.content.project_id,
                content_hash=plan.content_hash, options=plan.content.options, duration=target.duration,
                pricing_kind="simulation" if amount is not None else "unavailable",
                currency="SIM" if amount is not None else None, amount_minor=amount,
                basis="人工演练：2 SIM／秒，不代表平台报价或真实费用" if amount is not None else "真实厂商、报价与预算尚未配置",
                expires_at=int(time.time()) + 1200)
            quote = Quote(quote_id=uuid4().hex, created_at=now(), offer=offer,
                          quote_hash=digest(offer.model_dump(mode="json")))
            data.quotes[quote.quote_id] = quote
            self._save(data)
            return self._quote_view(quote, library, plans)

    def get_quote(self, quote_id):
        with self._transaction() as (library, plans, data):
            return self._quote_view(self._quote(data, quote_id), library, plans)

    def confirm_quote(self, quote_id, payload):
        with self._transaction() as (library, plans, data):
            quote = self._quote(data, quote_id)
            self._eligible(quote, library, plans)
            if payload.quote_hash != quote.quote_hash:
                raise conflict("QUOTE_HASH_MISMATCH", "报价与预览不一致，请重新查看")
            if quote.offer.amount_minor > payload.max_amount_minor:
                raise conflict("SINGLE_BUDGET_EXCEEDED", "本次报价超过单次上限")
            if quote.approval:
                if quote.approval.model_dump(exclude={"confirmed_at"}) != payload.model_dump():
                    raise conflict("QUOTE_ALREADY_CONFIRMED", "已保存确认不能覆盖，请新建报价后确认")
                return self._quote_view(quote, library, plans)
            budget = self._budget(data, quote.offer.project_id)
            if payload.project_limit_minor < budget.reserved_minor + budget.settled_minor:
                raise conflict("PROJECT_BUDGET_EXCEEDED", "项目已有消耗或预留超过新上限")
            quote.approval = Approval(**payload.model_dump(), confirmed_at=now())
            data.budget_limits[quote.offer.project_id] = payload.project_limit_minor
            self._save(data)
            return self._quote_view(quote, library, plans)

    def _eligible(self, quote, library, plans):
        if self.provider is None or quote.offer.pricing_kind != "simulation" or quote.offer.amount_minor is None:
            raise conflict("QUOTE_UNAVAILABLE", "费用未知或真实执行未接入，不能确认或入队")
        if quote.offer.expires_at <= int(time.time()):
            raise conflict("QUOTE_EXPIRED", "报价已过期，请重新查看报价并确认")
        if not self._current(quote, library, plans):
            raise conflict()

    def create(self, payload):
        with self._transaction() as (library, plans, data):
            quote = self._quote(data, payload.quote_id)
            old = next((t for t in data.tasks.values() if t.request_id == payload.request_id), None)
            if old:
                if old.quote_id != payload.quote_id:
                    raise conflict("TASK_IDEMPOTENCY_CONFLICT", "同一请求编号不能用于不同报价")
                return self._view(old, library, plans, data)
            if any(t.quote_id == payload.quote_id for t in data.tasks.values()):
                raise conflict("QUOTE_ALREADY_USED", "该报价已有任务，重新生成需新报价和确认")
            task = Task(**payload.model_dump(), task_id=uuid4().hex, project_id=quote.offer.project_id,
                plan_id=quote.offer.plan_id, segment_id=quote.offer.segment_id, content_hash=quote.offer.content_hash,
                created_at=now(), updated_at=now(), revision=1)
            data.tasks[task.task_id] = task
            self._save(data)
            return self._view(task, library, plans, data)

    def get(self, task_id):
        with self._transaction() as (library, plans, data):
            return self._view(self._task(data, task_id), library, plans, data)

    def list(self, project_id):
        with self._transaction() as (library, plans, data):
            return [self._view(t, library, plans, data) for t in data.tasks.values() if t.project_id == project_id]

    def budget(self, project_id):
        with self._transaction() as (_, _, data):
            return self._budget(data, project_id)

    @staticmethod
    def _changed(task):
        task.revision += 1
        task.updated_at = now()

    def enqueue(self, task_id, payload):
        with self._transaction() as (library, plans, data):
            task = self._task(data, task_id)
            quote = data.quotes[task.quote_id]
            if quote.quote_hash != payload.quote_hash:
                raise conflict("QUOTE_HASH_MISMATCH", "报价与预览不一致，请重新查看")
            if task.status != "blocked":
                if task.status in {"queued", "submitting", "submitted", "running", "fetching", "succeeded"}:
                    return self._view(task, library, plans, data)
                raise conflict("TASK_STATE_CONFLICT", "原任务不能再次提交，请先核对")
            if task.revision != payload.expected_revision:
                raise conflict("TASK_REVISION_CONFLICT", "任务已变化，请重新查看")
            self._eligible(quote, library, plans)
            if quote.approval is None:
                raise conflict("QUOTE_NOT_CONFIRMED", "请先查看报价并确认演练费用与上限")
            prepare_calls(plans.plans[task.plan_id].content, self.plans.archive)
            budget = self._budget(data, task.project_id)
            limit = min(budget.limit_minor, quote.approval.project_limit_minor)
            if budget.reserved_minor + budget.settled_minor + quote.offer.amount_minor > limit:
                raise conflict("PROJECT_BUDGET_EXCEEDED", "本次预留将超过项目总上限")
            task.billing = Billing(state="reserved", reserved_minor=quote.offer.amount_minor)
            task.status, task.error = "queued", None
            self._changed(task)
            self._save(data)
            return self._view(task, library, plans, data)

    def _claim(self, task_id):
        with self._transaction() as (library, plans, data):
            task = self._task(data, task_id)
            if task.status in {"succeeded", "failed"}:
                return None
            if task.lease_until is not None and task.lease_until > int(time.time()):
                raise conflict("TASK_BUSY", "任务正在处理，请稍后查询原任务")
            archived = self._archived(task, library) if task.status in {"fetching", "manual_review"} else None
            if archived is not None:
                task.lease_token, task.lease_until = uuid4().hex, int(time.time()) + LEASE_SECONDS
                self._changed(task)
                self._save(data)
                return task.model_copy(deep=True), "reuse", archived
            if self.provider is None:
                raise conflict("TASK_PROVIDER_UNAVAILABLE", "真实执行未接入，请保留任务等待配置")
            if task.status == "blocked":
                raise conflict("TASK_NOT_QUEUED", "请先确认费用并将任务入队")
            if (task.consecutive_errors >= 3
                    or task.status != "fetching" and task.query_attempts >= 20
                    or task.status == "fetching" and task.fetch_attempts >= 3):
                raise conflict("TASK_REVIEW_REQUIRED", "重试或查询达到上限，请人工核对原任务")
            quote = data.quotes[task.quote_id]
            arguments = None
            action = "fetch" if task.status == "fetching" else "query"
            if task.status == "queued":
                if not self._current(quote, library, plans) or quote.offer.expires_at <= int(time.time()):
                    task.status, task.billing = "blocked", Billing()
                    code = "SOURCE_CHANGED" if not self._current(quote, library, plans) else "QUOTE_EXPIRED"
                    task.error = TaskError(code=code, message=MESSAGES[code])
                    self._changed(task)
                    self._save(data)
                    return None
                arguments = next(c.arguments for c in prepare_calls(plans.plans[task.plan_id].content, self.plans.archive)
                                 if c.segment_id == task.segment_id)
                action = "submit"
                task.status, task.submit_attempts = "submitting", 1
            elif action == "query":
                task.query_attempts += 1
            else:
                task.fetch_attempts += 1
            task.lease_token, task.lease_until = uuid4().hex, int(time.time()) + LEASE_SECONDS
            self._changed(task)
            self._save(data)
            return task.model_copy(deep=True), action, arguments

    def _finish(self, claimed, update):
        with self._transaction() as (library, plans, data):
            task = self._task(data, claimed.task_id)
            if task.lease_token != claimed.lease_token:
                raise conflict("TASK_LEASE_CHANGED", "任务已由恢复流程接管，请查询最新状态")
            update(task)
            task.lease_token, task.lease_until = None, None
            self._changed(task)
            self._save(data)
            return self._view(task, library, plans, data)

    def _failure(self, claimed, code, action):
        def update(task):
            task.error = TaskError(code=code, message=MESSAGES[code])
            task.consecutive_errors += 1
            if action == "submit" or task.consecutive_errors >= 3 or code in {"SUBMISSION_NOT_FOUND", "PROVIDER_CONTRACT_INVALID"}:
                task.status = "manual_review"
            # No refund or resubmission merely because the response was lost.
        return self._finish(claimed, update)

    def _output(self, claimed):
        directory = self.path.parent / "task-output" / claimed.task_id
        if directory.parent.is_symlink() or directory.is_symlink():
            raise OSError("Unsafe private output directory")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / "output.mp4"
        if path.is_symlink() or path.with_suffix(".tmp").is_symlink():
            raise OSError("Unsafe output file")
        return path

    def _archived(self, claimed, library=None):
        library = library if library is not None else self.plans.library.read()
        matches = [(asset, version) for asset in library.assets.values() for version in asset.versions
                   if isinstance(version.source, TaskSource) and version.source.task_id == claimed.task_id]
        if len(matches) > 1:
            raise ValueError("Duplicate task result")
        if not matches:
            return None
        asset, version = matches[0]
        if (version.source.plan_id != claimed.plan_id or version.source.content_hash != claimed.content_hash
                or version.source.project_id != claimed.project_id or version.source.item_id != claimed.segment_id):
            raise ValueError("Task result provenance changed")
        self.plans.archive.verify_file(version.media)
        return Result(asset_id=asset.asset_id, version_id=version.version_id, sha256=version.media.sha256)

    def advance(self, task_id):
        claimed = self._claim(task_id)
        if claimed is None:
            return self.get(task_id)
        task, action, arguments = claimed
        if action == "reuse":
            return self._succeeded(task, arguments)
        try:
            provider = self.provider.initialize()
        except Exception:
            return self._failure(task, "PROVIDER_INIT_FAILED", action)
        if action == "submit":
            try:
                response = provider.submit(arguments, task.request_id, timeout_seconds=CALL_TIMEOUT_SECONDS)
                result = Submission.model_validate(response.model_dump() if isinstance(response, Submission) else response)
            except Exception:
                return self._failure(task, "SUBMISSION_UNCERTAIN", action)
            def submitted(current):
                current.provider_task_id, current.status = result.provider_task_id, "submitted"
                current.error, current.consecutive_errors = None, 0
            return self._finish(task, submitted)
        if action == "query":
            try:
                response = provider.query(task.provider_task_id, task.request_id, timeout_seconds=CALL_TIMEOUT_SECONDS)
                result = Observation.model_validate(response.model_dump() if isinstance(response, Observation) else response)
            except Exception:
                return self._failure(task, "QUERY_FAILED", action)
            if result.status == "unknown":
                return self._failure(task, "SUBMISSION_NOT_FOUND", action)
            if (task.provider_task_id is not None and result.provider_task_id != task.provider_task_id
                    or result.billed_minor is not None and result.billed_minor > task.billing.reserved_minor):
                return self._failure(task, "PROVIDER_CONTRACT_INVALID", action)
            def queried(current):
                current.provider_task_id = result.provider_task_id
                current.error, current.consecutive_errors = None, 0
                current.status = {"running": "running", "succeeded": "fetching", "failed": "failed"}[result.status]
                current.observed_billed_minor = result.billed_minor
                if result.status == "failed":
                    current.error = TaskError(code="PROVIDER_FAILED", message=MESSAGES["PROVIDER_FAILED"])
                    if result.billed_minor is not None:
                        current.billing = Billing(state="settled", reserved_minor=current.billing.reserved_minor, billed_minor=result.billed_minor)
                elif current.query_attempts >= 20 and result.status == "running":
                    current.status = "manual_review"
                    current.error = TaskError(code="QUERY_LIMIT_REACHED", message=MESSAGES["QUERY_LIMIT_REACHED"])
            return self._finish(task, queried)
        try:
            result = self._archived(task)
            if result is None:
                destination = self._output(task)
                provider.fetch_result(task.provider_task_id, task.request_id, destination, timeout_seconds=CALL_TIMEOUT_SECONDS)
                source = TaskSource(project_id=task.project_id, item_id=task.segment_id, task_id=task.task_id,
                    plan_id=task.plan_id, content_hash=task.content_hash, simulation_only=True)
                definition = VersionInput(name=f"人工任务 {task.segment_id}"[:100],
                    definition="本地任务恢复演练产物；人工 MP4，不代表模型结果")
                with self.plans.archive.prepare(destination, "video") as (temporary, details):
                    self.plans.library.import_media(source, "video", definition, self.plans.archive, temporary, details)
                result = self._archived(task)
                if result is None:
                    raise ValueError("Missing archived result")
        except Exception:
            return self._failure(task, "RESULT_FAILED", action)
        return self._succeeded(task, result)

    def _succeeded(self, task, result):
        def succeeded(current):
            current.result, current.status = result, "succeeded"
            current.error, current.consecutive_errors = None, 0
            if current.observed_billed_minor is not None:
                current.billing = Billing(state="settled", reserved_minor=current.billing.reserved_minor,
                                          billed_minor=current.observed_billed_minor)
        return self._finish(task, succeeded)
