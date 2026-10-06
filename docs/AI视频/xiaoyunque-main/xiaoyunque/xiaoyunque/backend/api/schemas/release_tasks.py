"""Read-only bounded summaries, with no documents or provider credentials."""
from typing import Literal
from pydantic import Field
from api.schemas.assets import Contract,Id,ProjectId
from api.schemas.release_media import ProviderId
class ReleaseTask(Contract):
    job_id:Id
    kind:Literal['creation','script','storyboard','image','video','composition']
    project_id:ProjectId
    project_exists:bool
    title:str=Field(min_length=1,max_length=160)
    number:int=Field(ge=1,le=200)
    created_at:str
    status:Literal['draft','awaiting_confirmation','queued','submitting','pending','querying','ready_download','downloading','rendering','succeeded','failed','manual_review','cancelled']
    simulation_only:bool
    model_call:bool
    estimate_fen:int=Field(ge=0,le=500)
    reserved_fen:int=Field(ge=0,le=500)
    actual_billed_fen:None=None
    provider_task_id:ProviderId|None=None
    href:str=Field(max_length=600)
    result_url:str|None=Field(default=None,max_length=600)
    error_summary:str|None=Field(default=None,max_length=160)
class ReleaseTasks(Contract):
    tasks:list[ReleaseTask]=Field(max_length=500)
