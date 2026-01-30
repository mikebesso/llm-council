"""FastAPI backend for LLM Council."""

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Any
import uuid
import json
import asyncio

# --- Resilience helpers -----------------------------------------------------
# NOTE: We intentionally keep these helpers lightweight and dependency-free.
# They rely on common exception shapes (httpx, aiohttp, FastAPI HTTPException)
# rather than importing provider-specific SDK types.

# TODO (future): UI diagnostics for streaming stages
#  - stage*_pending_models
#  - stage2_judges_completed_count
#  - wall_timeouts_used
#  - Expose in SSE payloads so UI can explain partial completion
#    (e.g., "8/12 judges responded before deadline; proceeding.")

TRANSIENT_RETRY_STATUS_CODES = {429, 502, 503}
NO_RETRY_STATUS_CODES = {400, 401, 403, 402}


def _extract_status_code(exc: Exception) -> int | None:
    """Best-effort extraction of an HTTP-ish status code from an exception."""
    # FastAPI / Starlette
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code

    # httpx.HTTPStatusError: exc.response.status_code
    resp = getattr(exc, "response", None)
    if resp is not None:
        code = getattr(resp, "status_code", None)
        if isinstance(code, int):
            return code

    # aiohttp.ClientResponseError: exc.status
    code = getattr(exc, "status", None)
    if isinstance(code, int):
        return code

    return None


async def _retry_once_on_transient(fn, *, context: dict | None = None):
    """Retry once on transient upstream/proxy/provider hiccups."""
    ctx = context or {}
    try:
        return await fn()
    except Exception as e:
        status = _extract_status_code(e)

        # Never retry on clear caller/auth/credits issues.
        if status in NO_RETRY_STATUS_CODES:
            raise

        if status in TRANSIENT_RETRY_STATUS_CODES:
            log_event({
                "event": "api.retry.transient",
                "status_code": status,
                "error": str(e)[:500],
                **ctx,
            })
            # Yield control so cancellations propagate cleanly.
            await asyncio.sleep(0)
            return await fn()

        # Unknown errors: don't blindly retry.
        raise


async def _raise_if_disconnected(request: Request):
    """Raise CancelledError if the client has disconnected."""
    try:
        if await request.is_disconnected():
            raise asyncio.CancelledError()
    except AttributeError:
        # Older/alternate Request implementations may not support is_disconnected.
        # In those cases, we fall back to normal cancellation semantics.
        return


def _sanitize_for_conversation(payload):
    """Remove provider model identifiers from conversation-facing payloads.
    Personas/member names are allowed; model ids belong in appendix/debug views."""
    # TODO (future): Rename persona.name to persona.role (internal) and keep it out of the
    # conversation UX. Personas select behavior; members are the only human-facing identity.
    if isinstance(payload, list):
        return [_sanitize_for_conversation(p) for p in payload]

    if isinstance(payload, dict):
        cleaned = {}
        for k, v in payload.items():
            # Strip internal identifiers from conversation UX
            if k in {"model_id", "chairman_model_id", "persona", "chairman_persona"}:
                continue
            cleaned[k] = _sanitize_for_conversation(v)
        return cleaned

    return payload

# TODO (future): Provide a separate appendix/debug endpoint that exposes model ids
# and provider details for transparency without polluting the conversation UX.

# TODO (future): Model cooldown circuit breaker
#   - Track per-model failures/timeouts in-memory across a sliding window.
#   - If a model fails 3 times, skip it for 10 minutes.
#   - Emit: council.model.cooldown
#
# TODO (future): UI-facing diagnostics in SSE payloads
#   - stage*_pending_models, stage2_judges_completed_count, wall_timeouts used, etc.
#   - Goal: UI can say "8/12 judges responded before deadline; proceeding."

# ---------------------------------------------------------------------------

from .observability import log_event, set_run_id

from . import storage
from .council import (
    run_full_council,
    generate_conversation_title,
    stage1_collect_responses,
    stage2_collect_rankings,
    stage3_synthesize_final,
    calculate_aggregate_rankings,
)

app = FastAPI(title="LLM Council API")

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateConversationRequest(BaseModel):
    """Request to create a new conversation."""
    pass


