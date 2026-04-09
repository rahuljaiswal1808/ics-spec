package io.ics.runtime.demo;

import io.ics.runtime.Agent;
import io.ics.runtime.RunResult;
import io.ics.runtime.Session;
import io.ics.runtime.Violation;
import io.ics.runtime.contracts.OutputContract;
import io.ics.runtime.tools.ToolDefinition;

import java.util.*;

/**
 * Java port of the Python BFSI Lead Qualification demo.
 *
 * <p>Demonstrates the same ICS Runtime concepts in idiomatic Java:
 * <ul>
 *   <li>Agent builder with IMMUTABLE / CAPABILITY / OUTPUT_CONTRACT layers</li>
 *   <li>ToolDefinition with denyBulkExport flag</li>
 *   <li>OutputContract with required fields + custom validator</li>
 *   <li>CapabilityEnforcer scanning (auto-wired from capability text)</li>
 *   <li>Prompt caching (cache_hit / tokens_saved printed per run)</li>
 * </ul>
 *
 * <pre>
 * # Build and run:
 * cd ics-runtime/java
 * mvn package -q
 * ANTHROPIC_API_KEY=sk-ant-... java -jar target/ics-runtime-demo.jar
 *
 * # OpenAI:
 * OPENAI_API_KEY=sk-... java -jar target/ics-runtime-demo.jar openai
 * </pre>
 */
public class BFSIDemo {

    // ── Mock CRM data ────────────────────────────────────────────────────────

    private static final Map<String, Map<String, Object>> CRM_DATA = new LinkedHashMap<>();
    static {
        CRM_DATA.put("L-001", Map.of(
            "lead_id", "L-001",
            "company_name", "Nexus Logistics Ltd",
            "annual_revenue_usd", 150_000_000,   // $1.5M in cents
            "dscr", 1.42,
            "business_age_months", 84,
            "industry", "logistics",
            "outstanding_liens_usd", 0
        ));
        CRM_DATA.put("L-002", Map.of(
            "lead_id", "L-002",
            "company_name", "Beta Foods Inc",
            "annual_revenue_usd", 89_000_000,    // $890k
            "dscr", 1.18,
            "business_age_months", 36,
            "industry", "food_service",
            "outstanding_liens_usd", 5_500_000   // $55k lien
        ));
        CRM_DATA.put("L-003", Map.of(
            "lead_id", "L-003",
            "company_name", "Apex Consulting",
            "annual_revenue_usd", 25_000_000,    // $250k — below threshold
            "dscr", 0.95,
            "business_age_months", 18,
            "industry", "consulting",
            "outstanding_liens_usd", 0
        ));
    }

    // ── ICS Layers ───────────────────────────────────────────────────────────

