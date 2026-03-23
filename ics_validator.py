#!/usr/bin/env python3
"""
ICS Reference Validator
Validates instructions against the Instruction Contract Specification v0.1.

Usage:
    python ics_validator.py <instruction_file>
    python ics_validator.py --stdin   (reads from stdin)
    python ics_validator.py --test    (runs built-in test suite)

Exit codes:
    0  compliant
    1  non-compliant
    2  usage error
"""

import re
import sys
import json
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LAYER_ORDER = [
    "IMMUTABLE_CONTEXT",
    "CAPABILITY_DECLARATION",
    "SESSION_STATE",
    "TASK_PAYLOAD",
    "OUTPUT_CONTRACT",
]

# Matches a well-formed directive line per the §3.2 scope grammar.
# Groups: keyword, action, qualifier_keyword (opt), target (opt), condition (opt)
DIRECTIVE_PATTERN = re.compile(
    r"^\s*(ALLOW|DENY|REQUIRE)\s+.+", re.IGNORECASE
)

# Qualifier keywords that introduce a scope target per the §3.2 grammar.
_QUALIFIER_WORDS = {"WITHIN", "ON", "WITH", "UNLESS"}


def _check_directive_grammar(line: str):
    """
    Validate a directive line against the §3.2 scope grammar.
    Returns an error string, or None if the line is valid.

    Grammar (enforced):
        directive ::= KEYWORD action [qualifier] [IF condition]
        qualifier ::= QWORD target
        QWORD     ::= WITHIN | ON | WITH | UNLESS
        action, target, condition ::= one or more whitespace-separated words
    """
    tokens = line.split()
    # tokens[0] is the directive keyword (already confirmed by DIRECTIVE_PATTERN)
    rest = tokens[1:]

    if not rest:
        return f"Directive '{tokens[0]}' has no action"

    # Find the first token that is an exact qualifier keyword (case-insensitive).
    # We match whole tokens only, so 'ON' in 'modification' is never a qualifier.
    qual_idx = next(
        (i for i, t in enumerate(rest) if t.upper() in _QUALIFIER_WORDS),
        None,
    )

    # Find IF token — must appear after the qualifier (if any).
    search_from = (qual_idx + 1) if qual_idx is not None else 0
    if_idx = next(
        (search_from + i for i, t in enumerate(rest[search_from:]) if t.upper() == "IF"),
        None,
    )

    # Action must be non-empty (tokens between keyword and qualifier/IF/end).
    action_end = qual_idx if qual_idx is not None else (if_idx if if_idx is not None else len(rest))
    if action_end == 0:
        return "Action must be non-empty after the directive keyword"

    # Qualifier must be followed by a non-empty target.
    if qual_idx is not None:
        target_end = if_idx if if_idx is not None else len(rest)
        if qual_idx + 1 >= target_end:
            qword = rest[qual_idx]
            return (
                f"Qualifier keyword '{qword}' must be followed by a non-empty target"
            )

    # IF must be followed by a non-empty condition.
    if if_idx is not None and if_idx + 1 >= len(rest):
        return "'IF' must be followed by a non-empty condition"

    return None

OUTPUT_CONTRACT_FIELDS = {"format", "schema", "variance", "on_failure"}

BOUNDARY_OPEN  = re.compile(r"^###ICS:([A-Z_]+)###\s*$")
BOUNDARY_CLOSE = re.compile(r"^###END:([A-Z_]+)###\s*$")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class Violation:
    step: int
    rule: str
    message: str

    def __str__(self):
        return f"  [Step {self.step}] {self.rule}: {self.message}"


