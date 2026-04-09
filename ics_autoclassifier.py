#!/usr/bin/env python3
"""
ICS Auto-Classifier

Classifies an arbitrary system prompt into ICS layers:
    IMMUTABLE_CONTEXT, CAPABILITY_DECLARATION, SESSION_STATE,
    TASK_PAYLOAD, OUTPUT_CONTRACT

Two modes:
  1. Annotation-driven  — developer wraps sections in <ics:layer> tags.
     Annotations are stripped before the prompt reaches the LLM.
  2. Heuristic          — signal-based inference on unannotated text.

Conservative by default: ambiguous sections are left UNCLASSIFIED and
excluded from caching recommendations. A missed saving is always
preferable to serving stale cached content.

Annotation syntax (block-level):
    <ics:immutable>   ... </ics:immutable>    → IMMUTABLE_CONTEXT
    <ics:capability>  ... </ics:capability>   → CAPABILITY_DECLARATION
    <ics:session>     ... </ics:session>      → SESSION_STATE
    <ics:dynamic>     ... </ics:dynamic>      → TASK_PAYLOAD
    <ics:output-contract> ... </ics:output-contract> → OUTPUT_CONTRACT

Aliases accepted:
    immutable   = stable = permanent
    capability  = capabilities = constraints
    session     = semi-static
    dynamic     = task = per-call
    output-contract = output_contract = format-contract

Usage:
    python ics_autoclassifier.py <prompt_file>
    python ics_autoclassifier.py --stdin
    python ics_autoclassifier.py --report <prompt_file>    # JSON classification report
    python ics_autoclassifier.py --to-ics <prompt_file>    # render as ICS-delimited output
"""

import re
import sys
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Layer definitions
# ---------------------------------------------------------------------------

class ICSLayer(Enum):
    IMMUTABLE_CONTEXT      = "IMMUTABLE_CONTEXT"
    CAPABILITY_DECLARATION = "CAPABILITY_DECLARATION"
    SESSION_STATE          = "SESSION_STATE"
    TASK_PAYLOAD           = "TASK_PAYLOAD"
    OUTPUT_CONTRACT        = "OUTPUT_CONTRACT"
    UNCLASSIFIED           = "UNCLASSIFIED"   # conservative: do not cache


# Cache eligibility per layer
CACHE_ELIGIBLE: dict[ICSLayer, bool] = {
    ICSLayer.IMMUTABLE_CONTEXT:      True,
    ICSLayer.CAPABILITY_DECLARATION: True,
    ICSLayer.SESSION_STATE:          False,   # session-scoped; caller manages
    ICSLayer.TASK_PAYLOAD:           False,
    ICSLayer.OUTPUT_CONTRACT:        True,
    ICSLayer.UNCLASSIFIED:           False,
}

