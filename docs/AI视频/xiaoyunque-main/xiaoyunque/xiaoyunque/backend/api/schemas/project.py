from typing import Any, Dict, Optional, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectStartRequest(BaseModel):
    idea: str
    project_name: str | None = Field(default=None,min_length=1,max_length=100,strict=True)
    target_duration: int | None = Field(default=None,ge=1,le=600,strict=True)
    file_path: Optional[str] = None
    style: Optional[str] = None
    video_ratio: Optional[str] = "9:16"
    video_resolution: Optional[str] = "720P"
    expand_idea: Optional[bool] = True
    llm_model: Optional[str] = None
    vlm_model: Optional[str] = None
    image_t2i_model: Optional[str] = None
    image_it2i_model: Optional[str] = None
    video_model: Optional[str] = None
    video_first_frame_model: Optional[str] = None
    video_start_end_model: Optional[str] = None
    video_reference_model: Optional[str] = None
    video_generation_mode: Optional[str] = "first_frame"
    enable_concurrency: Optional[bool] = True
    web_search: Optional[bool] = False
    episodes: Optional[int] = None

    @field_validator('project_name')
    @classmethod
    def name(cls,value):
        if value is None:return value
        value=value.strip()
        if not value:raise ValueError('Empty project name')
        return value

class InterventionRequest(BaseModel):
    stage: str
    modifications: Dict[str, Any]


class ProjectModelUpdateRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    llm_model: str = ""
    vlm_model: str = ""
    image_t2i_model: str = ""
    image_it2i_model: str = ""
    video_model: str = ""
    video_first_frame_model: str = ""
    video_start_end_model: str = ""
    video_reference_model: str = ""
    video_generation_mode: Literal["first_frame", "start_end_frame", "reference"] = "first_frame"
    video_ratio: str = ""
    video_resolution: str = ""
    style: str = ""
    enable_concurrency: bool = True
