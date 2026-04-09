"""@tool definitions for the BFSI Lead Qualification demo agent.

These are mock implementations — in production they would call real CRM
and risk-scoring APIs.  The tool contracts (deny flags) are real and are
enforced by the ICS Runtime registry.
"""

from __future__ import annotations

from ics_runtime.tools.decorator import tool


# ---------------------------------------------------------------------------
# Mock CRM data — keyed by lead_id
# ---------------------------------------------------------------------------
_CRM_DATA: dict[str, dict] = {
    "L-001": {
        "lead_id": "L-001",
        "company_name": "Nexus Logistics Ltd",
        "annual_revenue_usd": 1_500_000_00,   # $1.5M in cents
        "dscr": 1.42,
        "business_age_months": 84,
        "industry": "logistics",
        "outstanding_liens_usd": 0,
        "owner": "Jane Smith",
    },
    "L-002": {
        "lead_id": "L-002",
        "company_name": "Beta Foods Inc",
        "annual_revenue_usd": 890_000_00,     # $890k
        "dscr": 1.18,
        "business_age_months": 36,
        "industry": "food_service",
        "outstanding_liens_usd": 55_000_00,   # $55k lien
        "owner": "Marcus Chen",
    },
    "L-003": {
        "lead_id": "L-003",
        "company_name": "Apex Consulting",
        "annual_revenue_usd": 250_000_00,     # $250k — below threshold
        "dscr": 0.95,
        "business_age_months": 18,
        "industry": "consulting",
        "outstanding_liens_usd": 0,
        "owner": "Priya Patel",
    },
}

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@tool(
    name="crm.lookup",
    description="Look up enriched lead data from the CRM by lead ID.",
    deny_bulk_export=True,
)
def lookup_lead(lead_id: str) -> dict:
    """Look up enriched lead data from the CRM by lead ID.

    Args:
        lead_id: The lead identifier (e.g. "L-001").

    Returns a dict with company financials and metadata.
    Returns an error dict if the lead is not found.
    """
    data = _CRM_DATA.get(lead_id)
    if data is None:
        return {"error": f"Lead '{lead_id}' not found in CRM"}
    # Return a copy without PII — owner name is excluded per DENY policy
    return {k: v for k, v in data.items() if k != "owner"}


@tool(
    name="eligibility.check",
    description="Run automated eligibility check against qualification criteria.",
)
def check_eligibility(
    annual_revenue_usd: int,
    dscr: float,
    outstanding_liens_usd: int,
    business_age_months: int,
) -> dict:
    """Run the automated eligibility check against qualification criteria.

    Criteria:
    - Revenue >= $500,000 (50_000_000 cents)
    - DSCR >= 1.25
    - Outstanding liens <= $50,000 (5_000_000 cents)
    - Business age >= 24 months

    Args:
        annual_revenue_usd:    Annual revenue in USD cents.
        dscr:                  Debt Service Coverage Ratio.
        outstanding_liens_usd: Total outstanding liens in USD cents.
        business_age_months:   Business age in months.

    Returns dict with eligible (bool), flags (list), score (int 0-100).
    """
    flags: list[str] = []
    score = 100

    if annual_revenue_usd < 50_000_000:
        flags.append(f"revenue_below_threshold: ${annual_revenue_usd / 100:,.0f} < $500,000")
        score -= 40

    if dscr < 1.25:
        flags.append(f"dscr_below_threshold: {dscr:.2f} < 1.25")
        score -= 30
        if dscr < 1.0:
            flags.append("dscr_critical: DSCR below 1.0 — debt exceeds income")
            score -= 20

    if outstanding_liens_usd > 5_000_000:
        flags.append(f"active_liens: ${outstanding_liens_usd / 100:,.0f} > $50,000 limit")
        score -= 20

    if business_age_months < 24:
        flags.append(f"insufficient_operating_history: {business_age_months} months < 24 required")
        score -= 10

    score = max(0, score)
    eligible = len(flags) == 0

    return {
        "eligible": eligible,
        "flags": flags,
        "score": score,
    }


@tool(
    name="compliance.flag",
    description="Create a compliance flag in the lead management system.",
)
def flag_compliance_concern(lead_id: str, concern: str, severity: str) -> dict:
    """Create a compliance flag in the lead management system.

    Args:
        lead_id:  The lead identifier.
        concern:  Description of the compliance concern.
        severity: One of LOW, MEDIUM, or HIGH.

    Returns confirmation dict.
    """
    valid_severities = {"LOW", "MEDIUM", "HIGH"}
    if severity not in valid_severities:
        return {"error": f"severity must be one of {valid_severities}"}
    return {
        "flagged": True,
        "lead_id": lead_id,
        "concern": concern,
        "severity": severity,
        "ticket_id": f"COMP-{lead_id}-{severity[:1]}",
    }
