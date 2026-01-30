from abc import ABC
import os
from typing import Any, Dict, List, Optional, Tuple, Type, ClassVar, Protocol, Callable, Awaitable
from pathlib import Path
from dataclasses import dataclass, field
import json

#
# --- Stage Template Schema Support ---
#

@dataclass(frozen=True)
class StagePromptPart:
    source: str
    label: str | None = None
    required: bool = True
    render_style: str = "markdown"
    content: str | None = None


@dataclass(frozen=True)
class StageResponseFormat:
    type: str = "text"
    must_include_keys: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class StageFailurePolicy:
    on_refusal: str = "record_error"
    on_parse_error: str = "fallback_text"


@dataclass(frozen=True)
class StagePromptSpec:
    parts: List[StagePromptPart] = field(default_factory=list)


@dataclass(frozen=True)
class StageTemplate:
    kind: str = "single"
    purpose: str = ""
    version: str = "1.0"
    inputs_required: List[str] = field(default_factory=list)
    inputs_optional: List[str] = field(default_factory=list)
    response_format: StageResponseFormat = field(default_factory=StageResponseFormat)
    failure_policy: StageFailurePolicy = field(default_factory=StageFailurePolicy)
    prompt: StagePromptSpec = field(default_factory=StagePromptSpec)

# yes I know this is wrong, but vs code is fucked right now, I'll fix it later
from observability import get_logger

import asyncio
logger = get_logger(__name__)   


@dataclass(frozen=True)
class _RegistryEntry:
    cls: Type["_CouncilMetadata"]
    subfolder: Path

@dataclass(frozen=True)
class _CouncilMetadata(ABC):
    """Class to load and manage metadata files."""

    id: str
    name: str
    prompt: str


    _registry: ClassVar[Dict[str, _RegistryEntry]] = {}


    @classmethod
    def register(
        cls,
        kind: str,
        subclass: Type["_CouncilMetadata"],
        subfolder: Path
    ):
        if kind in cls._registry:
            raise ValueError(f"Kind '{kind}' is already registered.")
        cls._registry[kind] = _RegistryEntry(subclass, subfolder)

    @classmethod
    def _read_text(cls, path: Path) -> str:
        if not path.exists():
            msg = f"Missing required file: {path}"
            logger.error(msg)
            raise RuntimeError(msg)
        return path.read_text(encoding="utf-8")

    @classmethod
    def _parse_toml_front_matter(
        cls,
        md_text: str, 
        *, 
        source: str, 
        require: bool = True
    ) -> Tuple[Dict[str, Any], str]:
        """Parse TOML front matter delimited by +++ ... +++ in a markdown file.

        If `require` is False and the file does not start with +++, returns ({}, full_text).

        Returns: (front_matter_dict, body_text)
        """
        # Normalize common file artifacts.
        if md_text.startswith("\ufeff"):
            md_text = md_text.lstrip("\ufeff")
        lines = md_text.splitlines()

        # Optional front matter support (useful for simple persona markdown files).
        # Be forgiving: allow leading blank lines before the +++ delimiter.
        first_nonempty: Optional[int] = None
        for i, line in enumerate(lines):
            if line.strip() != "":
                first_nonempty = i
                break

        if first_nonempty is None or lines[first_nonempty].strip() != "+++":
            if require:
                msg = (
                    f"CONFIG ERROR: {source} is missing TOML front matter start delimiter (+++)."
                )
                logger.error(msg)
                raise RuntimeError(msg)
            # No front matter; treat whole file as body.
            return {}, md_text.lstrip("\n")

        end_idx: Optional[int] = None
        for i in range((first_nonempty or 0) + 1, len(lines)):
            if lines[i].strip() == "+++":
                end_idx = i
                break

        if end_idx is None:
            msg = f"CONFIG ERROR: {source} is missing TOML front matter end delimiter (+++)."
            logger.error(msg)
            raise RuntimeError(msg)

        toml_text = "\n".join(lines[(first_nonempty or 0) + 1:end_idx]).strip()
        body_text = "\n".join(lines[end_idx + 1 :]).lstrip("\n")

        # Python 3.11+ has tomllib in stdlib.
        try:
            import tomllib  # type: ignore
        except Exception as e:  # pragma: no cover
            msg = f"CONFIG ERROR: Unable to import tomllib for TOML parsing: {e}"
            logger.error(msg)
            raise RuntimeError(msg)

        try:
            data = tomllib.loads(toml_text) if toml_text else {}
        except Exception as e:
            msg = f"CONFIG ERROR: TOML parse error in {source}: {e}"
            logger.error(msg)
            raise RuntimeError(msg)

        return data, body_text

    @classmethod
    def _get_root_dir(cls) -> Path:
        # Allow the metadata root to be configured for different deployments/layouts.
        root = os.environ.get("COUNCIL_METADATA_ROOT")
        if root:
            return Path(root).expanduser().resolve()
        return (Path(__file__).parent.parent / "council-metadata").resolve()
    
    @classmethod
    def _get_metadata_path(cls, registry_entry:_RegistryEntry, id:str) -> Path:
        kind_folder = cls._get_root_dir() / registry_entry.subfolder

        path = (kind_folder / id).with_suffix(".md")

        if not path.exists():
            msg = f"Missing required file: {path}"
            logger.error(msg)
            raise RuntimeError(msg)

        return path


    @classmethod
    def from_file(cls, kind: str, id: str) -> "_CouncilMetadata":
        if kind is None:
            raise ValueError("kind is required")
        registry_entry = cls._registry.get(kind)
        if registry_entry is None:
            raise ValueError(f"Unknown kind: {kind}")
        subclass = registry_entry.cls
        if subclass is None:
            raise ValueError(f"Unknown kind: {kind}")
        path = cls._get_metadata_path(registry_entry, id)
        md = cls._read_text(path)
        fm, body = cls._parse_toml_front_matter(md, source=str(path))

        # Ensure the instance id is tied to the file identifier we loaded.
        fm["id"] = id

        # Special handling for stage files with TOML prompt table (don't clobber prompt)
        if kind == "stage" and isinstance(fm.get("prompt"), dict):
            fm["template_prompt"] = fm.pop("prompt")
            fm["prompt"] = body
        else:
            fm["prompt"] = body

        # Tolerant schema: ignore unknown keys but log them for visibility.
        allowed = set(getattr(subclass, "__dataclass_fields__", {}).keys())
        extra_keys = sorted([k for k in fm.keys() if k not in allowed])
        if extra_keys:
            logger.warning(
                "Ignoring unknown metadata keys",
                extra={
                    "source": str(path),
                    "kind": kind,
                    "id": id,
                    "extra_keys": extra_keys,
                },
            )

        filtered = {k: v for k, v in fm.items() if k in allowed}
        return subclass(**filtered)

    
