"""Pydantic request/response models."""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime


class JobSubmitRequest(BaseModel):
    """Submit new job."""
    # Strip before the length check: "   " is three characters but not a topic.
    topic: str = Field(..., min_length=3, max_length=200, description="Topic for video")

    @field_validator("topic")
    @classmethod
    def topic_must_have_content(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("Topic must be at least 3 characters of actual text")
        return cleaned


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


class KeysReplaceRequest(BaseModel):
    """Replace the HF key pool. One token per line in `text`."""
    text: str = Field(..., max_length=20000)


class KeyToggleRequest(BaseModel):
    """Turn one key on or off without deleting it."""
    enabled: bool
