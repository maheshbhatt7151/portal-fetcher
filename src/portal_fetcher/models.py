"""Pydantic models for structured portal fetch results."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class FailureReason(str, Enum):
    """Known failure reasons during portal fetch."""

    PORTAL_TIMEOUT = "portal_timeout"
    LOGIN_FAILED = "login_failed"
    OTP_REQUIRED = "otp_required"
    CAPTCHA_REQUIRED = "captcha_required"
    SUBSCRIBER_NOT_FOUND = "subscriber_not_found"
    MULTIPLE_RESULTS = "multiple_results"
    IDENTITY_MISMATCH = "identity_mismatch"
    INACTIVE_SUBSCRIBER = "inactive_subscriber"
    PAGE_STRUCTURE_CHANGED = "page_structure_changed"
    UNEXPECTED_ERROR = "unexpected_error"


class PlanDetails(BaseModel):
    """Broadband plan information."""

    plan_name: Optional[str] = None
    speed: Optional[str] = None
    duration: Optional[str] = None
    start_date: Optional[str] = None
    expiry_date: Optional[str] = None


class UserDetails(BaseModel):
    """Extracted subscriber details."""

    user_id: Optional[str] = None
    name: Optional[str] = None
    status: Optional[str] = None
    router_mac: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None
    current_plan: Optional[PlanDetails] = None
    future_plan: Optional[PlanDetails] = None
    extra_fields: dict[str, str] = Field(default_factory=dict)


class FetchResult(BaseModel):
    """Top-level result envelope — always produced, even on failure."""

    success: bool
    portal: str
    subscriber: str
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    details: Optional[UserDetails] = None
    failure_reason: Optional[FailureReason] = None
    error_message: Optional[str] = None
    screenshots: list[str] = Field(default_factory=list)
    dom_snapshot_path: Optional[str] = None
