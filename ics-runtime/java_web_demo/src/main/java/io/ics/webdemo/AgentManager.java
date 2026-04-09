package io.ics.webdemo;

import io.ics.runtime.Agent;
import io.ics.runtime.Violation;
import io.ics.runtime.contracts.OutputContract;
import io.ics.runtime.tools.ToolDefinition;

import java.util.*;

/**
 * Lazily initialises and caches the BFSI Agent singleton.
 *
 * <p>Recreated when the provider changes (mirrors Python's {@code _get_agent()}).
 * Thread-safe via {@code synchronized}.
 */
public final class AgentManager {

    // ── Mock CRM data ─────────────────────────────────────────────────────────

    static final Map<String, Map<String, Object>> CRM_DATA;
    static {
        Map<String, Map<String, Object>> m = new LinkedHashMap<>();
        m.put("L-001", Map.of(
            "lead_id", "L-001", "company_name", "Nexus Logistics Ltd",
            "annual_revenue_usd", 150_000_000, "dscr", 1.42,
            "business_age_months", 84, "industry", "logistics",
            "outstanding_liens_usd", 0));
        m.put("L-002", Map.of(
            "lead_id", "L-002", "company_name", "Beta Foods Inc",
            "annual_revenue_usd", 89_000_000, "dscr", 1.18,
            "business_age_months", 36, "industry", "food_service",
            "outstanding_liens_usd", 5_500_000));
        m.put("L-003", Map.of(
            "lead_id", "L-003", "company_name", "Apex Consulting",
            "annual_revenue_usd", 25_000_000, "dscr", 0.95,
            "business_age_months", 18, "industry", "consulting",
            "outstanding_liens_usd", 0));
        CRM_DATA = Collections.unmodifiableMap(m);
    }

    // ── ICS layers ────────────────────────────────────────────────────────────

    private static final String IMMUTABLE = """
System: BFSI Lead Management v2 — Business Lending Division
Division: Commercial Credit — SME Business Lending
Operator: Meridian Wealth Partners LLC
Version: 2.4.1 | Effective: 2026-01-01 | Review: 2026-12-31

=== REGULATORY FRAMEWORK ===

Equal Credit Opportunity Act (ECOA) / Regulation B:
  - Prohibits discrimination based on race, color, religion, national origin,
    sex, marital status, age, or receipt of public assistance.
  - Adverse action notices must be issued within 30 days citing specific reasons.
  - Do not reference: race, gender, marital status, religion, age, national origin.

Fair Credit Reporting Act (FCRA):
  - Credit data may only be used for permissible purposes (credit evaluation).
  - Adverse action triggers a right-to-dispute notice requirement.

Bank Secrecy Act (BSA) / Anti-Money Laundering (AML):
  - Flag unusual revenue patterns inconsistent with stated industry.
  - Flag leads where lien history suggests undisclosed creditors.
  - Mandatory compliance escalation for DSCR < 1.0 (insolvency risk indicator).

=== QUALIFICATION CRITERIA ===

Primary underwriting criteria (all must pass for QUALIFIED):
  1. Annual revenue:        >= $500,000 USD  (50,000,000 cents)
  2. Debt Service Coverage: >= 1.25 DSCR
  3. Outstanding liens:     <= $50,000 USD   (5,000,000 cents)
  4. Operating history:     >= 24 months

DSCR interpretation:
  - DSCR >= 1.50: Strong
  - DSCR 1.25-1.49: Acceptable — meets minimum
  - DSCR 1.00-1.24: Marginal — REVIEW_REQUIRED
  - DSCR < 1.00: Critical — mandatory HIGH compliance flag; adverse action required

Risk category:
  - LOW:    Score 80-100, DSCR >= 1.40, revenue >= $1M, no liens, history >= 48 mo
  - MEDIUM: Score 50-79, meets minimum criteria
  - HIGH:   Score 0-49 or critical DSCR or mandatory compliance flag

=== PRODUCT CATALOGUE ===

  BizGrow Flex Line (BFLEX): $50k-$500k revolving; Rev>=$500k, DSCR>=1.25, Age>=24mo
  Commercial Term Loan (CTL): $100k-$2M; Rev>=$750k, DSCR>=1.35, Age>=36mo
  SBA 7(a): up to $5M; Rev>=$1M, DSCR>=1.40, Age>=60mo
  Invoice Finance Advance (IFA): up to 85% receivables; Rev>=$300k, Age>=12mo

=== MONETARY CONVENTIONS ===

All tool monetary fields are in USD cents (integer).
Do NOT use floating-point arithmetic on monetary values.
    """;

    private static final String CAPABILITY = """
ALLOW: looking up CRM data for leads
ALLOW: running eligibility checks on leads
ALLOW: creating compliance flags for regulatory concerns
ALLOW: recommending products from the approved catalogue
DENY: logging or returning PII data (owner names, SSN, personal identifiers)
DENY: exporting bulk lead records
DENY: introduction of float arithmetic ON monetary values
DENY: credit decisions based on ECOA-protected characteristics
REQUIRE: risk category (LOW/MEDIUM/HIGH) in every qualification decision
REQUIRE: compliance flag for DSCR < 1.0
REQUIRE: rationale referencing specific qualification criteria
    """;

