"""Pydantic schemas for the BFSI Lead Qualification demo."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class LeadInput(BaseModel):
    """Incoming lead data submitted by the sales team."""
    lead_id: str
    company_name: str
    annual_revenue_usd: int       # in USD cents
    dscr: float                   # Debt Service Coverage Ratio
    business_age_months: int
    industry: str
    outstanding_liens_usd: int = 0  # in USD cents


class EligibilityResult(BaseModel):
    """Output from the eligibility.check tool."""
    eligible: bool
    flags: list[str]
    score: int                    # 0–100


class QualificationResult(BaseModel):
    """Final qualification output validated against OutputContract."""
    lead_id: str
    decision: Literal["QUALIFIED", "NOT_QUALIFIED", "REVIEW_REQUIRED"]
    score: int = Field(ge=0, le=100)
    risk_category: Literal["LOW", "MEDIUM", "HIGH"]
    rationale: str
    recommended_products: list[str]
    next_steps: list[str]
    compliance_flags: list[str] = Field(default_factory=list)


class StructuredFailure(BaseModel):
    """Returned when the agent cannot qualify due to missing data or out-of-scope."""
    failure_mode: Literal["insufficient_data", "outside_scope"]
    reason: str
    missing_fields: list[str] = Field(default_factory=list)
