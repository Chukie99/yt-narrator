"""Pydantic request/response models."""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class JobSubmitRequest(BaseModel):
    """Submit new job."""
    topic: str = Field(..., min_length=3, max_length=200, description="Topic for video")


class JobEstimateResponse(BaseModel):
    """Cost estimate."""
    job_id: str
    estimated_images: int
    estimated_cost_usd: float
    estimated_duration_min: str


class JobStatusResponse(BaseModel):
    """Job status."""
    id: str
    status: str
    stage: Optional[str]
    progress: str
    revision: int
    error_msg: Optional[str]
    created_at: str


class ScenePatchRequest(BaseModel):
    """Edit single scene."""
    narration_text: Optional[str] = None
    t2i_prompt: Optional[str] = None


class StyleBiblePatchRequest(BaseModel):
    """Edit style bible."""
    visual_themes: Optional[List[str]] = None
    color_palette: Optional[List[str]] = None
    mood: Optional[str] = None
