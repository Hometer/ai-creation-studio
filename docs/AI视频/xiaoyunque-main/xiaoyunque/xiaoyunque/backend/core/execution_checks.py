"""Recheck under the existing asset lock; compile privately and never dispatch."""

from uuid import uuid4

from api.schemas.execution_checks import ExecutionCheck, ExecutionChecksDocument, ExecutionCheckView
from api.schemas.generation_plans import digest
from core.asset_library import AssetLibraryError
from core.generation_readiness import periods
from core.media_archive import sync_directory
from core.model_preflight import inspect_target
from core.persistence import atomic_write_session
from core.task_provider import read_document
from core.wan_adapter import request_digest


class ExecutionChecks:
    def __init__(self, readiness):
        self.readiness = readiness
        self.path = readiness.path.parent / "execution-checks.json"

    @staticmethod
    def unavailable():
        return AssetLibraryError(503, "EXECUTION_CHECK_STORE_UNAVAILABLE", "执行前检查记录不可用，请保留原文件")

    def _read(self, data):
        try:
            document = read_document(self.path, ExecutionChecksDocument)
            for check in document.checks.values():
                preparation = data.preparations.get(check.preparation_id)
                if (preparation is None or check.preparation_revision > preparation.revision
                        or check.quote_id != preparation.quote_id or check.review_id != preparation.review_id
                        or check.budget_id != preparation.budget_id):
                    raise ValueError("Invalid preparation graph")
                quote = data.quotes[preparation.quote_id]
                if (check.plan_id != quote.terms.plan_id or check.content_hash != quote.terms.content_hash
                        or check.quote_hash != quote.quote_hash or check.target_count != len(quote.terms.lines)
                        or check.checked_at < preparation.created_at
                        or (check.inputs_compiled and set(check.request_sha256) != {line.segment_id for line in quote.terms.lines})):
                    raise ValueError("Invalid quote graph")
            return document
        except (OSError, ValueError, KeyError):
            raise self.unavailable() from None

    def _save(self, document):
        try:
            value = document.model_dump(mode="json")
            ExecutionChecksDocument.model_validate(value)
            atomic_write_session(self.path, value)
            sync_directory(self.path.parent)
        except (OSError, ValueError):
            raise self.unavailable() from None

    def _basis(self, data, preparation, plans, library):
        service = self.readiness
        view = service._preparation_view(data, preparation, plans, library)
        quote = data.quotes[preparation.quote_id]
        plan, plan_view = service._plan_view(plans, library, quote.terms.plan_id)
        # Only digests/classifications leave this transaction, never prompt/media/Key.
        basis = digest({"preparation": preparation.model_dump(mode="json"),
            "quote": quote.model_dump(mode="json"), "current_price_id": data.current_price_id,
            "current_budget_id": data.current_budget_id,
            "current_review_id": data.current_reviews.get(plan.plan_id),
            "plan_hash": plan.content_hash, "plan_current": plan_view.current,
            "blockers": view.blockers, "execution_blockers": view.execution_blockers,
            "connection": service.connection.model_dump(mode="json"), "periods": periods(service.clock())})
        return basis, view, quote, plan

    def _view(self, check, data, plans, library):
        preparation = data.preparations[check.preparation_id]
        basis, view, _, _ = self._basis(data, preparation, plans, library)
        return ExecutionCheckView(**check.model_dump(), current=basis == check.basis_sha256,
                                  current_blockers=view.blockers)

    def create(self, identifier):
        service = self.readiness
        with service.plans._transaction() as (library, plans):
            data = service._read()
            preparation = service._get(data.preparations, identifier)
            document = self._read(data)
            basis, view, quote, plan = self._basis(data, preparation, plans, library)
            blockers, fingerprints = list(view.blockers), {}
            if not blockers:
                for target in plan.content.targets:
                    checked, request = inspect_target(plan.content, target, service.plans.archive, True)
                    if not checked.input_protocol_ready or request is None:
                        blockers.extend(issue.code for issue in checked.issues if issue.severity == "error")
                        blockers.append("INPUT_PROTOCOL_INVALID")
                    else:
                        fingerprints[target.segment_id] = request_digest(request)
                    # Do not return, log or persist the compiled request.
                    del request
            if blockers:
                fingerprints = {}
            check = ExecutionCheck(check_id=uuid4().hex, preparation_id=identifier,
                preparation_revision=preparation.revision, plan_id=plan.plan_id,
                quote_id=preparation.quote_id, review_id=preparation.review_id, budget_id=preparation.budget_id,
                content_hash=quote.terms.content_hash, quote_hash=quote.quote_hash, checked_at=int(service.clock()),
                basis_sha256=basis, connection=service.connection, target_count=len(plan.content.targets),
                request_sha256=fingerprints, conditions_ready=not blockers, inputs_compiled=not blockers,
                blockers=list(dict.fromkeys(blockers)), execution_blockers=view.execution_blockers)
            document.checks[check.check_id] = check
            self._save(document)
            return self._view(check, data, plans, library)

    def get(self, identifier):
        service = self.readiness
        with service.plans._transaction() as (library, plans):
            data = service._read()
            document = self._read(data)
            check = document.checks.get(identifier)
            if check is None:
                raise AssetLibraryError(404, "EXECUTION_CHECK_NOT_FOUND", "执行前检查记录不存在")
            return self._view(check, data, plans, library)
