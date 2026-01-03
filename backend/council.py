"""3-stage LLM Council orchestration."""

import asyncio
import uuid
from typing import List, Dict, Any, Tuple, Optional

# Wall-clock timeouts (seconds) for each stage fan-out.
# These prevent the council from being held hostage by a single slow/blocked provider.
STAGE1_WALL_TIMEOUT_S = 45.0
STAGE2_WALL_TIMEOUT_S = 60.0
STAGE3_WALL_TIMEOUT_S = 60.0

# Per-request timeout passed to OpenRouter (seconds)
MODEL_TIMEOUT_S = 120.0

from .openrouter import query_model
from .config import COUNCIL_MODELS, CHAIRMAN_MODEL
from .personas import build_messages, persona_for_stage

from .observability import log_event, set_run_id


# Helper functions for council prompts
def build_stage2_ranking_prompt(user_query: str, responses_text: str) -> str:
    """Build the Stage 2 prompt where each model critiques and ranks anonymized responses."""

    # NOTE: This prompt intentionally forces “second-order” and “embodied constraint” checks.
    # We want the council to notice asymmetric access (e.g., organizers vs attendees),
    # bio/waste handling, and other operational realities that frequently get missed.
    return f"""You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. Evaluate each response individually. For each response, explain what it does well and what it does poorly.
2. When evaluating quality, you MUST explicitly check for the following common failure modes (and call them out if missing):
   - Constraint asymmetry: who bears the consequences vs who keeps an escape hatch (e.g., decision-makers retaining access/resources the public does not).
   - Embodied constraints: biological/physical limits (bathrooms, water, heat/cold, fatigue, mobility, disability access) and dignity impacts.
   - Second-order logistics: what happens afterward (waste, cleanup, disposal, enforcement residue, transport, bottlenecks).
   - Incentives and perverse optimizations: density, optics, throughput, or control prioritized over human needs.
   - One missing operational detail: explicitly name one concrete logistical or operational detail the response fails to address (e.g., waste removal, staffing, enforcement load, cleanup timing, accessibility edge cases).
3. Then, at the very end of your response, provide a final ranking.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response A")
- Do not add any other text or explanations in the ranking section

Example of the correct format for your ENTIRE response:

Response A provides good detail on X but misses Y...
Response B is accurate but lacks depth on Z...
Response C offers the most comprehensive answer...

FINAL RANKING:
1. Response C
2. Response A
3. Response B

Now provide your evaluation and ranking:"""


def build_stage3_chairman_prompt(user_query: str, stage1_text: str, stage2_text: str) -> str:
    """Build the Stage 3 prompt where the Chairman synthesizes a final answer."""

    # NOTE: This prompt forces the synthesis to include operational reality checks.
    return f"""You are the Chairman of an LLM Council. Multiple AI models have provided responses to a user's question, and then ranked each other's responses.

Original Question: {user_query}

STAGE 1 - Individual Responses:
{stage1_text}

STAGE 2 - Peer Rankings:
{stage2_text}

Your task as Chairman is to synthesize all of this information into a single, comprehensive, accurate answer to the user's original question.

In your synthesis, you MUST:
- Preserve the best insights across the council, but also correct blind spots.
- Explicitly surface “constraint asymmetry” when present (e.g., organizers/officials retaining bathrooms, exits, water, warmth, or privileges that attendees do not).
- Include embodied constraints and dignity impacts (humans have bodies; plans must respect biology).
- Include second-order logistics (waste handling/disposal, cleanup, enforcement residue, downstream bottlenecks).
- If the discussion implies ad-hoc coping (e.g., diapers), explicitly address the operational consequences (biohazard collection, containment, and disposal) and why that signals a planning failure.

Provide a clear, well-reasoned final answer that represents the council's collective wisdom:"""