@dataclass
class ValidationResult:
    compliant: bool
    violations: list[Violation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_violation(self, step: int, rule: str, message: str):
        self.violations.append(Violation(step, rule, message))
        self.compliant = False

    def add_warning(self, message: str):
        self.warnings.append(message)

    def report(self) -> str:
        lines = []
        if self.compliant:
            lines.append("COMPLIANT")
        else:
            lines.append("NON-COMPLIANT")
            lines.append(f"{len(self.violations)} violation(s) found:")
            for v in self.violations:
                lines.append(str(v))
        if self.warnings:
            lines.append(f"{len(self.warnings)} warning(s):")
            for w in self.warnings:
                lines.append(f"  [WARN] {w}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "compliant": self.compliant,
            "violations": [
                {"step": v.step, "rule": v.rule, "message": v.message}
                for v in self.violations
            ],
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

@dataclass
class Layer:
    name: str
    content: str
    start_line: int
    end_line: int


def parse_layers(text: str) -> tuple[list[Layer], list[str]]:
    """
    Extract layers from instruction text.
    Returns (layers, parse_errors).
    Parse errors are structural problems that prevent further validation.
    """
    layers = []
    errors = []
    lines = text.splitlines()

    open_layer: Optional[str] = None
    open_line: int = 0
    content_lines: list[str] = []

    for i, line in enumerate(lines, start=1):
        open_match  = BOUNDARY_OPEN.match(line)
        close_match = BOUNDARY_CLOSE.match(line)

        if open_match:
            layer_name = open_match.group(1)
            if open_layer is not None:
                errors.append(
                    f"Line {i}: opened ###ICS:{layer_name}### "
                    f"before ###END:{open_layer}### was seen"
                )
            else:
                open_layer   = layer_name
                open_line    = i
                content_lines = []

        elif close_match:
            layer_name = close_match.group(1)
            if open_layer is None:
                errors.append(
                    f"Line {i}: ###END:{layer_name}### "
                    f"found without matching ###ICS:{layer_name}###"
                )
            elif layer_name != open_layer:
                errors.append(
                    f"Line {i}: ###END:{layer_name}### "
                    f"does not match open layer {open_layer}"
                )
            else:
                layers.append(Layer(
                    name=open_layer,
                    content="\n".join(content_lines).strip(),
                    start_line=open_line,
                    end_line=i,
                ))
                open_layer    = None
                content_lines = []

        elif open_layer is not None:
            content_lines.append(line)

    if open_layer is not None:
        errors.append(
            f"Layer {open_layer} opened at line {open_line} "
            f"but ###END:{open_layer}### was never found"
        )

    return layers, errors


# ---------------------------------------------------------------------------
# Output contract types, parsers, and validators
# ---------------------------------------------------------------------------

@dataclass
class OutputContract:
    format: str
    schema: str
    variance: str
    on_failure: str


def _parse_output_contract_fields(content: str) -> dict:
    """
    Parse multiline key: value fields from an OUTPUT_CONTRACT layer body.
    Continuation lines (indented) are appended to the current field value.
    """
    fields = {}
    current_key = None
    current_lines: list[str] = []

    for line in content.splitlines():
        key_match = re.match(r"^([\w_]+)\s*:\s*(.*)", line)
        if key_match:
            if current_key is not None:
                fields[current_key] = "\n".join(current_lines).strip()
            current_key = key_match.group(1).lower()
            first_val = key_match.group(2)
            current_lines = [first_val] if first_val else []
        elif current_key is not None:
            current_lines.append(line)

    if current_key is not None:
        fields[current_key] = "\n".join(current_lines).strip()

    return fields


def parse_output_contract(
    ics_text: str,
) -> "tuple[Optional[OutputContract], list[str]]":
    """
    Extract and return the OUTPUT_CONTRACT from an ICS document.
    Returns (OutputContract, []) on success or (None, [errors]) on failure.
    """
    layers, parse_errors = parse_layers(ics_text)
    if parse_errors:
        return None, parse_errors

    oc_layers = [l for l in layers if l.name == "OUTPUT_CONTRACT"]
    if not oc_layers:
        return None, ["OUTPUT_CONTRACT layer not found in ICS document"]

    fields = _parse_output_contract_fields(oc_layers[0].content)

    missing = OUTPUT_CONTRACT_FIELDS - set(fields.keys())
    if missing:
        return None, [
            f"OUTPUT_CONTRACT is missing required field(s): {', '.join(sorted(missing))}"
        ]

    return OutputContract(
        format=fields["format"],
        schema=fields["schema"],
        variance=fields["variance"],
        on_failure=fields["on_failure"],
    ), []


def _is_valid_json(text: str) -> bool:
    try:
        json.loads(text.strip())
        return True
    except (json.JSONDecodeError, ValueError):
        return False


def _is_valid_unified_diff(text: str) -> bool:
    """Check that the text contains at least one unified diff hunk."""
    lines = text.splitlines()
    has_minus = any(l.startswith("--- ") for l in lines)
    has_plus  = any(l.startswith("+++ ") for l in lines)
    has_hunk  = any(l.startswith("@@ ") for l in lines)
    return has_minus and has_plus and has_hunk


# Maps normalised format name -> validator callable(text) -> bool
_FORMAT_VALIDATORS: dict = {
    "json":         _is_valid_json,
    "unified diff": _is_valid_unified_diff,
    "unified-diff": _is_valid_unified_diff,
    "diff":         _is_valid_unified_diff,
}


def _is_blocked_response(text: str) -> bool:
    return text.lstrip().startswith("BLOCKED:")


def _validate_blocked_response(
    output: str, contract: OutputContract, result: ValidationResult
) -> ValidationResult:
    """Validate a BLOCKED: response against the on_failure contract field."""
    on_failure_lower = contract.on_failure.lower()

    non_empty_lines = [l for l in output.splitlines() if l.strip()]
    requires_single_line = (
        "one line" in on_failure_lower or "single" in on_failure_lower
    )
    if requires_single_line and len(non_empty_lines) > 1:
        result.add_violation(
            0,
            "OUTPUT_CONTRACT on_failure",
            f"BLOCKED response must be a single line per on_failure spec, "
            f"but got {len(non_empty_lines)} non-empty lines",
        )

    prohibits_markdown = (
        "no markdown" in on_failure_lower
        or "no bold" in on_failure_lower
        or "no bold asterisks" in on_failure_lower
    )
    if prohibits_markdown and re.search(r"\*\*|__|#{1,6} ", output):
        result.add_violation(
            0,
            "OUTPUT_CONTRACT on_failure",
            "BLOCKED response contains markdown formatting, prohibited by on_failure spec",
        )

    if "blocked:" not in on_failure_lower:
        result.add_warning(
            "Output is a BLOCKED: response but on_failure spec does not reference 'BLOCKED:'"
        )

    return result


def validate_output(ics_text: str, llm_output: str) -> ValidationResult:
    """
    Validate an LLM output against the OUTPUT_CONTRACT declared in an ICS document.

    Returns a ValidationResult with:
    - violations for definite contract breaches
    - warnings for conditions that require human review
    """
    result = ValidationResult(compliant=True)

    contract, errors = parse_output_contract(ics_text)
    if errors:
        for err in errors:
            result.add_violation(0, "OUTPUT_CONTRACT parse error", err)
        return result

    output = llm_output.strip()

    # BLOCKED responses are validated against on_failure, not format
    if _is_blocked_response(output):
        return _validate_blocked_response(output, contract, result)

    # Format validation
    fmt_key = contract.format.strip().lower()
    validator = _FORMAT_VALIDATORS.get(fmt_key)
    if validator is not None:
        if not validator(output):
            result.add_violation(
                0,
                "OUTPUT_CONTRACT format",
                f"Output does not conform to declared format '{contract.format}'",
            )
    else:
        result.add_warning(
            f"Format '{contract.format}' has no automatic validator; "
            "manual review required"
        )

    return result


# ---------------------------------------------------------------------------
# Validation steps
# ---------------------------------------------------------------------------

def step1_all_layers_present(layers: list[Layer], result: ValidationResult):
    """Step 1: All five layer boundary tags are present and well-formed."""
    found_names = {l.name for l in layers}
    for name in LAYER_ORDER:
        if name not in found_names:
            result.add_violation(
                1, "§5.2 Step 1",
                f"Required layer {name} is missing"
            )
    unknown = found_names - set(LAYER_ORDER)
    for name in unknown:
        result.add_violation(
            1, "§3.6",
            f"Unknown layer name: {name}. "
            f"Valid names are: {', '.join(LAYER_ORDER)}"
        )


def step2_canonical_order(layers: list[Layer], result: ValidationResult):
    """Step 2: Layers appear in canonical order."""
    seen = [l.name for l in layers if l.name in LAYER_ORDER]
    expected = [name for name in LAYER_ORDER if name in {l.name for l in layers}]

    if seen != expected:
        result.add_violation(
            2, "§4.1",
            f"Layers are out of order. "
            f"Found: {', '.join(seen)}. "
            f"Expected: {', '.join(expected)}"
        )


def step3_session_state_clear(layers: list[Layer], result: ValidationResult):
    """
    Step 3: SESSION_STATE, if containing CLEAR, contains no other content.
    """
    ss_layers = [l for l in layers if l.name == "SESSION_STATE"]
    for layer in ss_layers:
        content_lines = [ln.strip() for ln in layer.content.splitlines() if ln.strip()]
        has_clear = any(ln == "CLEAR" for ln in content_lines)
        if has_clear and len(content_lines) > 1:
            result.add_violation(
                3, "§3.3",
                "SESSION_STATE contains CLEAR alongside other content. "
                "A SESSION_STATE layer containing CLEAR must contain only CLEAR."
            )


def step4_no_redefinition(layers: list[Layer], result: ValidationResult):
    """
    Step 4: No layer restates, redefines, or contradicts a preceding layer.
    This step performs heuristic checks where rules are mechanically enforceable.
    Full semantic contradiction checking requires human review.
    """
    layer_map = {l.name: l for l in layers}

    # Check TASK_PAYLOAD does not override CAPABILITY_DECLARATION
    # by embedding ALLOW/DENY/REQUIRE directives
    if "TASK_PAYLOAD" in layer_map:
        tp = layer_map["TASK_PAYLOAD"]
        for line in tp.content.splitlines():
            if DIRECTIVE_PATTERN.match(line):
                result.add_violation(
                    4, "§3.4 / §4.2",
                    f"TASK_PAYLOAD contains a capability directive: '{line.strip()}'. "
                    f"Directives belong in CAPABILITY_DECLARATION."
                )

    # Check SESSION_STATE does not redefine IMMUTABLE_CONTEXT
    # by heuristically checking for identical key lines
    if "IMMUTABLE_CONTEXT" in layer_map and "SESSION_STATE" in layer_map:
        ic_lines = {
            ln.strip().lower()
            for ln in layer_map["IMMUTABLE_CONTEXT"].content.splitlines()
            if ":" in ln and ln.strip()
        }
        ss_lines = [
            ln.strip()
            for ln in layer_map["SESSION_STATE"].content.splitlines()
            if ":" in ln and ln.strip() and ln.strip() != "CLEAR"
        ]
        for ss_line in ss_lines:
            if ss_line.lower() in ic_lines:
                result.add_violation(
                    4, "§3.3 / §4.2",
                    f"SESSION_STATE appears to restate a line from IMMUTABLE_CONTEXT: "
                    f"'{ss_line}'"
                )


def step5_capability_declaration_syntax(
    layers: list[Layer], result: ValidationResult
):
    """
    Step 5: CAPABILITY_DECLARATION uses only ALLOW, DENY, or REQUIRE directives.
    Blank lines and comment lines (starting with #) are permitted.
    Also enforces the §3.2 scope grammar: qualifier keywords must be followed
    by a non-empty target; IF must be followed by a non-empty condition.
    """
    cd_layers = [l for l in layers if l.name == "CAPABILITY_DECLARATION"]
    for layer in cd_layers:
        for line in layer.content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            if not DIRECTIVE_PATTERN.match(stripped):
                result.add_violation(
                    5, "§3.2",
                    f"CAPABILITY_DECLARATION contains a line that is not a valid "
                    f"ALLOW, DENY, or REQUIRE directive: '{stripped}'"
                )
                continue
            # Enforce scope grammar (§3.2 normative)
            grammar_error = _check_directive_grammar(stripped)
            if grammar_error:
                result.add_violation(
                    5, "§3.2 (scope grammar)",
                    f"{grammar_error}: '{stripped}'"
                )


def step7_allow_deny_overlap(layers: list[Layer], result: ValidationResult):
    """
    Step 7: Warn when a DENY directive's WITHIN target is a path prefix of an
    ALLOW directive's WITHIN target (or equal to it).

    Per §3.2, the more specific ALLOW takes precedence — but models may still
    apply the general DENY and produce a false BLOCK.  This check surfaces
    such overlaps so authors can audit intended behaviour before deployment.

    Example:
        DENY  modification of infra/              ← general
        ALLOW new Alembic migration file creation WITHIN infra/migrations/  ← specific

    The ALLOW target 'infra/migrations/' starts with the DENY target 'infra/',
    so a warning is emitted.
    """
    cd_layers = [l for l in layers if l.name == "CAPABILITY_DECLARATION"]
    for layer in cd_layers:
        allows_with: list[tuple[str, str]] = []   # (directive line, WITHIN target)
        denys_with:  list[tuple[str, str]] = []

        for line in layer.content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            tokens = stripped.split()
            if not tokens:
                continue
            keyword = tokens[0].upper()
            if keyword not in ("ALLOW", "DENY"):
                continue

            # Extract the WITHIN target (everything between WITHIN and IF/end).
            within_idx = next(
                (i for i, t in enumerate(tokens) if t.upper() == "WITHIN"), None
            )
            if within_idx is None:
                continue
            target_tokens = []
            for t in tokens[within_idx + 1:]:
                if t.upper() == "IF":
                    break
                target_tokens.append(t)
            if not target_tokens:
                continue
            target = " ".join(target_tokens)

            if keyword == "ALLOW":
                allows_with.append((stripped, target))
            else:
                denys_with.append((stripped, target))

        # For every (DENY, ALLOW) pair check whether the ALLOW target is a
        # sub-path of the DENY target.
        for deny_line, deny_target in denys_with:
            deny_norm = deny_target.rstrip("/")
            for allow_line, allow_target in allows_with:
                allow_norm = allow_target.rstrip("/")
                if allow_norm == deny_norm or allow_norm.startswith(deny_norm + "/"):
                    result.add_warning(
                        f"ALLOW/DENY specificity overlap (§3.2): "
                        f"DENY targets '{deny_target}' which is a path prefix of "
                        f"ALLOW target '{allow_target}'. "
                        f"Per §3.2 the more specific ALLOW takes precedence, but "
                        f"models may apply the general DENY. Audit intended behaviour. "
                        f"DENY directive: '{deny_line}' | "
                        f"ALLOW directive: '{allow_line}'"
                    )


def step6_output_contract_fields(
    layers: list[Layer], result: ValidationResult
):
    """
    Step 6: OUTPUT_CONTRACT contains all four required fields.
    Fields are detected by key: value patterns at the start of a line.
    """
    oc_layers = [l for l in layers if l.name == "OUTPUT_CONTRACT"]
    for layer in oc_layers:
        found_fields = set()
        for line in layer.content.splitlines():
            stripped = line.strip()
            match = re.match(r"^([\w_]+)\s*:", stripped)
            if match:
                found_fields.add(match.group(1).lower())
        missing = OUTPUT_CONTRACT_FIELDS - found_fields
        for f in sorted(missing):
            result.add_violation(
                6, "§3.5",
                f"OUTPUT_CONTRACT is missing required field: '{f}'"
            )


# ---------------------------------------------------------------------------
# Main validator
# ---------------------------------------------------------------------------

def validate(text: str) -> ValidationResult:
    result = ValidationResult(compliant=True)

    layers, parse_errors = parse_layers(text)

    if parse_errors:
        for err in parse_errors:
            result.add_violation(1, "§3.6 (parse error)", err)
        # Cannot proceed reliably past step 1 if parsing failed
        return result

    step1_all_layers_present(layers, result)
    if not result.compliant:
        return result

    step2_canonical_order(layers, result)
    step3_session_state_clear(layers, result)
    step4_no_redefinition(layers, result)
    step5_capability_declaration_syntax(layers, result)
    step6_output_contract_fields(layers, result)
    step7_allow_deny_overlap(layers, result)

    return result


# ---------------------------------------------------------------------------
# Built-in test suite
# ---------------------------------------------------------------------------

COMPLIANT_EXAMPLE = """
###ICS:IMMUTABLE_CONTEXT###
System: order management service
Language: Python 3.11
Repo structure:
  src/orders/       (business logic)
  src/orders/api/   (HTTP handlers)
  tests/            (pytest test suite)
Invariant: all monetary values stored as integer cents
###END:IMMUTABLE_CONTEXT###

###ICS:CAPABILITY_DECLARATION###
ALLOW   file modification WITHIN src/orders/
ALLOW   file creation WITHIN src/orders/ IF new file has corresponding test
DENY    modification of src/orders/api/
DENY    modification of any file WITHIN tests/
DENY    introduction of new external dependencies
REQUIRE type annotations ON all new functions
REQUIRE docstring ON all new public functions
###END:CAPABILITY_DECLARATION###

###ICS:SESSION_STATE###
[2024-01-15T09:30Z] Confirmed: discount logic lives in apply_discount() in src/orders/pricing.py
[2024-01-15T09:45Z] Decision: percentage and flat discounts to be handled by separate functions
###END:SESSION_STATE###

###ICS:TASK_PAYLOAD###
Split apply_discount() into two functions: apply_percentage_discount() and apply_flat_discount().
Preserve existing call sites by having apply_discount() delegate to the appropriate function
based on the discount type field.
###END:TASK_PAYLOAD###

###ICS:OUTPUT_CONTRACT###
format:     unified diff
schema:     standard unified diff against current HEAD; one diff block per modified file
variance:   diff header comments are permitted; no other variance allowed
on_failure: return plain text block with prefix "BLOCKED:" followed by a single-sentence description
###END:OUTPUT_CONTRACT###
""".strip()

CLEAR_EXAMPLE = """
###ICS:IMMUTABLE_CONTEXT###
System: test system
###END:IMMUTABLE_CONTEXT###

###ICS:CAPABILITY_DECLARATION###
ALLOW read access
###END:CAPABILITY_DECLARATION###

###ICS:SESSION_STATE###
CLEAR
###END:SESSION_STATE###

###ICS:TASK_PAYLOAD###
Run the analysis.
###END:TASK_PAYLOAD###

###ICS:OUTPUT_CONTRACT###
format:     JSON
schema:     { "result": "string" }
variance:   none
on_failure: return error string
###END:OUTPUT_CONTRACT###
""".strip()

TESTS = [
    {
        "name": "Compliant example passes",
        "input": COMPLIANT_EXAMPLE,
        "expect_compliant": True,
        "expect_violation_rules": [],
    },
    {
        "name": "CLEAR session state passes",
        "input": CLEAR_EXAMPLE,
        "expect_compliant": True,
        "expect_violation_rules": [],
    },
    {
        "name": "Missing layer is rejected",
        "input": COMPLIANT_EXAMPLE.replace(
            "###ICS:SESSION_STATE###\n"
            "[2024-01-15T09:30Z] Confirmed: discount logic lives in apply_discount() in src/orders/pricing.py\n"
            "[2024-01-15T09:45Z] Decision: percentage and flat discounts to be handled by separate functions\n"
            "###END:SESSION_STATE###",
            ""
        ),
        "expect_compliant": False,
        "expect_violation_rules": ["§5.2 Step 1"],
    },
    {
        "name": "Out-of-order layers are rejected",
        "input": (
            "###ICS:IMMUTABLE_CONTEXT###\nSystem: test\n###END:IMMUTABLE_CONTEXT###\n"
            "###ICS:TASK_PAYLOAD###\nDo something.\n###END:TASK_PAYLOAD###\n"
            "###ICS:CAPABILITY_DECLARATION###\nALLOW read\n###END:CAPABILITY_DECLARATION###\n"
            "###ICS:SESSION_STATE###\nSome state.\n###END:SESSION_STATE###\n"
            "###ICS:OUTPUT_CONTRACT###\nformat: JSON\nschema: {}\nvariance: none\non_failure: error\n###END:OUTPUT_CONTRACT###"
        ),
        "expect_compliant": False,
        "expect_violation_rules": ["§4.1"],
    },
    {
        "name": "CLEAR with extra content is rejected",
        "input": CLEAR_EXAMPLE.replace(
            "CLEAR",
            "CLEAR\n[2024-01-20T15:00Z] New window: past 30 days"
        ),
        "expect_compliant": False,
        "expect_violation_rules": ["§3.3"],
    },
    {
        "name": "Invalid directive in CAPABILITY_DECLARATION is rejected",
        "input": COMPLIANT_EXAMPLE.replace(
            "REQUIRE docstring ON all new public functions",
            "REQUIRE docstring ON all new public functions\nDon't touch the database."
        ),
        "expect_compliant": False,
        "expect_violation_rules": ["§3.2"],
    },
    {
        "name": "Missing OUTPUT_CONTRACT fields are rejected",
        "input": COMPLIANT_EXAMPLE.replace(
            "format:     unified diff\n"
            "schema:     standard unified diff against current HEAD; one diff block per modified file\n"
            "variance:   diff header comments are permitted; no other variance allowed\n"
            "on_failure: return plain text block with prefix \"BLOCKED:\" followed by a single-sentence description",
            "format:     unified diff\n"
            "schema:     standard unified diff against current HEAD"
        ),
        "expect_compliant": False,
        "expect_violation_rules": ["§3.5"],
    },
    {
        "name": "Directive in TASK_PAYLOAD is rejected",
        "input": COMPLIANT_EXAMPLE.replace(
            "Split apply_discount() into two functions",
            "DENY modification of tests/\nSplit apply_discount() into two functions"
        ),
        "expect_compliant": False,
        "expect_violation_rules": ["§3.4 / §4.2"],
    },
    {
        "name": "Directive with WITHIN and no target is rejected",
        "input": COMPLIANT_EXAMPLE.replace(
            "ALLOW   file modification WITHIN src/orders/",
            "ALLOW   file modification WITHIN"
        ),
        "expect_compliant": False,
        "expect_violation_rules": ["§3.2 (scope grammar)"],
    },
    {
        "name": "Directive with IF and no condition is rejected",
        "input": COMPLIANT_EXAMPLE.replace(
            "ALLOW   file creation WITHIN src/orders/ IF new file has corresponding test",
            "ALLOW   file creation WITHIN src/orders/ IF"
        ),
        "expect_compliant": False,
        "expect_violation_rules": ["§3.2 (scope grammar)"],
    },
    {
        "name": "Directive with WITHIN and valid target passes",
        "input": COMPLIANT_EXAMPLE,
        "expect_compliant": True,
        "expect_violation_rules": [],
    },
    {
        "name": "Unknown layer name is rejected",
        "input": COMPLIANT_EXAMPLE + "\n###ICS:CUSTOM_LAYER###\nsome content\n###END:CUSTOM_LAYER###",
        "expect_compliant": False,
        "expect_violation_rules": ["§3.6"],
    },
    {
        "name": "Unclosed layer is rejected",
        "input": COMPLIANT_EXAMPLE.replace("###END:TASK_PAYLOAD###", ""),
        "expect_compliant": False,
        "expect_violation_rules": ["§3.6 (parse error)"],
    },
    {
        "name": "ALLOW/DENY specificity overlap emits warning (Step 7)",
        # DENY ... WITHIN tests/ overlaps ALLOW ... WITHIN tests/unit/
        # mirrors the payments-platform pattern: DENY infra/ vs ALLOW infra/migrations/
        "input": COMPLIANT_EXAMPLE.replace(
            "DENY    modification of any file WITHIN tests/",
            "DENY    modification of any file WITHIN tests/\n"
            "ALLOW   new fixture creation WITHIN tests/unit/",
        ),
        "expect_compliant": True,   # overlap is a warning, not a violation
        "expect_violation_rules": [],
        "expect_warning_substring": "specificity overlap",
    },
]


# ---------------------------------------------------------------------------
# Output validation test suite
# ---------------------------------------------------------------------------

_OC_ICS = """
###ICS:IMMUTABLE_CONTEXT###
System: test
###END:IMMUTABLE_CONTEXT###

###ICS:CAPABILITY_DECLARATION###
ALLOW read access
###END:CAPABILITY_DECLARATION###

###ICS:SESSION_STATE###
CLEAR
###END:SESSION_STATE###

###ICS:TASK_PAYLOAD###
Do the thing.
###END:TASK_PAYLOAD###
""".strip()

_JSON_ICS = _OC_ICS + "\n\n" + """\
###ICS:OUTPUT_CONTRACT###
format:     JSON
schema:     { "result": "string", "count": "integer" }
variance:   none
on_failure: return plain text starting with BLOCKED:
###END:OUTPUT_CONTRACT###"""

_DIFF_ICS = _OC_ICS + "\n\n" + """\
###ICS:OUTPUT_CONTRACT###
format:     unified diff
schema:     standard unified diff against current HEAD
variance:   diff header timestamps may be omitted
on_failure: respond with a single line: BLOCKED: <reason>. No markdown. No bold asterisks.
###END:OUTPUT_CONTRACT###"""

_UNKNOWN_FORMAT_ICS = _OC_ICS + "\n\n" + """\
###ICS:OUTPUT_CONTRACT###
format:     mermaid diagram
schema:     flowchart LR syntax
variance:   none
on_failure: return BLOCKED: <reason>
###END:OUTPUT_CONTRACT###"""

_VALID_DIFF = """\
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,4 @@
 def foo():
-    pass
+    return 42
"""

OUTPUT_TESTS = [
    {
        "name": "Valid JSON output passes",
        "ics": _JSON_ICS,
        "output": '{"result": "ok", "count": 3}',
        "expect_compliant": True,
    },
    {
        "name": "Invalid JSON output fails",
        "ics": _JSON_ICS,
        "output": "this is not json",
        "expect_compliant": False,
        "expect_violation_rule": "OUTPUT_CONTRACT format",
    },
    {
        "name": "Valid unified diff passes",
        "ics": _DIFF_ICS,
        "output": _VALID_DIFF,
        "expect_compliant": True,
    },
    {
        "name": "Output missing diff markers fails",
        "ics": _DIFF_ICS,
        "output": "Here is my explanation without any diff.",
        "expect_compliant": False,
        "expect_violation_rule": "OUTPUT_CONTRACT format",
    },
    {
        "name": "BLOCKED single-line passes diff contract",
        "ics": _DIFF_ICS,
        "output": "BLOCKED: task requires modifying src/gateway/ which is denied",
        "expect_compliant": True,
    },
    {
        "name": "BLOCKED multi-line fails when on_failure requires single line",
        "ics": _DIFF_ICS,
        "output": "BLOCKED: reason\nExtra explanation here.",
        "expect_compliant": False,
        "expect_violation_rule": "OUTPUT_CONTRACT on_failure",
    },
    {
        "name": "BLOCKED with markdown fails when on_failure prohibits it",
        "ics": _DIFF_ICS,
        "output": "BLOCKED: **cannot modify** src/gateway/",
        "expect_compliant": False,
        "expect_violation_rule": "OUTPUT_CONTRACT on_failure",
    },
    {
        "name": "Unknown format emits warning not violation",
        "ics": _UNKNOWN_FORMAT_ICS,
        "output": "graph LR\n  A --> B",
        "expect_compliant": True,
        "expect_warning_substring": "no automatic validator",
    },
    {
        "name": "Malformed ICS returns parse error",
        "ics": "###ICS:IMMUTABLE_CONTEXT###\nno close tag",
        "output": "anything",
        "expect_compliant": False,
        "expect_violation_rule": "OUTPUT_CONTRACT parse error",
    },
    {
        "name": "JSON contract with BLOCKED: passes (routes to on_failure path)",
        "ics": _JSON_ICS,
        "output": "BLOCKED: cannot comply",
        "expect_compliant": True,
    },
]


def run_output_tests() -> int:
    passed = 0
    failed = 0

    print("Running ICS output validation test suite...\n")

    for test in OUTPUT_TESTS:
        result = validate_output(test["ics"], test["output"])
        ok = True

        if result.compliant != test["expect_compliant"]:
            ok = False

        if rule := test.get("expect_violation_rule"):
            if not any(rule in v.rule for v in result.violations):
                ok = False

        if warn_sub := test.get("expect_warning_substring"):
            if not any(warn_sub in w for w in result.warnings):
                ok = False

        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {test['name']}")

        if not ok:
            print(f"         Expected compliant={test['expect_compliant']}, "
                  f"got compliant={result.compliant}")
            if rule := test.get("expect_violation_rule"):
                found = [v.rule for v in result.violations]
                print(f"         Expected rule containing: '{rule}'")
                print(f"         Found rules: {found}")
            if result.violations:
                for v in result.violations:
                    print(f"         {v}")
            if result.warnings:
                for w in result.warnings:
                    print(f"         [WARN] {w}")
            failed += 1
        else:
            passed += 1

    print(f"\n{passed}/{passed + failed} tests passed.")
    return 0 if failed == 0 else 1


def run_tests() -> int:
    passed = 0
    failed = 0

    print("Running ICS validator test suite...\n")

    for test in TESTS:
        result = validate(test["input"])
        ok = True

        if result.compliant != test["expect_compliant"]:
            ok = False

        if test["expect_violation_rules"]:
            found_rules = {v.rule for v in result.violations}
            for expected_rule in test["expect_violation_rules"]:
                if expected_rule not in found_rules:
                    ok = False

        if warn_sub := test.get("expect_warning_substring"):
            if not any(warn_sub in w for w in result.warnings):
                ok = False

        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {test['name']}")

        if not ok:
            print(f"         Expected compliant={test['expect_compliant']}, "
                  f"got compliant={result.compliant}")
            if test["expect_violation_rules"]:
                found_rules = {v.rule for v in result.violations}
                print(f"         Expected rules: {test['expect_violation_rules']}")
                print(f"         Found rules:    {sorted(found_rules)}")
            if result.violations:
                for v in result.violations:
                    print(f"         {v}")
            failed += 1
        else:
            passed += 1

    print(f"\n{passed}/{passed + failed} tests passed.")
    return 0 if failed == 0 else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        sys.exit(2)

    if "--test" in args:
        rc1 = run_tests()
        print()
        rc2 = run_output_tests()
        sys.exit(0 if (rc1 == 0 and rc2 == 0) else 1)

    if "--stdin" in args:
        text = sys.stdin.read()
    else:
        path = args[0]
        json_output = "--json" in args
        try:
            with open(path) as f:
                text = f.read()
        except FileNotFoundError:
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(2)

    json_output = "--json" in args
    result = validate(text)

    if json_output:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(result.report())

    sys.exit(0 if result.compliant else 1)


if __name__ == "__main__":
    main()
