"""ICS layer definitions for the BFSI Lead Qualification demo agent."""

from __future__ import annotations

from ics_runtime import Agent, OutputContract
from demo.bfsi_agent.schemas import QualificationResult
from demo.bfsi_agent.tools import (
    lookup_lead,
    check_eligibility,
    flag_compliance_concern,
)

# ---------------------------------------------------------------------------
# ICS Layers
# ---------------------------------------------------------------------------

IMMUTABLE = """
System: BFSI Lead Management v2 — Business Lending Division
Domain: SME business lending qualification for Meridian Wealth
Regulatory context: Fair lending laws (ECOA, Reg B); FCRA compliance
Currency: All monetary values in USD; amounts as integers in cents
Qualification criteria:
  - Annual revenue >= $500,000 (50,000,000 cents)
  - DSCR >= 1.25 (Debt Service Coverage Ratio)
  - Outstanding liens <= $50,000 (5,000,000 cents)
  - Business operating history >= 24 months
Business context: Leads are pre-screened at acquisition; this agent performs
  second-stage qualification. Decisions are advisory — final approval requires
  human underwriter review.
""".strip()

CAPABILITY = """
ALLOW: lead qualification assessment
ALLOW: credit risk summary generation
ALLOW: eligibility determination WITHIN defined qualification criteria
ALLOW: compliance flag creation via compliance.flag tool
DENY: logging PII fields (SSN, date of birth, personal account numbers)
DENY: bulk export of lead records
DENY: qualification decision without running eligibility.check tool
DENY: introduction of float arithmetic ON monetary values
REQUIRE: monetary units in USD cents (integer)
REQUIRE: qualification rationale citing specific criteria
REQUIRE: risk_category field on every QualificationResult
REQUIRE: compliance flag via compliance.flag tool when DSCR < 1.0
""".strip()


def make_agent(provider: str = "anthropic", model: str | None = None) -> Agent:
    """Instantiate the BFSI Lead Qualification agent.

    Args:
        provider: ``"anthropic"`` or ``"openai"``.
        model:    Override the default model for the provider.
    """
    return Agent(
        provider=provider,
        model=model,
        immutable=IMMUTABLE,
        capability=CAPABILITY,
        tools=[lookup_lead, check_eligibility, flag_compliance_concern],
        output_contract=OutputContract(
            schema=QualificationResult,
            failure_modes=["BLOCKED:", "insufficient_data", "outside_scope"],
        ),
    )
