/**
 * ICS Prompt Library — TypeScript
 *
 * Thin wrappers for constructing ICS-layered prompts directly from code.
 * Each piece of your system prompt is labelled at the variable level, so
 * the compiled string the LLM receives is clean and undecorated.
 *
 * @example
 * ```ts
 * import * as ics from "./ics_prompt"
 *
 * // Static blocks — tag once at definition time
 * const PERSONA = ics.immutable`You are a senior financial analyst assistant.`
 * const RULES   = ics.capability`
 *   ALLOW  read-only market-data queries
 *   DENY   trading actions or account mutations
 * `
 * const FORMAT = ics.output_contract`
 *   format:   structured markdown
 *   schema:   { "analysis": "string", "risks": ["string"] }
 *   variance: "risks" MAY be omitted for informational queries
 *   on_failure: plain-text apology with brief reason
 * `
 *
 * // Per-call blocks — inline interpolation, still tagged
 * function sessionCtx(userName: string, portfolio: string) {
 *   return ics.session`User: ${userName}.  Portfolio focus: ${portfolio}.`
 * }
 *
 * function task(userMessage: string) {
 *   return ics.dynamic`The user asked: ${userMessage}`
 * }
 *
 * // Compile to a single ICS-delimited string
 * const prompt = ics.compile(
 *   PERSONA,
 *   RULES,
 *   sessionCtx(name, portfolio),
 *   task(msg),
 *   FORMAT,
 * )
 * ```
 *
 * ## API
 *
 * ### Layer taggers
 * Each tagger works as both a tagged template literal and a plain function:
 * ```ts
 * ics.immutable`static text`           // tagged template — interpolation allowed
 * ics.immutable("static text")         // plain string
 * ```
 *
 * ### compile(...blocks, options?)
 * Renders blocks to ICS-delimited text.  Logs warnings for layer-order
 * violations and template variables in cache-eligible blocks.
 *
 * ### validate(...blocks)
 * Returns the same warnings without rendering.
 *
 * ### parse(prompt)
 * Parses an ICS-delimited string back into ICSBlocks (useful for testing).
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ICSLayer =
  | "IMMUTABLE_CONTEXT"
  | "CAPABILITY_DECLARATION"
  | "SESSION_STATE"
  | "TASK_PAYLOAD"
  | "OUTPUT_CONTRACT"

export interface ICSBlock {
  readonly layer: ICSLayer
  readonly content: string
  /** Returns the raw content, so ICSBlocks can be used where strings are expected. */
  toString(): string
  /** True for layers the spec considers safe to cache across calls. */
  readonly cacheEligible: boolean
}

export interface CompileOptions {
  /** Set to false to silence validation warnings. Default: true. */
  warn?: boolean
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const LAYER_ORDER: ICSLayer[] = [
  "IMMUTABLE_CONTEXT",
  "CAPABILITY_DECLARATION",
  "SESSION_STATE",
  "TASK_PAYLOAD",
  "OUTPUT_CONTRACT",
]

const CACHE_ELIGIBLE = new Set<ICSLayer>([
  "IMMUTABLE_CONTEXT",
  "CAPABILITY_DECLARATION",
  "OUTPUT_CONTRACT",
])

// Template variable patterns
const TEMPLATE_VAR_RE =
  /\{\{[^}]+\}\}|(?<![/{])\{[A-Za-z_]\w*\}(?![/}])|\$\{[^}]+\}|<[A-Z][A-Z_]{2,}>/

// ---------------------------------------------------------------------------
// Dedent helper — strips common leading whitespace from multiline strings
// (mirrors Python's textwrap.dedent, handles indented template literals)
// ---------------------------------------------------------------------------

function dedent(text: string): string {
  const lines = text.split("\n")
  // Drop leading and trailing blank lines introduced by template literal syntax
  while (lines.length && lines[0].trim() === "") lines.shift()
  while (lines.length && lines[lines.length - 1].trim() === "") lines.pop()
  const minIndent = lines
    .filter(l => l.trim().length > 0)
    .reduce((min, l) => Math.min(min, (l.match(/^(\s*)/)?.[1].length ?? 0)), Infinity)
  if (!isFinite(minIndent) || minIndent === 0) return lines.join("\n")
  return lines.map(l => l.slice(minIndent)).join("\n")
}

// ---------------------------------------------------------------------------
// ICSBlock factory
// ---------------------------------------------------------------------------

function makeBlock(layer: ICSLayer, content: string): ICSBlock {
  return Object.freeze({
    layer,
    content: dedent(content),
    toString() { return this.content },
    get cacheEligible() { return CACHE_ELIGIBLE.has(layer) },
  })
}

// ---------------------------------------------------------------------------
// Tagger: works as both a tagged template and a plain function
// ---------------------------------------------------------------------------

type Tagger = {
  /** Tagged template: ics.immutable`You are ${role}.` */
  (strings: TemplateStringsArray, ...values: unknown[]): ICSBlock
  /** Plain function: ics.immutable("You are a financial analyst.") */
  (text: string): ICSBlock
}

