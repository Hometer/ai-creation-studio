"""First-release named project goal, compatible with old sessions."""
from typing import Annotated
from pydantic import Field,StringConstraints,field_validator
from api.schemas.assets import Contract,ProjectId,Id
from api.schemas.co_creation import Hash
Name=Annotated[str,StringConstraints(min_length=1,max_length=100)]
class GoalInput(Contract):
    project_name: Name
    target_duration: int=Field(ge=1,le=600)
    expected_token: Hash
    request_id: Id
    @field_validator('project_name')
    @classmethod
    def nonblank(cls,value):
        value=value.strip()
        if not value:raise ValueError('Empty project name')
        return value
class GoalRecord(GoalInput):
    saved_at: str
class GoalView(Contract):
    project_id: ProjectId
    project_name: Name|None
    target_duration: int|None=Field(ge=1,le=600)
    story_excerpt: str=Field(max_length=2000)
    style: str=Field(max_length=100)
    video_ratio: str=Field(max_length=32)
    revision: int=Field(ge=0,le=200)
    running: bool
    expected_token: Hash
