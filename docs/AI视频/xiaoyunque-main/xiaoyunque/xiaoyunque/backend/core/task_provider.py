"""Only a persistent, explicitly artificial local provider is executable."""

import fcntl
import json
import os
import shutil
import stat
from contextlib import contextmanager
from pathlib import Path

from pydantic import Field, model_validator

from api.schemas.assets import Contract, Id
from api.schemas.generation_tasks import Observation, Submission
from core.asset_library import AssetLibraryError, unique_fields
from core.media_archive import sync_directory
from core.persistence import atomic_write_session


def read_document(path: Path, model):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return model()
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Not a regular store")
        data = json.load(stream, object_pairs_hook=unique_fields)
    if (not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1
            or not set(model.model_fields).issubset(data)):
        raise ValueError("Unsupported store")
    return model.model_validate(data)


class SimulatedJob(Contract):
    request_id: Id
    duration: int = Field(ge=1, le=60)
    queries: int = Field(default=0, ge=0)
    submit_calls: int = Field(default=1, ge=1)


class SimulationDocument(Contract):
    schema_version: int = 1
    jobs: dict[Id, SimulatedJob] = Field(default_factory=dict)

    @model_validator(mode="after")
    def matching_ids(self):
        if any(key != job.request_id for key, job in self.jobs.items()):
            raise ValueError("Invalid artificial provider job")
        return self


class LocalSimulationProvider:
    """No model/network calls. Its task IDs and query counters survive restart."""

    simulation_only = True

    def __init__(self, directory: Path, sample_video: Path):
        self.directory, self.sample_video = Path(directory), Path(sample_video)

    @staticmethod
    def amount(model, duration):
        return 2 * duration if model == "local-test-model" else None

    def initialize(self):
        return self

    @contextmanager
    def _store(self):
        try:
            if self.directory.is_symlink():
                raise ValueError("Unsafe artificial provider directory")
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    path = self.directory / "simulation-provider.json"
                    data = read_document(path, SimulationDocument)
                    yield data
                    atomic_write_session(path, data.model_dump(mode="json"))
                    sync_directory(self.directory)
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
        except (OSError, ValueError) as exc:
            raise AssetLibraryError(503, "SIMULATION_STORE_UNAVAILABLE", "人工演练平台记录不可用，请保留数据") from exc

    def submit(self, arguments, request_id, *, timeout_seconds):
        with self._store() as data:
            old = data.jobs.get(request_id)
            if old is not None and old.duration != arguments["duration"]:
                raise ValueError("Artificial idempotency conflict")
            if old is None:
                data.jobs[request_id] = SimulatedJob(request_id=request_id, duration=arguments["duration"])
            else:
                old.submit_calls += 1
        return Submission(provider_task_id=f"sim_{request_id}")

    def query(self, provider_task_id, request_id, *, timeout_seconds):
        with self._store() as data:
            job = data.jobs.get(request_id)
            if job is None:
                return Observation(status="unknown")
            identifier = f"sim_{request_id}"
            if provider_task_id is not None and provider_task_id != identifier:
                raise ValueError("Artificial task ID mismatch")
            job.queries += 1
            done = job.queries >= 2
            return Observation(status="succeeded" if done else "running", provider_task_id=identifier,
                               billed_minor=2 * job.duration if done else None)

    def fetch_result(self, provider_task_id, request_id, destination, *, timeout_seconds):
        with self._store() as data:
            job = data.jobs.get(request_id)
            if provider_task_id != f"sim_{request_id}" or job is None or job.queries < 2:
                raise ValueError("Artificial result is not ready")
        # Both paths are supplied by server code, never an HTTP request.
        temporary = destination.with_suffix(".tmp")
        try:
            with self.sample_video.open("rb") as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