function makeTagger(layer: ICSLayer): Tagger {
  return function(
    strings: TemplateStringsArray | string,
    ...values: unknown[]
  ): ICSBlock {
    if (typeof strings === "string") {
      return makeBlock(layer, strings)
    }
    // Tagged template literal: interleave static parts and interpolated values
    const content = strings.reduce<string>(
      (acc, part, i) => acc + part + (i < values.length ? String(values[i]) : ""),
      "",
    )
    return makeBlock(layer, content)
  } as Tagger
}

// ---------------------------------------------------------------------------
// Public layer taggers
// ---------------------------------------------------------------------------

/** Tag content as IMMUTABLE_CONTEXT (cache-eligible, never changes). */
export const immutable = makeTagger("IMMUTABLE_CONTEXT")

/** Tag content as CAPABILITY_DECLARATION (cache-eligible, rarely changes). */
export const capability = makeTagger("CAPABILITY_DECLARATION")

/** Tag content as SESSION_STATE (per-session, not cached across sessions). */
export const session = makeTagger("SESSION_STATE")

/** Tag content as TASK_PAYLOAD (per-call, never cached). */
export const dynamic = makeTagger("TASK_PAYLOAD")

/** Tag content as OUTPUT_CONTRACT (cache-eligible, format/schema directives). */
export const output_contract = makeTagger("OUTPUT_CONTRACT")

// ---------------------------------------------------------------------------
// Validate
// ---------------------------------------------------------------------------

/**
 * Check blocks for common mistakes.  Returns warning strings; does not throw.
 *
 * Checks:
 *   1. Layer order — blocks should follow canonical ICS ordering.
 *   2. Template variables inside cache-eligible blocks.
 */
export function validate(...blocks: ICSBlock[]): string[] {
  const issues: string[] = []

  // Canonical subset order for layers actually present
  const presentLayers = LAYER_ORDER.filter(l => blocks.some(b => b.layer === l))
  const actualOrder: ICSLayer[] = []
  const seen = new Set<ICSLayer>()
  for (const b of blocks) {
    if (!seen.has(b.layer)) {
      actualOrder.push(b.layer)
      seen.add(b.layer)
    }
  }

  if (JSON.stringify(actualOrder) !== JSON.stringify(presentLayers)) {
    issues.push(
      `Layer order deviates from the canonical ICS ordering ` +
      `(${presentLayers.join(" → ")}). ` +
      `Some prompt-caching implementations depend on stable prefix order.`
    )
  }

  // Template variables in cache-eligible blocks
  for (const b of blocks) {
    if (b.cacheEligible && TEMPLATE_VAR_RE.test(b.content)) {
      issues.push(
        `${b.layer} is cache-eligible but its content contains template variables ` +
        `— the rendered string will differ per call and should not be cached. ` +
        `Move dynamic interpolation into a session() or dynamic() block.`
      )
    }
  }

  return issues
}

// ---------------------------------------------------------------------------
// Compile
// ---------------------------------------------------------------------------

/**
 * Render ICSBlocks into a single ICS-delimited prompt string.
 *
 * @param blocks  ICSBlock instances in the order they should appear.
 * @param options CompileOptions — set warn: false to silence validation.
 * @returns       A string with ###ICS:LAYER### … ###END:LAYER### delimiters.
 * @throws        TypeError if a plain string is passed instead of an ICSBlock.
 */
export function compile(
  ...args: [...ICSBlock[], CompileOptions] | ICSBlock[]
): string {
  // Detect optional trailing options object
  let blocks: ICSBlock[]
  let options: CompileOptions = {}

  const last = args[args.length - 1]
  if (
    args.length > 0 &&
    typeof last === "object" &&
    last !== null &&
    !("layer" in last)
  ) {
    options = last as CompileOptions
    blocks = args.slice(0, -1) as ICSBlock[]
  } else {
    blocks = args as ICSBlock[]
  }

  const warnEnabled = options.warn !== false

  for (let i = 0; i < blocks.length; i++) {
    const b = blocks[i]
    if (typeof b !== "object" || b === null || !("layer" in b)) {
      throw new TypeError(
        `Argument ${i} is not an ICSBlock. ` +
        `Wrap it with ics.immutable(), ics.dynamic(), etc.`
      )
    }
  }

  if (warnEnabled) {
    for (const issue of validate(...blocks)) {
      console.warn(`[ics] ${issue}`)
    }
  }

  return blocks
    .map(b => `###ICS:${b.layer}###\n${b.content}\n###END:${b.layer}###`)
    .join("\n\n")
}

// ---------------------------------------------------------------------------
// Parse (round-trip / testing helper)
// ---------------------------------------------------------------------------

const PARSE_RE = /###ICS:([A-Z_]+)###\n([\s\S]*?)###END:\1###/g

const LAYER_NAMES = new Set<string>(LAYER_ORDER)

/**
 * Parse an ICS-delimited prompt string back into a list of ICSBlocks.
 *
 * Useful for testing round-trips and inspecting already-compiled prompts.
 * Unknown layer names are skipped.
 */
export function parse(prompt: string): ICSBlock[] {
  const blocks: ICSBlock[] = []
  for (const [, layerName, content] of prompt.matchAll(PARSE_RE)) {
    if (LAYER_NAMES.has(layerName)) {
      blocks.push(makeBlock(layerName as ICSLayer, content))
    }
  }
  return blocks
}