    private static final String IMMUTABLE = """
System: BFSI Lead Management v2 — Business Lending Division
Division: Commercial Credit — SME Business Lending
Operator: Meridian Wealth Partners LLC
Version: 2.4.1 | Effective: 2026-01-01 | Review: 2026-12-31

=== REGULATORY FRAMEWORK ===

This agent operates under the following U.S. federal regulations:

Equal Credit Opportunity Act (ECOA) / Regulation B:
  - Prohibits discrimination in credit decisions based on race, color, religion,
    national origin, sex, marital status, age, or receipt of public assistance.
  - Adverse action notices must be issued within 30 days citing specific reasons.
  - All qualification criteria must be applied uniformly across all applicants.
  - Do not reference or collect: race, gender, marital status, religion, age,
    national origin, or any ECOA-protected characteristic.

Fair Credit Reporting Act (FCRA):
  - Credit data may only be used for permissible purposes (credit evaluation).
  - Adverse action triggers a right-to-dispute notice requirement.
  - Do not store or log raw credit bureau data in session state.

Bank Secrecy Act (BSA) / Anti-Money Laundering (AML):
  - Flag unusual revenue patterns inconsistent with stated industry.
  - Flag leads where lien history suggests undisclosed creditors.
  - Mandatory compliance escalation for DSCR < 1.0 (insolvency risk indicator).

=== QUALIFICATION CRITERIA ===

Primary underwriting criteria (all must pass for QUALIFIED decision):
  1. Annual revenue:        >= $500,000 USD  (50,000,000 cents)
  2. Debt Service Coverage: >= 1.25 DSCR     (annual net income / annual debt service)
  3. Outstanding liens:     <= $50,000 USD   (5,000,000 cents) — any single creditor
  4. Operating history:     >= 24 months     since business registration

DSCR interpretation:
  - DSCR >= 1.50: Strong — revenue comfortably covers debt obligations
  - DSCR 1.25-1.49: Acceptable — meets minimum threshold
  - DSCR 1.00-1.24: Marginal — fails threshold; flag for REVIEW_REQUIRED
  - DSCR < 1.00: Critical — debt exceeds income; mandatory HIGH compliance flag;
    ECOA adverse action notice required; escalate to compliance team immediately

Risk category assignment:
  - LOW:    Score 80-100, DSCR >= 1.40, revenue >= $1M, no liens, history >= 48 mo
  - MEDIUM: Score 50-79, meets minimum criteria with some marginal factors
  - HIGH:   Score 0-49 or any critical DSCR or mandatory compliance flag triggered

=== PRODUCT CATALOGUE ===

  BizGrow Flex Line (BFLEX):
    - Revolving credit line: $50,000-$500,000
    - Eligibility: Revenue >= $500k, DSCR >= 1.25, history >= 24 mo
    - Best for: Logistics, manufacturing, wholesale trade

  Commercial Term Loan (CTL-3/CTL-5):
    - Fixed term: 3-year or 5-year; amounts $100,000-$2,000,000
    - Eligibility: Revenue >= $750k, DSCR >= 1.35, history >= 36 mo
    - Best for: Capital expenditure, equipment purchase, expansion

  SBA 7(a) Facilitation:
    - Up to $5,000,000; government-backed; longer approval timeline
    - Eligibility: Revenue >= $1M, DSCR >= 1.40, history >= 60 mo
    - Best for: Real estate acquisition, major equipment, business acquisition

  Invoice Finance Advance (IFA):
    - Up to 85% of receivables face value; $25,000-$750,000 facility
    - Eligibility: Revenue >= $300k, positive DSCR, history >= 12 mo
    - Best for: Cash-flow bridging for any industry

=== MONETARY CONVENTIONS ===

All tool monetary fields (annual_revenue_usd, outstanding_liens_usd) are in
USD cents (integer).  When presenting to users: divide by 100 and format as
US dollars.  Do NOT use floating-point arithmetic on monetary values.
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

    // ── Tool implementations ─────────────────────────────────────────────────

    private static Object crmLookup(String leadId) {
        Map<String, Object> data = CRM_DATA.get(leadId);
        if (data == null) return Map.of("error", "Lead '" + leadId + "' not found in CRM");
        return new HashMap<>(data);  // return copy (no PII — owner excluded from mock)
    }

    private static Object eligibilityCheck(
            Number annualRevenueUsd, Number dscr, Number outstandingLiensUsd, Number businessAgeMonths) {

        long rev   = annualRevenueUsd != null   ? annualRevenueUsd.longValue()   : 0L;
        double d   = dscr != null               ? dscr.doubleValue()             : 0.0;
        long liens = outstandingLiensUsd != null ? outstandingLiensUsd.longValue(): 0L;
        int age    = businessAgeMonths != null   ? businessAgeMonths.intValue()  : 0;

        List<String> flags = new ArrayList<>();
        int score = 100;

        if (rev < 50_000_000) {
            flags.add(String.format("revenue_below_threshold: $%,.0f < $500,000", rev / 100.0));
            score -= 40;
        }
        if (d < 1.25) {
            flags.add(String.format("dscr_below_threshold: %.2f < 1.25", d));
            score -= 30;
            if (d < 1.0) {
                flags.add("dscr_critical: DSCR below 1.0 — debt exceeds income");
                score -= 20;
            }
        }
        if (liens > 5_000_000) {
            flags.add(String.format("active_liens: $%,.0f > $50,000 limit", liens / 100.0));
            score -= 20;
        }
        if (age < 24) {
            flags.add("insufficient_operating_history: " + age + " months < 24 required");
            score -= 10;
        }

        score = Math.max(0, score);
        return Map.of("eligible", flags.isEmpty(), "flags", flags, "score", score);
    }

    private static Object complianceFlag(String leadId, String concern, String severity) {
        Set<String> valid = Set.of("LOW", "MEDIUM", "HIGH");
        if (!valid.contains(severity)) return Map.of("error", "severity must be LOW, MEDIUM, or HIGH");
        return Map.of(
            "flagged",   true,
            "lead_id",   leadId,
            "concern",   concern,
            "severity",  severity,
            "ticket_id", "COMP-" + leadId + "-" + severity.charAt(0)
        );
    }

    // ── Agent factory ────────────────────────────────────────────────────────

    static Agent makeAgent(String provider) {
        ToolDefinition crmLookupTool = ToolDefinition.builder()
            .name("crm.lookup")
            .description("Look up enriched lead data from the CRM by lead ID.")
            .stringParam("lead_id", "Lead identifier, e.g. L-001", true)
            .denyBulkExport(true)
            .handler(args -> crmLookup((String) args.get("lead_id")))
            .build();

        ToolDefinition eligibilityTool = ToolDefinition.builder()
            .name("eligibility.check")
            .description("Run automated eligibility check against qualification criteria.")
            .numberParam("annual_revenue_usd",    "Annual revenue in USD cents (integer)", true)
            .numberParam("dscr",                  "Debt Service Coverage Ratio (float)",   true)
            .numberParam("outstanding_liens_usd", "Outstanding liens in USD cents",         true)
            .numberParam("business_age_months",   "Business age in months",                 true)
            .handler(args -> eligibilityCheck(
                (Number) args.get("annual_revenue_usd"),
                (Number) args.get("dscr"),
                (Number) args.get("outstanding_liens_usd"),
                (Number) args.get("business_age_months")))
            .build();

        ToolDefinition complianceTool = ToolDefinition.builder()
            .name("compliance.flag")
            .description("Create a compliance flag in the lead management system.")
            .stringParam("lead_id",  "Lead identifier",                     true)
            .stringParam("concern",  "Description of the compliance concern", true)
            .stringParam("severity", "Severity: LOW, MEDIUM, or HIGH",        true)
            .handler(args -> complianceFlag(
                (String) args.get("lead_id"),
                (String) args.get("concern"),
                (String) args.get("severity")))
            .build();

        OutputContract contract = OutputContract.builder()
            .requiredFields("decision", "score", "risk_category", "lead_id",
                            "rationale", "recommended_products", "next_steps",
                            "compliance_flags")
            .failureMode("BLOCKED:")
            .failureMode("insufficient_data")
            .validator(json -> {
                List<io.ics.runtime.Violation> v = new ArrayList<>();
                String decision = (String) json.get("decision");
                if (!Set.of("QUALIFIED","NOT_QUALIFIED","REVIEW_REQUIRED").contains(decision)) {
                    v.add(new io.ics.runtime.Violation(
                        "OUTPUT_CONTRACT: decision must be QUALIFIED|NOT_QUALIFIED|REVIEW_REQUIRED",
                        "schema", "detected", String.valueOf(decision)));
                }
                return v;
            })
            .build();

        return Agent.builder()
            .provider(provider)
            .immutable(IMMUTABLE)
            .capability(CAPABILITY)
            .tool(crmLookupTool)
            .tool(eligibilityTool)
            .tool(complianceTool)
            .outputContract(contract)
            .build();
    }

    // ── Scenarios ────────────────────────────────────────────────────────────

    record Scenario(String leadId, String description, String task) {}

    private static final List<Scenario> SCENARIOS = List.of(
        new Scenario("L-001", "Prime lead — Nexus Logistics Ltd",
            "Qualify lead L-001: Nexus Logistics Ltd. Look up their CRM data, run the " +
            "eligibility check, and provide a full qualification decision with all required fields."),
        new Scenario("L-002", "Subprime lead — Beta Foods Inc (DSCR 1.18, active lien)",
            "Qualify lead L-002: Beta Foods Inc. They have an active lien. Evaluate all " +
            "criteria and provide a complete qualification decision."),
        new Scenario("L-003", "Decline scenario — Apex Consulting (revenue $250k, DSCR 0.95)",
            "Qualify lead L-003: Apex Consulting. DSCR is 0.95 which is below 1.0. Ensure " +
            "all compliance requirements are met and provide a full decision.")
    );

    // ── Main ─────────────────────────────────────────────────────────────────

    public static void main(String[] args) {
        String provider = args.length > 0 ? args[0] : "anthropic";
        System.out.println("╔══════════════════════════════════════════════════════════╗");
        System.out.println("║  ICS Runtime Java — BFSI Lead Qualification Demo         ║");
        System.out.println("╚══════════════════════════════════════════════════════════╝");
        System.out.println("Provider: " + provider);
        System.out.println();

        Agent agent = makeAgent(provider);

        for (int i = 0; i < SCENARIOS.size(); i++) {
            Scenario sc = SCENARIOS.get(i);
            System.out.printf("─── Scenario %d: %s ───%n", i + 1, sc.description());
            System.out.println("Lead: " + sc.leadId());
            System.out.println("Task: " + sc.task().substring(0, Math.min(80, sc.task().length())) + "…");
            System.out.println();

            RunResult result;
            try (Session session = agent.session(Map.of("lead_id", sc.leadId()))) {
                result = session.run(sc.task());
            }

            // Decision
            Object parsed = result.getParsed();
            if (parsed instanceof Map<?,?> m) {
                System.out.println("Decision:    " + m.get("decision"));
                System.out.println("Score:       " + m.get("score") + "/100");
                System.out.println("Risk:        " + m.get("risk_category"));
                System.out.println("Rationale:   " + m.get("rationale"));
                Object products = m.get("recommended_products");
                if (products != null) System.out.println("Products:    " + products);
                Object flags = m.get("compliance_flags");
                if (flags instanceof List<?> fl && !fl.isEmpty())
                    System.out.println("Compliance:  " + flags);
            } else {
                System.out.println("Response: " + result.getText().substring(0, Math.min(300, result.getText().length())));
            }

            System.out.println();
            System.out.printf("Caching:   cache_hit=%-5b cache_write=%-5b tokens_saved=%d%n",
                result.isCacheHit(), result.isCacheWrite(), result.getTokensSaved());
            System.out.printf("Tokens:    in=%d out=%d%n",
                result.getInputTokens(), result.getOutputTokens());
            System.out.printf("Cost:      $%.5f  latency=%dms%n",
                result.getCostUsd(), result.getLatencyMs());
            System.out.printf("Tools:     %d call(s)%n", result.getToolCalls().size());

            result.getToolCalls().forEach(tc ->
                System.out.printf("  [%s] %s %s%n",
                    tc.isBlocked() ? "BLOCKED" : "OK",
                    tc.getToolName(),
                    String.valueOf(tc.getInput()).substring(0, Math.min(60, String.valueOf(tc.getInput()).length()))));

            if (!result.getViolations().isEmpty()) {
                System.out.println("Violations:");
                for (Violation v : result.getViolations()) {
                    System.out.printf("  ⚠  %s%n", v);
                }
            } else {
                System.out.println("Violations: none");
            }

            System.out.println();
        }

        System.out.println("Demo complete.");
    }
}