@dataclass(frozen=True)
class CouncilMetadata(_CouncilMetadata):
    chairman: str
    members: List[str]
    stages: List[str]

_CouncilMetadata.register("council", CouncilMetadata, Path("councils"))


@dataclass(frozen=True)
class PersonaMetadata(_CouncilMetadata):
    """A reusable system prompt persona."""

_CouncilMetadata.register("persona", PersonaMetadata, Path("personas"))


@dataclass(frozen=True)
class MemberMetadata(_CouncilMetadata):
    model_id: str
    persona: str

_CouncilMetadata.register("member", MemberMetadata, Path("members"))



@dataclass(frozen=True)
class StageMetadata(_CouncilMetadata):
    kind: str = "single"
    purpose: str = ""
    version: str = "1.0"
    inputs_required: List[str] = field(default_factory=list)
    inputs_optional: List[str] = field(default_factory=list)
    response_format: Dict[str, Any] = field(default_factory=dict)
    failure_policy: Dict[str, Any] = field(default_factory=dict)
    template_prompt: Dict[str, Any] = field(default_factory=dict)

_CouncilMetadata.register("stage", StageMetadata, Path("stages"))


@dataclass(frozen=True)
class ChairmanMetadata(_CouncilMetadata):
    model_id: str
    persona: str

_CouncilMetadata.register("chairman", ChairmanMetadata, Path("chairmen"))


@dataclass(frozen=True)
class Prompt(_CouncilMetadata):
    pass
_CouncilMetadata.register("prompt", Prompt, Path("prompts"))




# --- Minimal model client protocol and helpers for council orchestration ---

class ModelClient(Protocol):
    """Minimal interface for something that can query a model.

    Real mode: wraps OpenRouter / provider clients.
    Mock mode: returns deterministic content for fast dev + tests.
    """

    async def query(self, model_id: str, messages: List[Dict[str, str]], *, timeout: float) -> Dict[str, Any]:
        ...


