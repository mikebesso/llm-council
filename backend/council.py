"""Metadata-driven LLM Council orchestration (workhorse runtime).

Fork B architecture:
- metadata.py is the dumb data layer (load/parse dataclasses from TOML+Markdown)
- council.py hydrates runtime actors and executes stages

This module keeps a backward-compatible API surface for the FastAPI endpoints.
"""

from __future__ import annotations

import asyncio
import json
import uuid
import string
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Tuple

from .observability import log_event, set_run_id
from .openrouter import query_model
from . import metadata as md

# Wall-clock timeouts (seconds) for each stage fan-out.
# These prevent the council from being held hostage by a single slow/blocked provider.
STAGE1_WALL_TIMEOUT_S = 45.0
STAGE2_WALL_TIMEOUT_S = 60.0
STAGE3_WALL_TIMEOUT_S = 120.0

# Per-request timeout passed to OpenRouter (seconds)
MODEL_TIMEOUT_S = 120.0

# Default council used when callers do not specify one.
DEFAULT_COUNCIL_ID = "ai-council"


# ------------------------------
# Model client abstraction
# ------------------------------

class ModelClient(Protocol):
    async def query(
        self,
        model_id: str,
        messages: List[Dict[str, str]],
        *,
        timeout: float,
        run_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        ...


@dataclass
class OpenRouterModelClient:
    async def query(
        self,
        model_id: str,
        messages: List[Dict[str, str]],
        *,
        timeout: float,
        run_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        return await query_model(model_id, messages, timeout=timeout, run_id=run_id)


@dataclass
class MockModelClient:
    """Deterministic offline client for fast tests/dev."""

    prefix: str = "MOCK"

    async def query(
        self,
        model_id: str,
        messages: List[Dict[str, str]],
        *,
        timeout: float,
        run_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        _ = timeout
        _ = run_id

        last_user = ""
        for m in reversed(messages or []):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break

        # If the prompt demands JSON, return a minimal valid object.
        if "Output MUST be valid JSON" in last_user or ("Do not include markdown" in last_user and "JSON" in last_user):
            return {"content": "{}"}

        return {"content": f"[{self.prefix}:{model_id}] {last_user}".strip()}


def _build_messages_simple(user_text: str, *, system_text: str) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages.append({"role": "user", "content": user_text})
    return messages


# ------------------------------
# Hydrated runtime actors
# ------------------------------

@dataclass
class Member:
    id: str
    metadata: md.MemberMetadata
    persona: md.PersonaMetadata

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def model_id(self) -> str:
        return self.metadata.model_id


@dataclass
class Chairman:
    id: str
    metadata: md.ChairmanMetadata
    persona: md.PersonaMetadata

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def model_id(self) -> str:
        return self.metadata.model_id


def _format_template(
    template: str,
    context: Dict[str, Any],
    *,
    template_label: str,
    council_id: Optional[str] = None,
    stage_id: Optional[str] = None,
) -> str:
    """Safely format a prompt template with helpful errors.

    We rely on Python's `{name}` format syntax. When a key is missing or a format string is malformed,
    default exceptions are annoyingly vague during council execution. This wrapper:

    - Detects missing keys and reports them along with available context keys.
    - Surfaces common user mistakes (e.g., unescaped `{` / `}`) with a clear hint.

    Notes:
    - Supports basic field names like `{user_query}`.
    - For advanced fields like `{foo.bar}` or `{foo[0]}`, we validate the root `foo`.
    """

    if not isinstance(template, str) or not template.strip():
        raise ValueError(f"Template '{template_label}' is empty")

    # Extract referenced field names from the template so we can preflight missing keys.
    formatter = string.Formatter()
    referenced: List[str] = []
    try:
        for _literal, field_name, _fmt, _conv in formatter.parse(template):
            if not field_name:
                continue
            # field_name may include indexing/attributes; only validate the root.
            root = field_name.split(".", 1)[0].split("[", 1)[0]
            referenced.append(root)
    except ValueError as e:
        # Malformed format string (often unmatched braces)
        where = f"council='{council_id}' stage='{stage_id}' " if (council_id or stage_id) else ""
        raise ValueError(
            f"Malformed template in {template_label} ({where}): {e}. "
            "Hint: if you need a literal '{' or '}', escape it as '{{' or '}}'."
        ) from e

    missing = sorted({k for k in referenced if k not in context})
    if missing:
        where = f"council='{council_id}' stage='{stage_id}' " if (council_id or stage_id) else ""
        available = ", ".join(sorted(context.keys()))
        # Show a short snippet so users can find the area without dumping the whole prompt.
        snippet = template.strip().replace("\n", " ")
        if len(snippet) > 220:
            snippet = snippet[:217] + "..."
        raise KeyError(
            f"Missing template keys in {template_label} ({where}). "
            f"Missing: {missing}. Available keys: [{available}]. "
            f"Template snippet: '{snippet}'."
        )

    try:
        return template.format_map(context)
    except KeyError as e:
        # KeyError can still occur for nested/indexed fields.
        key = str(e).strip("'\"")
        where = f"council='{council_id}' stage='{stage_id}' " if (council_id or stage_id) else ""
        available = ", ".join(sorted(context.keys()))
        raise KeyError(
            f"Template key error in {template_label} ({where}): '{key}'. "
            f"Available keys: [{available}]."
        ) from e
    except ValueError as e:
        # ValueError often indicates malformed format specifiers.
        where = f"council='{council_id}' stage='{stage_id}' " if (council_id or stage_id) else ""
        raise ValueError(
            f"Template format error in {template_label} ({where}): {e}."
        ) from e


@dataclass
class Council:
    id: str
    metadata: md.CouncilMetadata
    chairman: Chairman
    members: List[Member]
    stages: List[md.StageMetadata]

    @property
    def name(self) -> str:
        return self.metadata.name

    def render_stage_prompt(self, stage: md.StageMetadata, *, context: Dict[str, Any]) -> str:
        """Render a stage prompt.

        Current implementation:
        - Uses `stage.prompt` if present (from markdown body or frontmatter)
        - Supports `{key}` formatting via `format_map(context)`

        Stage template 'parts' support can be added later without changing callers.
        """
        prompt = getattr(stage, "prompt", "") or ""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"Stage '{stage.id}' has no prompt content")
        return _format_template(
            prompt,
            context,
            template_label="stage.prompt",
            council_id=self.id,
            stage_id=stage.id,
        )

    async def run(
        self,
        user_query: str,
        *,
        client: ModelClient,
        timeout_s: float = MODEL_TIMEOUT_S,
        run_id: Optional[str] = None,
        stages_to_run: Optional[List[int]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
        """Run council stages.

        Returns: (stage1_results, stage2_results, stage3_result, metadata)
        """
        if run_id is None:
            run_id = f"run-{uuid.uuid4()}"
            set_run_id(run_id)
            log_event({"event": "council.run.generated", "run_id": run_id, "council_id": self.id})

        stage1_results: List[Dict[str, Any]] = []
        stage2_results: List[Dict[str, Any]] = []
        stage3_result: Dict[str, Any] = {}

        # Shared context for prompt rendering
        ctx: Dict[str, Any] = {
            "user_query": user_query,
            "council_prompt": getattr(self.metadata, "prompt", "") or "",
            "stage_context": "",
            "responses_text": "",
            "rankings_text": "",
            "stage1_text": "",
            "stage2_text": "",
        }

        def _refresh_stage_context() -> None:
            parts: List[str] = []
            if ctx.get("stage1_text"):
                parts.append(f"## Stage 1 – Member Responses\n{ctx['stage1_text']}")
            if ctx.get("stage2_text"):
                parts.append(f"## Stage 2 – Peer Rankings\n{ctx['stage2_text']}")
            ctx["stage_context"] = "\n\n".join(parts).strip()

        def _format_stage1_text(rows: List[Dict[str, Any]]) -> str:
            lines: List[str] = []
            for i, r in enumerate(rows, start=1):
                lines.append(f"Member {i}: {r.get('member_name') or r.get('model') or 'member'}")
                lines.append(r.get("response", ""))
                lines.append("")
            return "\n".join(lines).strip()

        def _format_stage2_text(rows: List[Dict[str, Any]]) -> str:
            lines: List[str] = []
            for i, r in enumerate(rows, start=1):
                lines.append(f"Judge {i}: {r.get('member_name') or r.get('model') or 'judge'}")
                lines.append(r.get("ranking", ""))
                lines.append("")
            return "\n".join(lines).strip()

        def _parse_json(content: str) -> Dict[str, Any]:
            try:
                obj = json.loads(content)
                return obj if isinstance(obj, dict) else {}
            except Exception:
                return {}

        # Stage selection is still numeric (1/2/3) for API compatibility.
        stages_to_run_set = set(stages_to_run or [1, 2, 3])

        log_event({
            "event": "council.run.start",
            "run_id": run_id,
            "council_id": self.id,
            "stages": sorted(list(stages_to_run_set)),
            "user_query_len": len(user_query or ""),
            "members": [{"id": m.id, "name": m.name, "model_id": m.model_id, "persona": m.persona.name} for m in self.members],
            "chairman": {"id": self.chairman.id, "name": self.chairman.name, "model_id": self.chairman.model_id, "persona": self.chairman.persona.name},
        })

        # We execute stages based on metadata stage ordering.
        # Convention:
        # - Stage 1 is the first fanout stage
        # - Stage 2 is the second fanout stage
        # - Stage 3 is the first chairman (single) stage after those
        fanout_seen = 0
        chairman_seen = 0

        for stage in self.stages:
            kind = getattr(stage, "kind", "") or ""

            # Fanout stages (per member)
            if kind == "fanout_members":
                fanout_seen += 1
                if fanout_seen == 1 and 1 not in stages_to_run_set:
                    continue
                if fanout_seen == 2 and 2 not in stages_to_run_set:
                    continue

                wall_timeout = STAGE1_WALL_TIMEOUT_S if fanout_seen == 1 else STAGE2_WALL_TIMEOUT_S

                member_tasks: List[Tuple[Member, asyncio.Task]] = []
                for m in self.members:
                    persona_prompt = (m.persona.prompt or "").strip()
                    addendum = (m.metadata.prompt or "").strip()
                    system_text = f"{persona_prompt}\n\n{addendum}".strip() if addendum else persona_prompt

                    ctx["persona_prompt"] = system_text
                    prompt = self.render_stage_prompt(stage, context=ctx)
                    messages = _build_messages_simple(prompt, system_text=system_text)
                    member_tasks.append((m, asyncio.create_task(client.query(m.model_id, messages, timeout=timeout_s, run_id=run_id))))

                done, pending = await asyncio.wait([t for (_m, t) in member_tasks], timeout=wall_timeout)

                pending_members: List[Dict[str, str]] = []
                for m, t in member_tasks:
                    if t in pending:
                        pending_members.append({"member_name": m.name, "model_id": m.model_id})
                        t.cancel()

                if pending_members:
                    log_event({
                        "event": "council.stage.timeout",
                        "run_id": run_id,
                        "stage_id": stage.id,
                        "wall_timeout_s": wall_timeout,
                        "pending_members": pending_members,
                    })

                results: List[Dict[str, Any]] = []
                for m, t in member_tasks:
                    if t in done:
                        try:
                            r = t.result()
                        except Exception as e:
                            log_event({
                                "event": "council.stage.task_error",
                                "run_id": run_id,
                                "stage_id": stage.id,
                                "member_id": m.id,
                                "member_name": m.name,
                                "model_id": m.model_id,
                                "error": str(e)[:200],
                            })
                            continue

                        content = r.get("content", "") if isinstance(r, dict) else ""
                        results.append({
                            "model": m.name,  # backward-compatible UI field
                            "member_name": m.name,
                            "persona": m.persona.name,
                            "response" if fanout_seen == 1 else "ranking": content,
                            "run_id": run_id,
                            "model_id": m.model_id,
                        })

                if fanout_seen == 1:
                    stage1_results = results
                    ctx["responses_text"] = "\n\n".join([f"{r['member_name']}\n{r.get('response','')}" for r in stage1_results])
                    ctx["stage1_text"] = _format_stage1_text(stage1_results)
                else:
                    # Keep both the raw ranking text and the parsed ranking to preserve prior behavior
                    stage2_results = []
                    for r in results:
                        full_text = r.get("ranking", "")
                        stage2_results.append({
                            **r,
                            "parsed_ranking": parse_ranking_from_text(full_text),
                        })
                    ctx["rankings_text"] = "\n\n".join([f"{r['member_name']}\n{r.get('ranking','')}" for r in stage2_results])
                    ctx["stage2_text"] = _format_stage2_text(stage2_results)

                _refresh_stage_context()
                continue

            # Chairman stages (single)
            chairman_seen += 1
            if chairman_seen == 1 and 3 not in stages_to_run_set:
                continue

            persona_prompt = (self.chairman.persona.prompt or "").strip()
            addendum = (self.chairman.metadata.prompt or "").strip()
            system_text = f"{persona_prompt}\n\n{addendum}".strip() if addendum else persona_prompt

            ctx["persona_prompt"] = system_text
            prompt = self.render_stage_prompt(stage, context=ctx)
            messages = _build_messages_simple(prompt, system_text=system_text)

            try:
                r = await asyncio.wait_for(
                    client.query(self.chairman.model_id, messages, timeout=timeout_s, run_id=run_id),
                    timeout=STAGE3_WALL_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                log_event({
                    "event": "council.stage.timeout",
                    "run_id": run_id,
                    "stage_id": stage.id,
                    "wall_timeout_s": STAGE3_WALL_TIMEOUT_S,
                    "chairman_model_id": self.chairman.model_id,
                })
                r = None

            content = r.get("content", "") if isinstance(r, dict) and r is not None else ""

            # Optional stage semantics by id (kept for existing templates)
            if stage.id == "improve-prompt":
                parsed = _parse_json(content)
                improved = parsed.get("improved_query")
                if isinstance(improved, str) and improved.strip():
                    ctx["improved_query"] = improved.strip()
            elif stage.id == "validate-council-applicability":
                parsed = _parse_json(content)
                ctx["council_applicability"] = parsed
                if isinstance(parsed, dict) and parsed.get("decision") == "STOP":
                    pretty = json.dumps(parsed, indent=2) if parsed else content
                    stage3_result = {
                        "model": self.chairman.name,
                        "member_name": self.chairman.name,
                        "persona": self.chairman.persona.name,
                        "response": pretty,
                        "model_id": self.chairman.model_id,
                        "chairman_model_id": self.chairman.model_id,
                    }
                    break
            else:
                stage3_result = {
                    "model": self.chairman.name,
                    "member_name": self.chairman.name,
                    "persona": self.chairman.persona.name,
                    "response": content or "Error: Unable to generate final synthesis.",
                    "model_id": self.chairman.model_id,
                    "chairman_model_id": self.chairman.model_id,
                }

            _refresh_stage_context()

        # Label mapping + aggregate rankings are retained for the UI/debug metadata.
        labels = [chr(65 + i) for i in range(len(stage1_results))]
        label_to_model = {f"Response {label}": (r.get("member_name") or r.get("model") or f"Member {label}") for label, r in zip(labels, stage1_results)}
        aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model) if stage2_results else []

        metadata_out = {
            "label_to_model": label_to_model,
            "aggregate_rankings": aggregate_rankings,
            "run_id": run_id,
            "council_id": self.id,
        }

        log_event({
            "event": "council.run.done",
            "run_id": run_id,
            "council_id": self.id,
            "stage1_count": len(stage1_results),
            "stage2_count": len(stage2_results),
            "aggregate_count": len(aggregate_rankings),
            "final_len": len(stage3_result.get("response") or ""),
        })

        # Clear ambient context for safety (e.g., in long-lived server processes)
        set_run_id(None)

        return stage1_results, stage2_results, stage3_result, metadata_out


# ------------------------------
# Hydration helpers (Fork B)
# ------------------------------

def _load_typed(kind: str, id: str) -> md._CouncilMetadata:
    return md._CouncilMetadata.from_file(kind, id=id)


def _load_persona(persona_id: str) -> md.PersonaMetadata:
    p = _load_typed("persona", persona_id)
    if not isinstance(p, md.PersonaMetadata):
        raise TypeError(f"Expected PersonaMetadata for persona '{persona_id}', got {type(p).__name__}")
    return p


def _load_member(member_id: str) -> Member:
    m = _load_typed("member", member_id)
    if not isinstance(m, md.MemberMetadata):
        raise TypeError(f"Expected MemberMetadata for member '{member_id}', got {type(m).__name__}")
    persona = _load_persona(m.persona)
    return Member(id=member_id, metadata=m, persona=persona)


def _load_chairman(chairman_id: str) -> Chairman:
    c = _load_typed("chairman", chairman_id)
    if not isinstance(c, md.ChairmanMetadata):
        raise TypeError(f"Expected ChairmanMetadata for chairman '{chairman_id}', got {type(c).__name__}")
    persona = _load_persona(c.persona)
    return Chairman(id=chairman_id, metadata=c, persona=persona)


def _load_stage(stage_id: str) -> md.StageMetadata:
    s = _load_typed("stage", stage_id)
    if not isinstance(s, md.StageMetadata):
        raise TypeError(f"Expected StageMetadata for stage '{stage_id}', got {type(s).__name__}")
    return s


def load_council(council_id: str) -> Council:
    c = _load_typed("council", council_id)
    if not isinstance(c, md.CouncilMetadata):
        raise TypeError(f"Expected CouncilMetadata for council '{council_id}', got {type(c).__name__}")

    chairman = _load_chairman(c.chairman)
    members = [_load_member(member_id) for member_id in c.members]
    stages = [_load_stage(stage_id) for stage_id in c.stages]

    return Council(id=council_id, metadata=c, chairman=chairman, members=members, stages=stages)


# ------------------------------
# Backward-compatible public API
# ------------------------------

async def stage1_collect_responses(user_query: str, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    council = load_council(DEFAULT_COUNCIL_ID)
    s1, _s2, _s3, _meta = await council.run(
        user_query,
        client=OpenRouterModelClient(),
        run_id=run_id,
        stages_to_run=[1],
    )
    return s1


async def stage2_collect_rankings(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    run_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    # NOTE: Stage 2 in the metadata-driven flow is run from scratch to ensure prompt context.
    # We keep the signature for compatibility but do not reuse the passed stage1_results.
    council = load_council(DEFAULT_COUNCIL_ID)
    _s1, s2, _s3, meta = await council.run(
        user_query,
        client=OpenRouterModelClient(),
        run_id=run_id,
        stages_to_run=[1, 2],
    )
    return s2, meta.get("label_to_model", {})


async def stage3_synthesize_final(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    # NOTE: Stage 3 in the metadata-driven flow is run from scratch to ensure prompt context.
    # We keep the signature for compatibility but do not reuse the passed stage results.
    council = load_council(DEFAULT_COUNCIL_ID)
    _s1, _s2, s3, _meta = await council.run(
        user_query,
        client=OpenRouterModelClient(),
        run_id=run_id,
        stages_to_run=[1, 2, 3],
    )
    return s3


def parse_ranking_from_text(ranking_text: str) -> List[str]:
    """Parse the FINAL RANKING section from a model response."""
    import re

    if "FINAL RANKING:" in ranking_text:
        parts = ranking_text.split("FINAL RANKING:")
        if len(parts) >= 2:
            ranking_section = parts[1]
            numbered_matches = re.findall(r"\d+\.\s*Response [A-Z]", ranking_section)
            if numbered_matches:
                return [re.search(r"Response [A-Z]", m).group() for m in numbered_matches]

            matches = re.findall(r"Response [A-Z]", ranking_section)
            return matches

    matches = re.findall(r"Response [A-Z]", ranking_text)
    return matches


def calculate_aggregate_rankings(stage2_results: List[Dict[str, Any]], label_to_model: Dict[str, str]) -> List[Dict[str, Any]]:
    """Calculate aggregate rankings across all models."""
    from collections import defaultdict

    model_positions: Dict[str, List[int]] = defaultdict(list)

    for ranking in stage2_results:
        parsed_ranking = parse_ranking_from_text(ranking.get("ranking", ""))
        for position, label in enumerate(parsed_ranking, start=1):
            if label in label_to_model:
                model_name = label_to_model[label]
                model_positions[model_name].append(position)

    aggregate: List[Dict[str, Any]] = []
    for model_name, positions in model_positions.items():
        if positions:
            avg_rank = sum(positions) / len(positions)
            aggregate.append({
                "model": model_name,
                "average_rank": round(avg_rank, 2),
                "rankings_count": len(positions),
            })

    aggregate.sort(key=lambda x: x["average_rank"])
    return aggregate


async def generate_conversation_title(user_query: str) -> str:
    """Generate a short title for a conversation based on the first user message."""

    title_prompt = (
        "Generate a very short title (3-5 words maximum) that summarizes the following question.\n"
        "The title should be concise and descriptive. Do not use quotes or punctuation in the title.\n\n"
        f"Question: {user_query}\n\nTitle:"
    )

    messages = _build_messages_simple(title_prompt, system_text="")
    response = await query_model("google/gemini-2.5-flash", messages, timeout=30.0)

    if response is None:
        return "New Conversation"

    title = (response.get("content", "New Conversation") or "New Conversation").strip().strip('"\'')
    if len(title) > 50:
        title = title[:47] + "..."
    return title


async def run_full_council(user_query: str, council_id: str = DEFAULT_COUNCIL_ID) -> Tuple[List, List, Dict, Dict]:
    """Run the complete 3-stage council process."""

    council = load_council(council_id)
    stage1_results, stage2_results, stage3_result, metadata_out = await council.run(
        user_query,
        client=OpenRouterModelClient(),
        stages_to_run=[1, 2, 3],
    )

    if not stage1_results:
        return [], [], {"model": "error", "response": "All models failed to respond. Please try again."}, metadata_out

    return stage1_results, stage2_results, stage3_result, metadata_out