    // ── Singleton ─────────────────────────────────────────────────────────────

    private static Agent agent;
    private static String agentProvider = "";

    public static synchronized Agent get(String provider) {
        if (agent == null || !agentProvider.equals(provider)) {
            LogBus.info("Initialising BFSI agent (provider=" + provider + ")");
            agent = build(provider);
            agentProvider = provider;
            LogBus.ok("Agent ready — model=" + agent.getModel());
        }
        return agent;
    }

    // ── Tool implementations ──────────────────────────────────────────────────

    private static Object crmLookup(String leadId) {
        Map<String, Object> data = CRM_DATA.get(leadId);
        if (data == null) return Map.of("error", "Lead '" + leadId + "' not found in CRM");
        return new HashMap<>(data);   // copy; no PII — owner excluded from mock data
    }

    private static Object eligibilityCheck(Number rev, Number dscr, Number liens, Number age) {
        long r   = rev   != null ? rev.longValue()   : 0L;
        double d = dscr  != null ? dscr.doubleValue(): 0.0;
        long l   = liens != null ? liens.longValue() : 0L;
        int a    = age   != null ? age.intValue()    : 0;

        List<String> flags = new ArrayList<>();
        int score = 100;

        if (r < 50_000_000) {
            flags.add(String.format("revenue_below_threshold: $%,.0f < $500,000", r / 100.0));
            score -= 40;
        }
        if (d < 1.25) {
            flags.add(String.format("dscr_below_threshold: %.2f < 1.25", d));
            score -= 30;
            if (d < 1.0) { flags.add("dscr_critical: DSCR below 1.0"); score -= 20; }
        }
        if (l > 5_000_000) {
            flags.add(String.format("active_liens: $%,.0f > $50,000 limit", l / 100.0));
            score -= 20;
        }
        if (a < 24) {
            flags.add("insufficient_operating_history: " + a + " months < 24 required");
            score -= 10;
        }
        return Map.of("eligible", flags.isEmpty(), "flags", flags, "score", Math.max(0, score));
    }

    private static Object complianceFlag(String leadId, String concern, String severity) {
        if (!Set.of("LOW", "MEDIUM", "HIGH").contains(severity))
            return Map.of("error", "severity must be LOW, MEDIUM, or HIGH");
        return Map.of("flagged", true, "lead_id", leadId, "concern", concern,
                      "severity", severity, "ticket_id", "COMP-" + leadId + "-" + severity.charAt(0));
    }

    // ── Agent factory ─────────────────────────────────────────────────────────

    static String getModel() { return agent != null ? agent.getModel() : ""; }

    private static Agent build(String provider) {
        ToolDefinition crm = ToolDefinition.builder()
            .name("crm.lookup")
            .description("Look up enriched lead data from the CRM by lead ID.")
            .stringParam("lead_id", "Lead identifier, e.g. L-001", true)
            .denyBulkExport(true)
            .handler(a -> crmLookup((String) a.get("lead_id")))
            .build();

        ToolDefinition elig = ToolDefinition.builder()
            .name("eligibility.check")
            .description("Run automated eligibility check against qualification criteria.")
            .numberParam("annual_revenue_usd",    "Annual revenue in USD cents",   true)
            .numberParam("dscr",                  "Debt Service Coverage Ratio",   true)
            .numberParam("outstanding_liens_usd", "Outstanding liens in USD cents", true)
            .numberParam("business_age_months",   "Business age in months",         true)
            .handler(a -> eligibilityCheck(
                (Number) a.get("annual_revenue_usd"), (Number) a.get("dscr"),
                (Number) a.get("outstanding_liens_usd"), (Number) a.get("business_age_months")))
            .build();

        ToolDefinition comp = ToolDefinition.builder()
            .name("compliance.flag")
            .description("Create a compliance flag in the lead management system.")
            .stringParam("lead_id",  "Lead identifier",                      true)
            .stringParam("concern",  "Description of the compliance concern",  true)
            .stringParam("severity", "Severity: LOW, MEDIUM, or HIGH",         true)
            .handler(a -> complianceFlag(
                (String) a.get("lead_id"), (String) a.get("concern"), (String) a.get("severity")))
            .build();

        OutputContract contract = OutputContract.builder()
            .requiredFields("decision", "score", "risk_category", "lead_id",
                            "rationale", "recommended_products", "next_steps", "compliance_flags")
            .failureMode("BLOCKED:")
            .failureMode("insufficient_data")
            .validator(json -> {
                List<Violation> v = new ArrayList<>();
                String dec = (String) json.get("decision");
                if (!Set.of("QUALIFIED","NOT_QUALIFIED","REVIEW_REQUIRED").contains(dec)) {
                    v.add(new Violation(
                        "OUTPUT_CONTRACT: decision must be QUALIFIED|NOT_QUALIFIED|REVIEW_REQUIRED",
                        "schema", "detected", String.valueOf(dec)));
                }
                return v;
            })
            .build();

        return Agent.builder()
            .provider(provider)
            .immutable(IMMUTABLE)
            .capability(CAPABILITY)
            .tool(crm).tool(elig).tool(comp)
            .outputContract(contract)
            .build();
    }

    private AgentManager() {}
}
