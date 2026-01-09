"""
Personas (system prompts) for the LLM Council.

Design goals:
- Keep personas data-driven and easy to tweak.
- Support stage-specific behavior (Stage 1 / Stage 2 / Stage 3).
- Allow optional per-model overrides without changing orchestration code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Iterable


@dataclass(frozen=True)
class Persona:
    """A reusable system prompt persona."""
    name: str
    system_prompt: str


# ----------------------------
# Default personas (stage-level)
# ----------------------------

DEFAULT_STAGE1_MEMBER = Persona(
    name="DefaultStage1Member",
    system_prompt=(
        "You are a council member in a multi-model deliberation system.\n"
        "Goal: answer the user's question directly, accurately, and usefully.\n"
        "Constraints:\n"
        "- Be honest about uncertainty; do not invent facts.\n"
        "- Prefer clear structure and concrete reasoning.\n"
        "- If the question could benefit from a checklist, give one.\n"
        "- Keep the answer compact unless the user asks for depth.\n"
    ),
)

DEFAULT_STAGE2_JUDGE = Persona(
    name="DefaultStage2Judge",
    system_prompt=(
        "You are a judge in an LLM Council. Your job is to evaluate responses.\n"
        "Goal: fairly assess correctness, completeness, clarity, and helpfulness.\n"
        "Constraints:\n"
        "- Be consistent: use the same standards across responses.\n"
        "- Penalize hallucinations, vagueness, and missing caveats.\n"
        "- Reward concrete reasoning and actionable guidance.\n"
        "- Follow the required ranking format exactly.\n"
    ),
)

DEFAULT_CHAIRMAN = Persona(
    name="DefaultChairman",
    system_prompt=(
        "You are the Chairman of an LLM Council.\n"
        "Goal: synthesize the best final answer using the council's work.\n"
        "Constraints:\n"
        "- Prefer the most verifiable, least speculative claims.\n"
        "- Resolve disagreements by explaining tradeoffs or noting uncertainty.\n"
        "- Output a single cohesive answer; do not mention internal stages unless asked.\n"
        "- Keep it clear, structured, and oriented to the user's intent.\n"
    ),
)

# Backward compatibility aliases (older code may import these names)
STAGE1_MEMBER = DEFAULT_STAGE1_MEMBER
STAGE2_JUDGE = DEFAULT_STAGE2_JUDGE
CHAIRMAN = DEFAULT_CHAIRMAN


# ----------------------------
# Optional per-model overrides
# ----------------------------
# If you later want a specific model to play a specific "character" (e.g., skeptic, teacher),
# you can add it here without touching council.py.
#
# Example:
# MODEL_PERSONAS_STAGE1["openai/gpt-5.2"] = Persona(...)

MODEL_PERSONAS_STAGE1: Dict[str, Persona] = {}
MODEL_PERSONAS_STAGE2: Dict[str, Persona] = {}
MODEL_PERSONAS_STAGE3: Dict[str, Persona] = {}  # Chairman overrides (rare)


# ----------------------------
# Persona registry (name -> Persona)
# ----------------------------
# Council members should reference personas by unique name.
# Models are implementation details; personas are the UX-facing identity.

PERSONAS: Dict[str, Persona] = {
    # Canonical default persona names (explicit)
    DEFAULT_STAGE1_MEMBER.name: DEFAULT_STAGE1_MEMBER,
    DEFAULT_STAGE2_JUDGE.name: DEFAULT_STAGE2_JUDGE,
    DEFAULT_CHAIRMAN.name: DEFAULT_CHAIRMAN,

    # Backward-compatible aliases often used in config / older code
    "Stage1Member": DEFAULT_STAGE1_MEMBER,
    "Stage2Judge": DEFAULT_STAGE2_JUDGE,
    "Chairman": DEFAULT_CHAIRMAN,
}


def register_personas(personas: Iterable[Persona]) -> None:
    """Register additional personas by unique name."""
    for p in personas:
        PERSONAS[p.name] = p


def get_persona(name: str, default: Optional[Persona] = None) -> Persona:
    """Lookup a persona by name. If missing, return default or raise KeyError."""
    if not name:
        if default is not None:
            return default
        raise KeyError("Persona name is empty")

    p = PERSONAS.get(name)
    if p is not None:
        return p

    if default is not None:
        return default

    raise KeyError(f"Unknown persona: {name}")


# ----------------------------
# Message builders
# ----------------------------

def build_messages(
    user_content: str,
    *,
    persona: Persona,
    extra_system: Optional[str] = None,
) -> List[Dict[str, str]]:
    """
    Build OpenAI-style chat messages with a system persona and a single user message.

    extra_system: optional additional system text appended after persona prompt
                  (useful for per-request constraints).
    """
    sys = persona.system_prompt
    if extra_system:
        sys = f"{sys}\n\n{extra_system}".strip()

    return [
        {"role": "system", "content": sys},
        {"role": "user", "content": user_content},
    ]


def persona_for_member(persona_name: str, *, fallback_stage: int = 1) -> Persona:
    """Resolve a member's persona by unique name, with a stage-based fallback."""
    try:
        return get_persona(persona_name)
    except KeyError:
        return persona_for_stage(fallback_stage)


def persona_for_stage(stage: int, model: Optional[str] = None) -> Persona:
    """
    stage:
      1 = member response
      2 = judge/ranker
      3 = chairman synthesis
    """
    if stage == 1:
        if model and model in MODEL_PERSONAS_STAGE1:
            return MODEL_PERSONAS_STAGE1[model]
        return DEFAULT_STAGE1_MEMBER

    if stage == 2:
        if model and model in MODEL_PERSONAS_STAGE2:
            return MODEL_PERSONAS_STAGE2[model]
        return DEFAULT_STAGE2_JUDGE

    if stage == 3:
        if model and model in MODEL_PERSONAS_STAGE3:
            return MODEL_PERSONAS_STAGE3[model]
        return DEFAULT_CHAIRMAN

    raise ValueError(f"Unknown stage: {stage}")