# Canonical ICS layer order (Section 4.1)
LAYER_ORDER = [
    ICSLayer.IMMUTABLE_CONTEXT,
    ICSLayer.CAPABILITY_DECLARATION,
    ICSLayer.SESSION_STATE,
    ICSLayer.TASK_PAYLOAD,
    ICSLayer.OUTPUT_CONTRACT,
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ClassifiedBlock:
    content: str
    layer: ICSLayer
    confidence: float          # 0.0–1.0; 1.0 for annotation-driven blocks
    source: str                # "annotation" | "heuristic" | "conservative"
    warnings: list[str] = field(default_factory=list)

    @property
    def cache_eligible(self) -> bool:
        return CACHE_ELIGIBLE[self.layer]


@dataclass
class ClassificationResult:
    blocks: list[ClassifiedBlock]
    warnings: list[str] = field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        return any("conflict" in w.lower() for w in self.warnings)

    @property
    def cache_eligible_blocks(self) -> list[ClassifiedBlock]:
        return [b for b in self.blocks if b.cache_eligible]

    @property
    def unclassified_blocks(self) -> list[ClassifiedBlock]:
        return [b for b in self.blocks if b.layer == ICSLayer.UNCLASSIFIED]


# ---------------------------------------------------------------------------
# Annotation tag registry
# ---------------------------------------------------------------------------

_ANNOTATION_ALIASES: dict[str, ICSLayer] = {
    # IMMUTABLE_CONTEXT
    "immutable":          ICSLayer.IMMUTABLE_CONTEXT,
    "stable":             ICSLayer.IMMUTABLE_CONTEXT,
    "permanent":          ICSLayer.IMMUTABLE_CONTEXT,
    # CAPABILITY_DECLARATION
    "capability":         ICSLayer.CAPABILITY_DECLARATION,
    "capabilities":       ICSLayer.CAPABILITY_DECLARATION,
    "constraints":        ICSLayer.CAPABILITY_DECLARATION,
    # SESSION_STATE
    "session":            ICSLayer.SESSION_STATE,
    "semi-static":        ICSLayer.SESSION_STATE,
    # TASK_PAYLOAD
    "dynamic":            ICSLayer.TASK_PAYLOAD,
    "task":               ICSLayer.TASK_PAYLOAD,
    "per-call":           ICSLayer.TASK_PAYLOAD,
    # OUTPUT_CONTRACT
    "output-contract":    ICSLayer.OUTPUT_CONTRACT,
    "output_contract":    ICSLayer.OUTPUT_CONTRACT,
    "format-contract":    ICSLayer.OUTPUT_CONTRACT,
}

# Build a single regex that matches any <ics:alias> ... </ics:alias> block
_ALIAS_PATTERN = "|".join(re.escape(k) for k in _ANNOTATION_ALIASES)
_ANNOTATION_RE = re.compile(
    r"<ics:(" + _ALIAS_PATTERN + r")>(.*?)</ics:(?:" + _ALIAS_PATTERN + r")>",
    re.DOTALL | re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Template variable patterns — presence → TASK_PAYLOAD signal
# ---------------------------------------------------------------------------

_TEMPLATE_VAR_PATTERNS = [
    re.compile(r"\{\{[^}]+\}\}"),           # {{variable}}  — Jinja2/Handlebars
    re.compile(r"(?<![/{])\{[A-Za-z_]\w*\}(?![/}])"),  # {variable} — excludes URL path params like /v2/{id}
    re.compile(r"\$\{[^}]+\}"),             # ${variable}   — shell/JS template literal
    re.compile(r"<[A-Z][A-Z_]{2,}>"),       # <PLACEHOLDER> — all-caps, min 3 chars to avoid acronyms
]


def _has_template_vars(text: str) -> bool:
    return any(p.search(text) for p in _TEMPLATE_VAR_PATTERNS)


# ---------------------------------------------------------------------------
# Heuristic signal tables
# ---------------------------------------------------------------------------

# Each entry is (compiled_regex, weight).  Higher weight = stronger signal.
_SIGNALS: dict[ICSLayer, list[tuple[re.Pattern, float]]] = {

    ICSLayer.IMMUTABLE_CONTEXT: [
        (re.compile(r"\bYou are\b",                    re.I), 1.5),
        (re.compile(r"\bYour role (is|as)\b",          re.I), 1.5),
        (re.compile(r"\bAs an? (AI|assistant|agent)\b",re.I), 1.5),
        (re.compile(r"\bYou must (always|never)\b",    re.I), 1.2),
        (re.compile(r"\b(architectural|invariant)\b",  re.I), 1.2),
        (re.compile(r"\bRepository (layout|structure)\b", re.I), 1.2),
        (re.compile(r"\bCore data model\b",            re.I), 1.2),
        (re.compile(r"\bExternal dependencies\b",      re.I), 1.0),
        (re.compile(r"\bSystem:\s",                    re.I), 1.0),
        (re.compile(r"\bOwner:\s",                     re.I), 0.8),
        (re.compile(r"\bAPI surface\b",                re.I), 0.8),
        (re.compile(r"\bCompliance context\b",         re.I), 0.8),
        (re.compile(r"\bObservability\b",              re.I), 0.6),
    ],

    ICSLayer.CAPABILITY_DECLARATION: [
        (re.compile(r"^\s*(ALLOW|DENY|REQUIRE)\b",     re.I | re.M), 2.0),
        (re.compile(r"\bPermitted actions?\b",         re.I), 1.5),
        (re.compile(r"\bProhibited\b",                 re.I), 1.5),
        (re.compile(r"\bMandatory (requirements?|actions?)\b", re.I), 1.5),
        (re.compile(r"\b(WITHIN|UNLESS|IF)\b",         re.I), 0.8),
        (re.compile(r"\b(capability|permission|constraint)\b", re.I), 0.8),
    ],

    ICSLayer.SESSION_STATE: [
        (re.compile(r"\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", re.I), 2.0),  # ISO timestamps
        (re.compile(r"\b(Decision|Confirmed|Task):\s",  re.I), 1.5),
        (re.compile(r"\b(user preferences?|user settings?)\b", re.I), 1.2),
        (re.compile(r"\bThis (conversation|session)\b", re.I), 1.2),
        (re.compile(r"\bSo far\b",                     re.I), 1.0),
        (re.compile(r"\bCLEAR\b",                      re.I), 1.5),
    ],

    ICSLayer.TASK_PAYLOAD: [
        (re.compile(r"\b(The user has asked|User request|User message)\b", re.I), 2.0),
        (re.compile(r"\bPlease\b.{0,80}\b(add|implement|fix|create|update)\b", re.I | re.S), 1.5),
        (re.compile(r"^\s*(Add|Implement|Fix|Create|Refactor|Write|Update)\b", re.I | re.M), 1.2),
        (re.compile(r"\bThis (request|query|task)\b",  re.I), 1.0),
        (re.compile(r"\bthe following (task|request|instruction)\b", re.I), 1.0),
    ],

    ICSLayer.OUTPUT_CONTRACT: [
        (re.compile(r"^format\s*:",                    re.I | re.M), 2.0),
        (re.compile(r"^schema\s*:",                    re.I | re.M), 2.0),
        (re.compile(r"^variance\s*:",                  re.I | re.M), 2.0),
        (re.compile(r"^on_failure\s*:",                re.I | re.M), 2.0),
        (re.compile(r"\bRespond in\b",                 re.I), 1.5),
        (re.compile(r"\bFormat your (response|output|answer)\b", re.I), 1.5),
        (re.compile(r"\bAlways (use|return|output|respond with)\b", re.I), 1.0),
        (re.compile(r"\b(unified diff|JSON|XML|markdown|plain text)\b.*\bformat\b", re.I | re.S), 0.8),
    ],
}

# Confidence threshold below which a heuristic classification is considered
# too uncertain to cache.  Below this → UNCLASSIFIED (conservative fallback).
_CONFIDENCE_THRESHOLD = 0.50


def _score_segment(text: str) -> tuple[ICSLayer, float, list[str]]:
    """
    Score a text segment against all layer signal tables.

    Returns (best_layer, confidence, warnings).
    If template variables are present and the best layer is cache-eligible,
    a warning is added and the layer is forced to TASK_PAYLOAD.
    """
    warnings: list[str] = []
    scores: dict[ICSLayer, float] = {}

    for layer, signals in _SIGNALS.items():
        total = sum(
            weight * len(pattern.findall(text))
            for pattern, weight in signals
        )
        scores[layer] = total

    total_score = sum(scores.values())
    if total_score == 0:
        return ICSLayer.UNCLASSIFIED, 0.0, warnings

    best_layer = max(scores, key=lambda l: scores[l])
    confidence = scores[best_layer] / total_score

    # Template variable override: dynamic content must not be cached
    if _has_template_vars(text) and CACHE_ELIGIBLE.get(best_layer, False):
        warnings.append(
            f"Heuristic suggested {best_layer.value} but template variables "
            f"detected — reclassified as TASK_PAYLOAD. "
            f"If this content is intentionally static, use an <ics:immutable> "
            f"annotation to confirm."
        )
        return ICSLayer.TASK_PAYLOAD, 1.0, warnings

    return best_layer, confidence, warnings


# ---------------------------------------------------------------------------
# Segment splitter
# ---------------------------------------------------------------------------

# Split on: two or more blank lines, or a markdown header (## / ###)
_SEGMENT_SPLIT_RE = re.compile(r"\n{2,}|\n(?=#{1,3} )")


def _split_segments(text: str) -> list[str]:
    """Split text into classifiable segments, dropping empty ones."""
    raw = _SEGMENT_SPLIT_RE.split(text)
    return [s.strip() for s in raw if s.strip()]


# ---------------------------------------------------------------------------
# ICS delimiter fast-path
# ---------------------------------------------------------------------------

_ICS_BLOCK_RE = re.compile(
    r"###ICS:([A-Z_]+)###\n(.*?)###END:\1###",
    re.DOTALL,
)

_VALID_LAYER_NAMES = {l.value for l in ICSLayer if l != ICSLayer.UNCLASSIFIED}


def _parse_ics_delimiters(prompt: str) -> Optional[ClassificationResult]:
    """
    If the prompt already uses ###ICS:LAYER### delimiters, parse them directly
    and skip heuristics.  Returns None if no delimiters are found.
    """
    matches = _ICS_BLOCK_RE.findall(prompt)
    if not matches:
        return None

    blocks: list[ClassifiedBlock] = []
    warnings: list[str] = []

    for layer_name, content in matches:
        if layer_name not in _VALID_LAYER_NAMES:
            warnings.append(f"Unknown layer name in delimiter: {layer_name!r} — skipped.")
            continue
        layer = ICSLayer(layer_name)
        block_warnings: list[str] = []
        if _has_template_vars(content) and CACHE_ELIGIBLE[layer]:
            block_warnings.append(
                f"{layer.value} block contains template variables — "
                f"this content will change per call and should not be cached."
            )
        blocks.append(ClassifiedBlock(
            content=content.strip(),
            layer=layer,
            confidence=1.0,
            source="delimiter",
            warnings=block_warnings,
        ))

    return ClassificationResult(blocks=blocks, warnings=warnings)


# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------

class ICSAutoClassifier:
    """
    Classifies an arbitrary system prompt into ICS layers.

    Precedence (highest to lowest):
      1. Existing ###ICS:LAYER### delimiters (fast-path, no heuristics run)
      2. Developer <ics:annotation> tags
      3. Heuristic scoring
      4. Conservative fallback → UNCLASSIFIED
    """

    def classify(self, prompt: str) -> ClassificationResult:
        """
        Main entry point.  Returns a ClassificationResult with all blocks
        classified and any warnings surfaced.
        """
        # Fast-path: already ICS-formatted
        delimiter_result = _parse_ics_delimiters(prompt)
        if delimiter_result is not None:
            return delimiter_result

        # Phase 1: extract annotation blocks
        annotated_blocks, remaining_text = self._parse_annotations(prompt)

        # Phase 2: heuristic classification of remaining text
        heuristic_blocks = self._classify_remaining(remaining_text)

        # Phase 3: conflict detection
        all_blocks = annotated_blocks + heuristic_blocks
        global_warnings = self._check_conflicts(annotated_blocks, heuristic_blocks)

        return ClassificationResult(blocks=all_blocks, warnings=global_warnings)

    # ------------------------------------------------------------------
    # Phase 1 — annotation parsing
    # ------------------------------------------------------------------

    def _parse_annotations(self, prompt: str) -> tuple[list[ClassifiedBlock], str]:
        """
        Extract <ics:tag>...</ics:tag> blocks.
        Returns (annotated_blocks, prompt_with_annotations_removed).
        """
        annotated: list[ClassifiedBlock] = []
        positions: list[tuple[int, int]] = []   # (start, end) of each match

        for m in _ANNOTATION_RE.finditer(prompt):
            alias = m.group(1).lower()
            content = m.group(2).strip()
            layer = _ANNOTATION_ALIASES[alias]
            warnings: list[str] = []

            if _has_template_vars(content) and CACHE_ELIGIBLE[layer]:
                warnings.append(
                    f"<ics:{alias}> annotation marks content as cache-eligible "
                    f"but template variables were detected inside. "
                    f"Verify this content is truly static."
                )

            annotated.append(ClassifiedBlock(
                content=content,
                layer=layer,
                confidence=1.0,
                source="annotation",
                warnings=warnings,
            ))
            positions.append((m.start(), m.end()))

        # Remove annotation tags (and their contents) from remaining text
        remaining = prompt
        for start, end in reversed(positions):
            remaining = remaining[:start] + remaining[end:]

        return annotated, remaining.strip()

    # ------------------------------------------------------------------
    # Phase 2 — heuristic classification
    # ------------------------------------------------------------------

    def _classify_remaining(self, text: str) -> list[ClassifiedBlock]:
        if not text:
            return []

        segments = _split_segments(text)
        blocks: list[ClassifiedBlock] = []

        for segment in segments:
            layer, confidence, warnings = _score_segment(segment)

            if confidence < _CONFIDENCE_THRESHOLD:
                layer = ICSLayer.UNCLASSIFIED
                source = "conservative"
                if confidence > 0:
                    warnings.append(
                        f"Confidence {confidence:.0%} below threshold "
                        f"({_CONFIDENCE_THRESHOLD:.0%}) — marked UNCLASSIFIED. "
                        f"Add an <ics:...> annotation to assign a layer explicitly."
                    )
            else:
                source = "heuristic"

            blocks.append(ClassifiedBlock(
                content=segment,
                layer=layer,
                confidence=confidence,
                source=source,
                warnings=warnings,
            ))

        return blocks

    # ------------------------------------------------------------------
    # Phase 3 — conflict detection
    # ------------------------------------------------------------------

    def _check_conflicts(
        self,
        annotated: list[ClassifiedBlock],
        heuristic: list[ClassifiedBlock],
    ) -> list[str]:
        """
        Warn when a heuristic classification would strongly disagree with
        an annotation for overlapping content.  (Overlap is approximated by
        checking if heuristic blocks are near annotation boundaries.)
        """
        warnings: list[str] = []

        # Surface any per-block warnings at the result level too
        for block in annotated + heuristic:
            warnings.extend(block.warnings)

        # Warn about high-confidence heuristic UNCLASSIFIED blocks that
        # neighbour cache-eligible annotation blocks — possible boundary error
        cache_annotated = any(CACHE_ELIGIBLE[b.layer] for b in annotated)
        unclassified_heuristic = [b for b in heuristic if b.layer == ICSLayer.UNCLASSIFIED]

        if cache_annotated and unclassified_heuristic:
            warnings.append(
                f"{len(unclassified_heuristic)} segment(s) could not be classified "
                f"heuristically and neighbour annotated blocks. "
                f"Consider extending your <ics:...> annotations to cover them."
            )

        return warnings


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def to_ics(result: ClassificationResult) -> str:
    """
    Render classified blocks as ICS-delimited output (Section 3.6 format).
    UNCLASSIFIED blocks are emitted as plain text with a comment.
    Annotation tags are already stripped; this output is clean for LLM use.
    """
    parts: list[str] = []
    for block in result.blocks:
        if block.layer == ICSLayer.UNCLASSIFIED:
            parts.append(f"# [UNCLASSIFIED — review required]\n{block.content}")
        else:
            layer = block.layer.value
            parts.append(
                f"###ICS:{layer}###\n{block.content}\n###END:{layer}###"
            )
    return "\n\n".join(parts)


def to_report(result: ClassificationResult) -> dict:
    """Return a JSON-serialisable classification report."""
    return {
        "summary": {
            "total_blocks": len(result.blocks),
            "cache_eligible": len(result.cache_eligible_blocks),
            "unclassified": len(result.unclassified_blocks),
            "has_conflicts": result.has_conflicts,
        },
        "blocks": [
            {
                "layer": b.layer.value,
                "source": b.source,
                "confidence": round(b.confidence, 3),
                "cache_eligible": b.cache_eligible,
                "content_preview": b.content[:120].replace("\n", " "),
                "warnings": b.warnings,
            }
            for b in result.blocks
        ],
        "warnings": result.warnings,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _usage() -> None:
    print(__doc__)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        _usage()
        return 2

    mode = "ics"       # default: render ICS output
    file_arg = None

    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--stdin":
            file_arg = None
            break
        elif arg == "--report":
            mode = "report"
            i += 1
            if i >= len(argv):
                print("--report requires a file argument", file=sys.stderr)
                return 2
            file_arg = argv[i]
        elif arg == "--to-ics":
            mode = "ics"
            i += 1
            if i >= len(argv):
                print("--to-ics requires a file argument", file=sys.stderr)
                return 2
            file_arg = argv[i]
        else:
            file_arg = arg
        i += 1

    if file_arg:
        try:
            with open(file_arg, "r", encoding="utf-8") as fh:
                prompt = fh.read()
        except OSError as e:
            print(f"Error reading {file_arg!r}: {e}", file=sys.stderr)
            return 2
    else:
        prompt = sys.stdin.read()

    classifier = ICSAutoClassifier()
    result = classifier.classify(prompt)

    if mode == "report":
        print(json.dumps(to_report(result), indent=2))
    else:
        print(to_ics(result))
        if result.warnings:
            print("\n# Classifier warnings:", file=sys.stderr)
            for w in result.warnings:
                print(f"#   {w}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