@dataclass
class MockModelClient:
    """A deterministic fake client for offline development."""

    prefix: str = "MOCK"

    async def query(self, model_id: str, messages: List[Dict[str, str]], *, timeout: float) -> Dict[str, Any]:
        # Keep it deterministic and fast; ignore timeout.
        last_user = ""
        for m in reversed(messages or []):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break

        # Detect explicit JSON-only contract language (avoid false positives on incidental words).
        json_requested = (
            "Output MUST be valid JSON" in last_user
            or ("Do not include markdown" in last_user and "JSON" in last_user)
        )

        if json_requested:
            # Two known schemas used by council stages.
            # 1) improve-prompt schema
            if "\"improved_query\"" in last_user or "improved_query:" in last_user:
                payload = {
                    "improved_query": "MOCK: " + (last_user[:200] if last_user else "This is a mock improved query."),
                    "notes": "mock",
                    "assumptions": [],
                    "clarifying_questions": [],
                }
                return {"content": json.dumps(payload)}

            # 2) validate-council-applicability schema
            if (
                ("\"decision\"" in last_user or "decision:" in last_user)
                and ("\"alternatives\"" in last_user or "alternatives:" in last_user)
            ):
                payload = {
                    "applicable": True,
                    "decision": "CONTINUE",
                    "reason": "mock",
                    "alternatives": [],
                }
                return {"content": json.dumps(payload)}

            # Unknown JSON request: return an empty object.
            return {"content": "{}"}

        # Otherwise, default behavior: echo the final user prompt for visibility.
        content = f"[{self.prefix}:{model_id}] {last_user}".strip()
        return {"content": content}


def build_messages_simple(user_text: str, *, system_text: str) -> List[Dict[str, str]]:
    """Build the minimum viable chat message list.

    This avoids importing the full personas/message builder while we stabilize the new metadata-driven flow.
    """
    messages: List[Dict[str, str]] = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages.append({"role": "user", "content": user_text})
    return messages