class SendMessageRequest(BaseModel):
    """Request to send a message in a conversation."""
    content: str

    # MVP: prompt-runner controls (optional; defaults apply when omitted)
    council: str | None = None
    stages: List[int] | None = None


class RunPromptRequest(BaseModel):
    """Request to run a standalone prompt (no conversation persistence)."""
    content: str
    council: str | None = None
    stages: List[int] | None = None
    prompt_id: str | None = None
    title: str | None = None

class ConversationMetadata(BaseModel):
    """Conversation metadata for list view."""
    id: str
    created_at: str
    title: str
    message_count: int


class Conversation(BaseModel):
    """Full conversation with all messages."""
    id: str
    created_at: str
    title: str
    messages: List[Dict[str, Any]]


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "LLM Council API"}


@app.get("/api/conversations", response_model=List[ConversationMetadata])
async def list_conversations():
    """List all conversations (metadata only)."""
    return storage.list_conversations()


@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(request: CreateConversationRequest):
    """Create a new conversation."""
    conversation_id = str(uuid.uuid4())
    conversation = storage.create_conversation(conversation_id)
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(conversation_id: str):
    """Get a specific conversation with all its messages."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.post("/api/conversations/{conversation_id}/message")
async def send_message(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and run the 3-stage council process.
    Returns the complete response with all stages.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    # Add user message
    storage.add_user_message(conversation_id, request.content)

    # If this is the first message, generate a title
    if is_first_message:
        title = await generate_conversation_title(request.content)
        storage.update_conversation_title(conversation_id, title)

    stages = _normalize_stages(request.stages)

    async def _run_selected_stages():
        # Run the full council once using the selected council id.
        # For now, even if the caller requests a subset of stages, we run all 3
        # and then omit unrequested results in the response.
        council_id = request.council or "ai-council"
        stage1_results, stage2_results, stage3_result, council_meta = await run_full_council(
            request.content,
            council_id=council_id,
        )

        # Respect requested stages by omitting unrequested outputs.
        if 1 not in stages:
            stage1_results = None
        if 2 not in stages:
            stage2_results = None
            council_meta = {**(council_meta or {}), "label_to_model": None, "aggregate_rankings": None}
        if 3 not in stages:
            stage3_result = None

        metadata = {
            "execution": _build_execution_metadata(council=request.council, stages=stages),
            "label_to_model": (council_meta or {}).get("label_to_model"),
            "aggregate_rankings": (council_meta or {}).get("aggregate_rankings"),
            "run_id": (council_meta or {}).get("run_id"),
            "council_id": (council_meta or {}).get("council_id") or council_id,
        }
        return stage1_results, stage2_results, stage3_result, metadata

    stage1_results, stage2_results, stage3_result, metadata = await _retry_once_on_transient(
        _run_selected_stages,
        context={
            "conversation_id": conversation_id,
            "path": "/api/conversations/{conversation_id}/message",
            "requested_council": request.council,
            "requested_stages": stages,
        },
    )   

    # Add assistant message with all stages
    storage.add_assistant_message(
        conversation_id,
        stage1_results,
        stage2_results,
        stage3_result
    )

    # Return the complete response with metadata
    return _sanitize_for_conversation({
        "stage1": stage1_results,
        "stage2": stage2_results,
        "stage3": stage3_result,
        "metadata": metadata
    })

def _normalize_stages(stages: List[int] | None) -> List[int]:
    """Normalize stages to a sorted list from {1,2,3}. Defaults to [1,2,3]."""
    if not stages:
        return [1, 2, 3]

    try:
        normalized = sorted(set(int(s) for s in stages))
    except Exception:
        raise HTTPException(status_code=400, detail="stages must be a list of integers")

    allowed = {1, 2, 3}
    if any(s not in allowed for s in normalized):
        raise HTTPException(status_code=400, detail="stages must be a subset of [1,2,3]")

    # Enforce dependencies: stage2 requires stage1; stage3 requires stage1+stage2
    if 2 in normalized and 1 not in normalized:
        raise HTTPException(status_code=400, detail="stage2 requires stage1")
    if 3 in normalized and (1 not in normalized or 2 not in normalized):
        raise HTTPException(status_code=400, detail="stage3 requires stages 1 and 2")

    return normalized


def _build_execution_metadata(*, council: str | None, stages: List[int]) -> dict:
    """MVP metadata describing how the run was executed."""
    return {
        "requested_council": council,
        "requested_stages": stages,
        # MVP: council selection is recorded but not yet plumbed into the runner.
        "council_applied": None,
        "stages_executed": stages,
    }


@app.post("/api/prompts/run")
async def run_prompt(request: RunPromptRequest):
    """Run a standalone prompt and persist the result like a conversation."""
    stages = _normalize_stages(request.stages)

    # Use provided prompt_id as the conversation id when available; otherwise generate one.
    conversation_id = request.prompt_id or str(uuid.uuid4())

    # Ensure a conversation file exists.
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        conversation = storage.create_conversation(conversation_id)

    # Detect first message (for title generation)
    is_first_message = len(conversation.get("messages", [])) == 0

    # Add user message
    storage.add_user_message(conversation_id, request.content)

    # Title handling: prefer explicit title; else generate on first message.
    if request.title:
        storage.update_conversation_title(conversation_id, request.title)
    elif is_first_message:
        title = await generate_conversation_title(request.content)
        storage.update_conversation_title(conversation_id, title)

    async def _run_selected_stages():
        council_id = request.council or "ai-council"
        stage1_results, stage2_results, stage3_result, council_meta = await run_full_council(
            request.content,
            council_id=council_id,
        )

        if 1 not in stages:
            stage1_results = None
        if 2 not in stages:
            stage2_results = None
            council_meta = {**(council_meta or {}), "label_to_model": None, "aggregate_rankings": None}
        if 3 not in stages:
            stage3_result = None

        metadata = {
            "execution": _build_execution_metadata(council=request.council, stages=stages),
            "prompt_id": request.prompt_id,
            "title": request.title,
            "label_to_model": (council_meta or {}).get("label_to_model"),
            "aggregate_rankings": (council_meta or {}).get("aggregate_rankings"),
            "run_id": (council_meta or {}).get("run_id"),
            "council_id": (council_meta or {}).get("council_id") or council_id,
        }

        return stage1_results, stage2_results, stage3_result, metadata

    stage1_results, stage2_results, stage3_result, metadata = await _retry_once_on_transient(
        _run_selected_stages,
        context={
            "conversation_id": conversation_id,
            "path": "/api/prompts/run",
            "prompt_id": request.prompt_id,
            "requested_council": request.council,
            "requested_stages": stages,
        },
    )

    # Persist assistant message with all stages (same as conversation endpoint)
    storage.add_assistant_message(
        conversation_id,
        stage1_results,
        stage2_results,
        stage3_result,
    )

    return _sanitize_for_conversation({
        "conversation_id": conversation_id,
        "stage1": stage1_results,
        "stage2": stage2_results,
        "stage3": stage3_result,
        "metadata": metadata,
    })


@app.post("/api/conversations/{conversation_id}/message/stream")
async def send_message_stream(conversation_id: str, request: SendMessageRequest, http_request: Request):
    """
    Send a message and stream the 3-stage council process.
    Returns Server-Sent Events as each stage completes.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    stages = _normalize_stages(request.stages)

    async def event_generator():
        run_id = None
        try:
            # Stream lifecycle observability
            run_id = None
            log_event({
                "event": "api.stream.start",
                "run_id": run_id,
                "conversation_id": conversation_id,
                "is_first_message": is_first_message,
                "user_query_len": len(request.content or ""),
            })

            await _raise_if_disconnected(http_request)

            # Add user message
            storage.add_user_message(conversation_id, request.content)

            await _raise_if_disconnected(http_request)

            # Start title generation in parallel (don't await yet)
            title_task = None
            if is_first_message:
                title_task = asyncio.create_task(generate_conversation_title(request.content))

            # Stage 1: Collect responses
            stage1_results = None
            if 1 in stages:
                yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"
                await _raise_if_disconnected(http_request)
                stage1_results = await _retry_once_on_transient(
                    lambda: stage1_collect_responses(request.content),
                    context={
                        "conversation_id": conversation_id,
                        "stage": 1,
                        "requested_council": request.council,
                        "requested_stages": stages,
                    },
                )
                await _raise_if_disconnected(http_request)

                # Derive run_id from stage1 results (stage1 attaches run_id to each result)
                try:
                    if stage1_results and isinstance(stage1_results, list):
                        run_id = stage1_results[0].get("run_id")
                except Exception:
                    run_id = run_id

                if run_id:
                    set_run_id(run_id)

                log_event({
                    "event": "api.stream.stage1.done",
                    "run_id": run_id,
                    "conversation_id": conversation_id,
                    "stage1_count": len(stage1_results) if stage1_results else 0,
                })

                yield f"data: {json.dumps({'type': 'stage1_complete', 'data': _sanitize_for_conversation(stage1_results)})}\n\n"
                
            # Stage 2: Collect rankings
            stage2_results = None
            label_to_model = None
            aggregate_rankings = None
            if 2 in stages:
                yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
                await _raise_if_disconnected(http_request)
                stage2_results, label_to_model = await _retry_once_on_transient(
                    lambda: stage2_collect_rankings(request.content, stage1_results),
                    context={
                        "conversation_id": conversation_id,
                        "stage": 2,
                        "requested_council": request.council,
                        "requested_stages": stages,
                    },
                )
                await _raise_if_disconnected(http_request)
                aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
                log_event({
                    "event": "api.stream.stage2.done",
                    "run_id": run_id,
                    "conversation_id": conversation_id,
                    "stage2_count": len(stage2_results) if stage2_results else 0,
                    "aggregate_count": len(aggregate_rankings) if aggregate_rankings else 0,
                })
                yield f"data: {json.dumps({'type': 'stage2_complete', 'data': _sanitize_for_conversation(stage2_results), 'metadata': _sanitize_for_conversation({'label_to_model': label_to_model, 'aggregate_rankings': aggregate_rankings})})}\n\n"
                
            # Stage 3: Synthesize final answer
            stage3_result = None
            if 3 in stages:
                yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
                await _raise_if_disconnected(http_request)
                stage3_result = await _retry_once_on_transient(
                    lambda: stage3_synthesize_final(request.content, stage1_results, stage2_results),
                    context={
                        "conversation_id": conversation_id,
                        "stage": 3,
                        "requested_council": request.council,
                        "requested_stages": stages,
                    },
                )
                await _raise_if_disconnected(http_request)
                log_event({
                    "event": "api.stream.stage3.done",
                    "run_id": run_id,
                    "conversation_id": conversation_id,
                    "final_len": len((stage3_result or {}).get("response") or ""),
                })
                yield f"data: {json.dumps({'type': 'stage3_complete', 'data': _sanitize_for_conversation(stage3_result)})}\n\n"
                
                yield f"data: {json.dumps({'type': 'execution', 'metadata': _sanitize_for_conversation({'execution': _build_execution_metadata(council=request.council, stages=stages)})})}\n\n"

            # Wait for title generation if it was started
            if title_task:
                await _raise_if_disconnected(http_request)
                title = await title_task
                storage.update_conversation_title(conversation_id, title)
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

            # Save complete assistant message
            storage.add_assistant_message(
                conversation_id,
                stage1_results,
                stage2_results,
                stage3_result
            )

            # Send completion event
            log_event({
                "event": "api.stream.final_yield",
                "run_id": run_id,
                "conversation_id": conversation_id,
            })
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"

        except asyncio.CancelledError:
            # Can be triggered by client disconnect or explicit cancellation checks.
            log_event({
                "event": "api.stream.cancelled",
                "run_id": run_id,
                "conversation_id": conversation_id,
            })
            raise

        except Exception as e:
            status = _extract_status_code(e)
            if status == 402:
                # Surface a clear UI message. No retry.
                yield f"data: {json.dumps({'type': 'error', 'code': 402, 'message': 'Upstream provider credits exhausted (402). Please check billing/credits or switch models.'})}\n\n"
                return
            log_event({
                "event": "api.stream.error",
                "run_id": run_id,
                "conversation_id": conversation_id,
                "error": str(e)[:500],
            })
            # Send error event
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        finally:
            log_event({
                "event": "api.stream.close",
                "run_id": run_id,
                "conversation_id": conversation_id,
            })

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