async def stage1_collect_responses(user_query: str, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Stage 1: Collect individual responses from all council models."""

    if run_id is None:
        run_id = f"run-{uuid.uuid4()}"
        set_run_id(run_id)
        log_event({
            "event": "council.run.generated",
            "run_id": run_id,
        })

    log_event({
        "event": "council.stage1.start",
        "run_id": run_id,
        "user_query_len": len(user_query or ""),
        "expected_count": len(COUNCIL_MODELS),
    })

    model_to_messages = {
        model: build_messages(user_query, persona=persona_for_stage(1, model))
        for model in COUNCIL_MODELS
    }

    # Fan out per-model calls with a wall-clock timeout so we can return partial results.
    tasks = {
        model: asyncio.create_task(
            query_model(model, msgs, timeout=MODEL_TIMEOUT_S, run_id=run_id)
        )
        for model, msgs in model_to_messages.items()
    }

    done, pending = await asyncio.wait(
        tasks.values(),
        timeout=STAGE1_WALL_TIMEOUT_S,
    )

    # Cancel any stragglers so they don't leak work in a long-lived server.
    pending_models = []
    for model, task in tasks.items():
        if task in pending:
            pending_models.append(model)
            task.cancel()

    if pending_models:
        log_event({
            "event": "council.stage1.timeout",
            "run_id": run_id,
            "wall_timeout_s": STAGE1_WALL_TIMEOUT_S,
            "pending_models": pending_models,
        })

    responses: Dict[str, Optional[Dict[str, Any]]] = {}
    for model, task in tasks.items():
        if task in done:
            try:
                responses[model] = task.result()
            except Exception as e:
                responses[model] = None
                log_event({
                    "event": "council.stage1.task_error",
                    "run_id": run_id,
                    "model": model,
                    "error": str(e)[:200],
                })
        else:
            responses[model] = None

    stage1_results: List[Dict[str, Any]] = []
    for model, response in responses.items():
        if response is not None:
            stage1_results.append({
                "model": model,
                "response": response.get("content", ""),
                "run_id": run_id,
            })

    log_event({
        "event": "council.stage1.done",
        "run_id": run_id,
        "ok_count": len(stage1_results),
        "expected_count": len(COUNCIL_MODELS),
    })

    return stage1_results


async def stage2_collect_rankings(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    run_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Stage 2: Each model ranks the anonymized responses."""

    if run_id is None and stage1_results:
        run_id = stage1_results[0].get("run_id")

    log_event({
        "event": "council.stage2.start",
        "run_id": run_id,
        "responses_count": len(stage1_results),
        "expected_count": len(COUNCIL_MODELS),
    })

    labels = [chr(65 + i) for i in range(len(stage1_results))]  # A, B, C, ...

    label_to_model = {
        f"Response {label}": result["model"]
        for label, result in zip(labels, stage1_results)
    }

    responses_text = "\n\n".join([
        f"Response {label}:\n{result['response']}"
        for label, result in zip(labels, stage1_results)
    ])

    ranking_prompt = build_stage2_ranking_prompt(user_query=user_query, responses_text=responses_text)

    model_to_messages = {
        model: build_messages(ranking_prompt, persona=persona_for_stage(2, model))
        for model in COUNCIL_MODELS
    }

    # Fan out per-model judge calls with a wall-clock timeout so we can return partial results.
    tasks = {
        model: asyncio.create_task(
            query_model(model, msgs, timeout=MODEL_TIMEOUT_S, run_id=run_id)
        )
        for model, msgs in model_to_messages.items()
    }

    done, pending = await asyncio.wait(
        tasks.values(),
        timeout=STAGE2_WALL_TIMEOUT_S,
    )

    pending_models = []
    for model, task in tasks.items():
        if task in pending:
            pending_models.append(model)
            task.cancel()

    if pending_models:
        log_event({
            "event": "council.stage2.timeout",
            "run_id": run_id,
            "wall_timeout_s": STAGE2_WALL_TIMEOUT_S,
            "pending_models": pending_models,
        })

    responses: Dict[str, Optional[Dict[str, Any]]] = {}
    for model, task in tasks.items():
        if task in done:
            try:
                responses[model] = task.result()
            except Exception as e:
                responses[model] = None
                log_event({
                    "event": "council.stage2.task_error",
                    "run_id": run_id,
                    "model": model,
                    "error": str(e)[:200],
                })
        else:
            responses[model] = None

    stage2_results: List[Dict[str, Any]] = []
    for model, response in responses.items():
        if response is not None:
            full_text = response.get("content", "")
            parsed = parse_ranking_from_text(full_text)
            stage2_results.append({
                "model": model,
                "ranking": full_text,
                "parsed_ranking": parsed,
                "run_id": run_id,
            })

    log_event({
        "event": "council.stage2.done",
        "run_id": run_id,
        "ok_count": len(stage2_results),
        "expected_count": len(COUNCIL_MODELS),
    })

    return stage2_results, label_to_model


async def stage3_synthesize_final(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Stage 3: Chairman synthesizes final response."""

    if run_id is None and stage1_results:
        run_id = stage1_results[0].get("run_id")

    log_event({
        "event": "council.stage3.start",
        "run_id": run_id,
        "stage1_count": len(stage1_results),
        "stage2_count": len(stage2_results),
        "chairman_model": CHAIRMAN_MODEL,
    })

    stage1_text = "\n\n".join([
        f"Model: {result['model']}\nResponse: {result['response']}"
        for result in stage1_results
    ])

    stage2_text = "\n\n".join([
        f"Model: {result['model']}\nRanking: {result['ranking']}"
        for result in stage2_results
    ])

    chairman_prompt = build_stage3_chairman_prompt(
        user_query=user_query,
        stage1_text=stage1_text,
        stage2_text=stage2_text,
    )

    messages = build_messages(chairman_prompt, persona=persona_for_stage(3, CHAIRMAN_MODEL))
    try:
        response = await asyncio.wait_for(
            query_model(CHAIRMAN_MODEL, messages, timeout=MODEL_TIMEOUT_S, run_id=run_id),
            timeout=STAGE3_WALL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log_event({
            "event": "council.stage3.timeout",
            "run_id": run_id,
            "wall_timeout_s": STAGE3_WALL_TIMEOUT_S,
            "chairman_model": CHAIRMAN_MODEL,
        })
        response = None

    if response is None:
        log_event({
            "event": "council.stage3.done",
            "run_id": run_id,
            "ok": False,
            "chairman_model": CHAIRMAN_MODEL,
        })
        return {
            "model": CHAIRMAN_MODEL,
            "response": "Error: Unable to generate final synthesis.",
        }

    log_event({
        "event": "council.stage3.done",
        "run_id": run_id,
        "ok": True,
        "chairman_model": CHAIRMAN_MODEL,
        "content_len": len(response.get("content") or ""),
    })

    return {
        "model": CHAIRMAN_MODEL,
        "response": response.get("content", ""),
    }


def parse_ranking_from_text(ranking_text: str) -> List[str]:
    """Parse the FINAL RANKING section from the model's response."""
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


def calculate_aggregate_rankings(
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str]
) -> List[Dict[str, Any]]:
    """Calculate aggregate rankings across all models."""
    from collections import defaultdict

    model_positions = defaultdict(list)

    for ranking in stage2_results:
        parsed_ranking = parse_ranking_from_text(ranking["ranking"])
        for position, label in enumerate(parsed_ranking, start=1):
            if label in label_to_model:
                model_name = label_to_model[label]
                model_positions[model_name].append(position)

    aggregate = []
    for model, positions in model_positions.items():
        if positions:
            avg_rank = sum(positions) / len(positions)
            aggregate.append({
                "model": model,
                "average_rank": round(avg_rank, 2),
                "rankings_count": len(positions),
            })

    aggregate.sort(key=lambda x: x["average_rank"])
    return aggregate


async def generate_conversation_title(user_query: str) -> str:
    """Generate a short title for a conversation based on the first user message."""

    title_prompt = f"""Generate a very short title (3-5 words maximum) that summarizes the following question.
The title should be concise and descriptive. Do not use quotes or punctuation in the title.

Question: {user_query}

Title:"""

    messages = build_messages(title_prompt, persona=persona_for_stage(1, "google/gemini-2.5-flash"))
    response = await query_model("google/gemini-2.5-flash", messages, timeout=30.0)

    if response is None:
        return "New Conversation"

    title = response.get("content", "New Conversation").strip().strip('"\'')
    if len(title) > 50:
        title = title[:47] + "..."
    return title


async def run_full_council(user_query: str) -> Tuple[List, List, Dict, Dict]:
    """Run the complete 3-stage council process."""

    run_id = f"run-{uuid.uuid4()}"
    set_run_id(run_id)
    log_event({
        "event": "council.run.start",
        "run_id": run_id,
        "user_query_len": len(user_query or ""),
        "council_models": COUNCIL_MODELS,
        "chairman_model": CHAIRMAN_MODEL,
    })

    stage1_results = await stage1_collect_responses(user_query, run_id=run_id)

    if not stage1_results:
        return [], [], {
            "model": "error",
            "response": "All models failed to respond. Please try again.",
        }, { "run_id": run_id }

    stage2_results, label_to_model = await stage2_collect_rankings(user_query, stage1_results, run_id=run_id)
    aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)

    stage3_result = await stage3_synthesize_final(
        user_query,
        stage1_results,
        stage2_results,
        run_id=run_id,
    )

    metadata = {
        "label_to_model": label_to_model,
        "aggregate_rankings": aggregate_rankings,
        "run_id": run_id,
    }

    log_event({
        "event": "council.run.done",
        "run_id": run_id,
        "stage1_count": len(stage1_results),
        "stage2_count": len(stage2_results),
        "aggregate_count": len(aggregate_rankings),
        "final_len": len(stage3_result.get("response") or ""),
    })

    # Clear ambient context for safety (e.g., in long-lived server processes)
    set_run_id(None)
    return stage1_results, stage2_results, stage3_result, metadata