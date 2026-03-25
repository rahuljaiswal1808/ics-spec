"""Session — manages a single conversation lifecycle and executes LLM calls."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ics_runtime.core.result import RunResult, ToolCallRecord
from ics_runtime.observability.recorder import MetricsRecorder
from ics_runtime.providers.base import ProviderMessage
from ics_runtime.session_backends.base import SessionData

if TYPE_CHECKING:
    from ics_runtime.core.agent import Agent
    from ics_runtime.observability.metrics import SessionMetrics

# Pricing per 1M tokens (USD) — input / output / cache_write / cache_read
_PRICING: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-opus-4-6":             {"in": 15.0,  "out": 75.0,  "cw": 18.75, "cr": 1.50},
    "claude-sonnet-4-6":           {"in": 3.0,   "out": 15.0,  "cw": 3.75,  "cr": 0.30},
    "claude-3-5-sonnet-20241022":  {"in": 3.0,   "out": 15.0,  "cw": 3.75,  "cr": 0.30},
    "claude-3-5-haiku-20241022":   {"in": 0.80,  "out": 4.0,   "cw": 1.0,   "cr": 0.08},
    # OpenAI
    "gpt-4o":                      {"in": 2.50,  "out": 10.0,  "cw": 0.0,   "cr": 1.25},
    "gpt-4o-mini":                 {"in": 0.15,  "out": 0.60,  "cw": 0.0,   "cr": 0.075},
    "o1":                          {"in": 15.0,  "out": 60.0,  "cw": 0.0,   "cr": 7.50},
}
_FALLBACK_PRICE = {"in": 3.0, "out": 15.0, "cw": 3.75, "cr": 0.30}


def _estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int,
    cache_read_tokens: int,
) -> float:
    p = _PRICING.get(model, _FALLBACK_PRICE)
    return (
        input_tokens       * p["in"]  / 1_000_000
        + output_tokens    * p["out"] / 1_000_000
        + cache_write_tokens * p["cw"] / 1_000_000
        + cache_read_tokens  * p["cr"] / 1_000_000
    )


class Session:
    """A single conversation session.

    Do not instantiate directly — use ``Agent.session()``.
    """

    def __init__(
        self,
        agent: "Agent",
        session_id: str,
        session_vars: dict[str, Any],
    ) -> None:
        self._agent = agent
        self.session_id = session_id
        self._session_vars = session_vars
        self._pending_clear = False
        self._recorder = MetricsRecorder(session_id, agent._model, agent._provider_name)

        backend = agent._backend
        if not backend.exists(session_id):
            data = SessionData(
                session_id=session_id,
                context=dict(session_vars),
            )
            backend.save(session_id, data)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, task: str, *, max_tool_rounds: int = 10) -> RunResult:
        """Execute one turn and return a RunResult."""
        from ics_runtime.exceptions import MaxToolRoundsError

        t_start = time.monotonic()
        agent = self._agent
        data = agent._backend.load(self.session_id)
        assert data is not None

        # Build SESSION_STATE text
        session_state = self._build_session_state(data)

        # Build output contract text (may be empty)
        oc_text = ""
        if agent._output_contract:
            oc_text = agent._output_contract.to_ics_text()

        # Build system prompt blocks
        system_blocks = agent._prompt_builder.build_system(
            immutable=agent._immutable,
            capability=agent._capability,
            session_state=session_state,
            output_contract=oc_text,
        )

        # Build initial conversation messages (task is the first user message)
        messages: list[ProviderMessage] = [
            ProviderMessage(role="user", content=task)
        ]

        # Get provider-formatted tools
        tools = agent._registry.to_provider_tools(agent._provider_name) if agent._registry else None

        # Tool execution loop
        all_tool_calls: list[ToolCallRecord] = []
        prov_response = None

        for _round in range(max_tool_rounds + 1):
            prov_response = agent._provider.complete(
                system_blocks=system_blocks,
                messages=messages,
                tools=tools,
                max_tokens=4096,
            )

            if not prov_response.tool_calls:
                break  # Final text response

            if _round >= max_tool_rounds:
                raise MaxToolRoundsError(max_tool_rounds)

            # Execute each tool call
            assistant_tool_content: list[dict] = []
            for tc in prov_response.tool_calls:
                t_tool_start = time.monotonic()
                record = self._execute_tool(tc, data)
                all_tool_calls.append(record)
                t_tool_ms = int((time.monotonic() - t_tool_start) * 1000)

                if agent._provider_name == "anthropic":
                    assistant_tool_content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc["input"],
                    })
                # Append the assistant "I want to call a tool" message
            if agent._provider_name == "anthropic":
                messages.append(ProviderMessage(
                    role="assistant",
                    content=assistant_tool_content,
                ))
            elif agent._provider_name == "openai":
                # OpenAI: one assistant message with tool_calls list
                # (handled internally by provider; we re-send messages list)
                pass  # OpenAI provider tracks internally via raw response

            # Append tool results
            for tc, record in zip(prov_response.tool_calls, all_tool_calls[-len(prov_response.tool_calls):]):
                result_msg = agent._provider.tool_result_message(
                    tc["id"],
                    record.output if not record.blocked else f"BLOCKED: tool '{record.tool_name}' denied",
                )
                messages.append(result_msg)

        assert prov_response is not None
        response_text = prov_response.text

        # Post-execution enforcement
        violations = []
        validated = True
        parsed = None

        if agent._capability_enforcer:
            violations += agent._capability_enforcer.scan_output(response_text)

        if agent._output_contract:
            outcome = agent._output_contract.validate(response_text)
            validated = outcome.passed
            parsed = outcome.parsed
            violations += outcome.violations

        # Cost estimate
        cost = _estimate_cost(
            agent._model,
            prov_response.input_tokens,
            prov_response.output_tokens,
            prov_response.cache_creation_tokens,
            prov_response.cache_read_tokens,
        )

        latency_ms = int((time.monotonic() - t_start) * 1000)
        data.turn_count += 1
        turn_number = data.turn_count

        # Persist updated session state
        entry = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] Turn {turn_number}: {task[:80]}"
        if self._pending_clear:
            data.entries = []
            data.cleared = True
            self._pending_clear = False
        data.entries.append(entry)
        data.cleared = False
        agent._backend.save(self.session_id, data)

        result = RunResult(
            text=response_text,
            validated=validated,
            violations=violations,
            parsed=parsed,
            cache_hit=prov_response.cache_hit,
            cache_write=prov_response.cache_creation_tokens > 0,
            tokens_saved=prov_response.cache_read_tokens,
            input_tokens=prov_response.input_tokens,
            output_tokens=prov_response.output_tokens,
            cache_write_tokens=prov_response.cache_creation_tokens,
            tool_calls=all_tool_calls,
            session_id=self.session_id,
            turn_number=turn_number,
            latency_ms=latency_ms,
            model=agent._model,
            provider=agent._provider_name,
            cost_usd=cost,
        )
        self._recorder.record(result)
        return result

    def clear(self) -> None:
        """Mark session state for CLEAR on the next run (ICS §3.3 semantics)."""
        self._pending_clear = True

    @property
    def turn_count(self) -> int:
        data = self._agent._backend.load(self.session_id)
        return data.turn_count if data else 0

    @property
    def metrics(self) -> "SessionMetrics":
        """Accumulated observability metrics for this session."""
        return self._recorder.metrics

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_session_state(self, data: SessionData) -> str:
        parts: list[str] = []
        if data.cleared:
            parts.append("###CLEAR###")
        if data.context:
            ctx_lines = "\n".join(f"{k}: {v}" for k, v in data.context.items())
            parts.append(f"Context:\n{ctx_lines}")
        if data.entries:
            parts.append("History:\n" + "\n".join(data.entries[-20:]))  # last 20 turns
        return "\n\n".join(parts) if parts else ""

    def _execute_tool(self, tc: dict, data: SessionData) -> ToolCallRecord:
        """Dispatch a tool call through the registry, enforcing contracts."""
        name = tc["name"]
        inp = tc.get("input", {})

        registry = self._agent._registry
        if registry is None:
            return ToolCallRecord(
                tool_name=name,
                input=inp,
                output=f"Error: no tool registry configured",
                duration_ms=0,
                blocked=True,
            )

        try:
            t0 = time.monotonic()
            output = registry.dispatch(name, inp)
            duration_ms = int((time.monotonic() - t0) * 1000)
            return ToolCallRecord(
                tool_name=name,
                input=inp,
                output=output,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            return ToolCallRecord(
                tool_name=name,
                input=inp,
                output=f"Error: {exc}",
                duration_ms=0,
                blocked=False,
            )


class SessionContext:
    """Context manager wrapper returned by ``Agent.session()``."""

    def __init__(self, agent: "Agent", session_vars: dict[str, Any]) -> None:
        self._agent = agent
        self._session_vars = session_vars
        self._session: Session | None = None

    def __enter__(self) -> Session:
        session_id = str(uuid.uuid4())
        self._session = Session(self._agent, session_id, self._session_vars)
        return self._session

    def __exit__(self, *_: Any) -> None:
        pass  # Sessions are not auto-deleted; callers can reuse session_id