def _format_stage1_responses(stage1_results: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for i, r in enumerate(stage1_results, start=1):
        lines.append(f"Member {i}: {r.get('member_name') or r.get('id') or 'member'}")
        lines.append(r.get("response", ""))
        lines.append("")
    return "\n".join(lines).strip()


def _format_stage2_rankings(stage2_results: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for i, r in enumerate(stage2_results, start=1):
        lines.append(f"Judge {i}: {r.get('member_name') or r.get('id') or 'judge'}")
        lines.append(r.get("ranking", ""))
        lines.append("")
    return "\n".join(lines).strip()

@dataclass
class Member:
    """A hydrated council member (member metadata + persona metadata)."""

    id: str
    metadata: MemberMetadata
    persona: PersonaMetadata

    @classmethod
    def from_id(cls, id: str) -> "Member":
        md = _CouncilMetadata.from_file("member", id=id)
        if not isinstance(md, MemberMetadata):
            raise TypeError(f"Expected MemberMetadata for member '{id}', got {type(md).__name__}")

        persona_md = _CouncilMetadata.from_file("persona", id=md.persona)
        if not isinstance(persona_md, PersonaMetadata):
            raise TypeError(
                f"Expected PersonaMetadata for persona '{md.persona}', got {type(persona_md).__name__}"
            )

        return cls(id=id, metadata=md, persona=persona_md)

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def model_id(self) -> str:
        return self.metadata.model_id


@dataclass
class Chairman:
    """A hydrated chairman (chairman metadata + persona metadata)."""

    id: str
    metadata: ChairmanMetadata
    persona: PersonaMetadata

    @classmethod
    def from_id(cls, id: str) -> "Chairman":
        md = _CouncilMetadata.from_file("chairman", id=id)
        if not isinstance(md, ChairmanMetadata):
            raise TypeError(f"Expected ChairmanMetadata for chairman '{id}', got {type(md).__name__}")

        persona_md = _CouncilMetadata.from_file("persona", id=md.persona)
        if not isinstance(persona_md, PersonaMetadata):
            raise TypeError(
                f"Expected PersonaMetadata for persona '{md.persona}', got {type(persona_md).__name__}"
            )

        return cls(id=id, metadata=md, persona=persona_md)

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def model_id(self) -> str:
        return self.metadata.model_id


@dataclass
class Council:
    """A hydrated council: council metadata + chairman + members + ordered stages."""

    id: str
    metadata: CouncilMetadata
    chairman: Chairman
    members: List[Member]
    stages: List[StageMetadata]

    @classmethod
    def from_id(cls, id: str) -> "Council":
        md = _CouncilMetadata.from_file("council", id=id)
        if not isinstance(md, CouncilMetadata):
            raise TypeError(f"Expected CouncilMetadata for council '{id}', got {type(md).__name__}")

        chairman = Chairman.from_id(md.chairman)

        # Preserve declared order; allow duplicates only if explicitly intended.
        members = [Member.from_id(member_id) for member_id in md.members]

        stages: List[StageMetadata] = []
        for stage_id in md.stages:
            stage_md = _CouncilMetadata.from_file("stage", id=stage_id)
            if not isinstance(stage_md, StageMetadata):
                raise TypeError(
                    f"Expected StageMetadata for stage '{stage_id}', got {type(stage_md).__name__}"
                )
            stages.append(stage_md)

        return cls(id=id, metadata=md, chairman=chairman, members=members, stages=stages)

    @property
    def name(self) -> str:
        return self.metadata.name

    def render_stage_prompt(self, stage_id: str, *, context: Dict[str, Any]) -> str:
        """Render a stage prompt from new stage template schema."""
        stage = next((s for s in self.stages if s.id == stage_id), None)
        if stage is None:
            raise ValueError(f"Unknown stage_id '{stage_id}' for council '{self.id}'")

        # Validate required inputs
        required_inputs = getattr(stage, "inputs_required", []) or []
        for k in required_inputs:
            if k not in context or context[k] is None or context[k] == "":
                raise KeyError(
                    f"Missing required input '{k}' in context when rendering stage '{stage_id}' for council '{self.id}'"
                )

        # Get prompt parts (from [[prompt.parts]] TOML front matter)
        prompt_parts = []
        # Use new schema if present
        template_prompt = getattr(stage, "template_prompt", None)
        if template_prompt and isinstance(template_prompt, dict):
            prompt_parts = template_prompt.get("parts", [])
        # fallback: treat body as a single markdown part
        if not prompt_parts and getattr(stage, "prompt", None):
            return stage.prompt.format_map(context)

        rendered = []
        for part in prompt_parts:
            # part: dict from TOML
            source = part.get("source")
            label = part.get("label")
            required = part.get("required", True)
            render_style = part.get("render_style", "markdown")
            content = None
            if source == "instructions":
                content = part.get("content", "")
            else:
                content = context.get(source)
            if required and (content is None or str(content).strip() == ""):
                raise KeyError(
                    f"Missing required prompt part source '{source}' in context when rendering stage '{stage_id}' for council '{self.id}'"
                )
            content = "" if content is None else str(content)
            if render_style == "markdown":
                if label:
                    rendered.append(f"### {label}\n{content}\n")
                else:
                    rendered.append(f"{content}\n")
            elif render_style == "text":
                rendered.append(f"{content}\n")
            elif render_style == "json":
                rendered.append(f"```json\n{content}\n```\n")
            else:
                rendered.append(f"{content}\n")
        return "".join(rendered).strip()


    async def run(
        self,
        user_query: str,
        *,
        client: ModelClient,
        timeout_s: float = 120.0,
    ) -> Dict[str, Any]:
        """Run this council using injected model-calling behavior with new stage template schema context keys."""
        stage1_results: List[Dict[str, Any]] = []
        stage2_results: List[Dict[str, Any]] = []
        final_result: Dict[str, Any] = {}

        # Shared context passed into stage templates.
        ctx: Dict[str, Any] = {
            "user_query": user_query,
            "council_prompt": self.metadata.prompt,
            "stage_context": "",
            # legacy / transitional keys (still useful for UI + debugging)
            "responses_text": "",
            "rankings_text": "",
            "stage1_text": "",
            "stage2_text": "",
        }

        if not self.stages:
            raise RuntimeError(f"Council '{self.id}' has no stages configured")

        def _parse_json_from_content(content: str) -> Dict[str, Any]:
            try:
                obj = json.loads(content)
                return obj if isinstance(obj, dict) else {}
            except Exception:
                return {}

        def _refresh_stage_context() -> None:
            parts: List[str] = []
            if ctx.get("stage1_text"):
                parts.append(f"## Stage 1 – Member Responses\n{ctx['stage1_text']}")
            if ctx.get("stage2_text"):
                parts.append(f"## Stage 2 – Peer Reviews\n{ctx['stage2_text']}")
            ctx["stage_context"] = "\n\n".join(parts).strip()

        for stage in self.stages:
            # Fanout stages run once per member
            if getattr(stage, "kind", "") == "fanout_members":
                tasks: List[asyncio.Task] = []
                rendered_prompts: List[str] = []

                for m in self.members:
                    addendum = m.metadata.prompt
                    ctx["persona_prompt"] = f"{m.persona.prompt}\n\n{addendum}" if addendum else m.persona.prompt

                    prompt = self.render_stage_prompt(stage.id, context=ctx)
                    rendered_prompts.append(prompt)

                    messages = build_messages_simple(prompt, system_text=ctx["persona_prompt"])
                    tasks.append(asyncio.create_task(client.query(m.model_id, messages, timeout=timeout_s)))

                results = await asyncio.gather(*tasks, return_exceptions=True)

                if stage.id == "delegate-prompt":
                    stage1_results = []
                    for m, r in zip(self.members, results):
                        if isinstance(r, BaseException):
                            logger.warning(
                                "Member query failed",
                                extra={"council": self.id, "stage": stage.id, "member": m.id, "error": str(r)[:200]},
                            )
                            continue
                        stage1_results.append(
                            {
                                "id": m.id,
                                "member_name": m.name,
                                "model_id": m.model_id,
                                "persona": m.persona.name,
                                "response": (r.get("content", "") if isinstance(r, dict) else ""),
                            }
                        )

                    ctx["responses_text"] = "\n\n".join(
                        [f"{r['member_name']}\n{r['response']}" for r in stage1_results]
                    )
                    ctx["stage1_text"] = _format_stage1_responses(stage1_results)

                else:
                    # Treat all other fanout stages as “peer review / critique” for now.
                    stage2_results = []
                    for m, r in zip(self.members, results):
                        if isinstance(r, BaseException):
                            logger.warning(
                                "Judge query failed",
                                extra={"council": self.id, "stage": stage.id, "member": m.id, "error": str(r)[:200]},
                            )
                            continue
                        stage2_results.append(
                            {
                                "id": m.id,
                                "member_name": m.name,
                                "model_id": m.model_id,
                                "persona": m.persona.name,
                                "ranking": (r.get("content", "") if isinstance(r, dict) else ""),
                            }
                        )

                    ctx["rankings_text"] = "\n\n".join(
                        [f"{r['member_name']}\n{r['ranking']}" for r in stage2_results]
                    )
                    ctx["stage2_text"] = _format_stage2_rankings(stage2_results)

                _refresh_stage_context()
                continue

            # Single stages run on the chairman
            addendum = self.chairman.metadata.prompt
            ctx["persona_prompt"] = (
                f"{self.chairman.persona.prompt}\n\n{addendum}" if addendum else self.chairman.persona.prompt
            )

            prompt = self.render_stage_prompt(stage.id, context=ctx)
            messages = build_messages_simple(prompt, system_text=ctx["persona_prompt"])
            r = await client.query(self.chairman.model_id, messages, timeout=timeout_s)
            content = (r.get("content", "") if isinstance(r, dict) else "")

            if stage.id == "improve-prompt":
                parsed = _parse_json_from_content(content)
                improved = parsed.get("improved_query")
                if isinstance(improved, str) and improved.strip():
                    ctx["improved_query"] = improved.strip()

            elif stage.id == "validate-council-applicability":
                parsed = _parse_json_from_content(content)
                ctx["council_applicability"] = parsed
                if isinstance(parsed, dict) and parsed.get("decision") == "STOP":
                    # Short-circuit: return a “final” response explaining why we stopped.
                    pretty = json.dumps(parsed, indent=2) if parsed else content
                    return {
                        "stage1": [],
                        "stage2": [],
                        "final": {
                            "response": pretty,
                        },
                    }

            elif stage.id == "synthesize-output":
                final_result = {
                    "id": self.chairman.id,
                    "member_name": self.chairman.name,
                    "model_id": self.chairman.model_id,
                    "persona": self.chairman.persona.name,
                    "response": content,
                }

            _refresh_stage_context()

        return {
            "stage1": stage1_results,
            "stage2": stage2_results,
            "final": final_result,
        }


    async def run_mock(self, user_query: str, *, timeout_s: float = 0.01) -> Dict[str, Any]:
        """Convenience: run with a local deterministic mock client."""
        return await self.run(user_query, client=MockModelClient(), timeout_s=timeout_s)




if __name__ == "__main__":

    council = Council.from_id("ai-council")
    print(f"Loaded council: {council.id} ({council.name})")
    print(f"Chairman: {council.chairman.id} ({council.chairman.name})")
    print(f"Members: {[m.id for m in council.members]}")
    print(f"Stages: {[s.id for s in council.stages]}")

    async def _smoke():
        out = await council.run_mock("Test question: what is 2+2?")
        print("Mock run final:")
        print(out.get("final", {}).get("response"))

    asyncio.run(_smoke())