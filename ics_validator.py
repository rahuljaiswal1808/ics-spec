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

DIRECTIVE_PATTERN = re.compile(
    r"^\s*(ALLOW|DENY|REQUIRE)\s+.+", re.IGNORECASE
)

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
]


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
        sys.exit(run_tests())

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
