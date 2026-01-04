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
from .council import run_full_council, generate_conversation_title, stage1_collect_responses, stage2_collect_rankings, stage3_synthesize_final, calculate_aggregate_rankings

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

    # Run the 3-stage council process
    stage1_results, stage2_results, stage3_result, metadata = await _retry_once_on_transient(
        lambda: run_full_council(request.content),
        context={
            "conversation_id": conversation_id,
            "path": "/api/conversations/{conversation_id}/message",
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
    return {
        "stage1": stage1_results,
        "stage2": stage2_results,
        "stage3": stage3_result,
        "metadata": metadata
    }


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
            yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"
            await _raise_if_disconnected(http_request)
            stage1_results = await _retry_once_on_transient(
                lambda: stage1_collect_responses(request.content),
                context={
                    "conversation_id": conversation_id,
                    "stage": 1,
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

            yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"

            # Stage 2: Collect rankings
            yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
            await _raise_if_disconnected(http_request)
            stage2_results, label_to_model = await _retry_once_on_transient(
                lambda: stage2_collect_rankings(request.content, stage1_results),
                context={
                    "conversation_id": conversation_id,
                    "stage": 2,
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
            yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': {'label_to_model': label_to_model, 'aggregate_rankings': aggregate_rankings}})}\n\n"

            # Stage 3: Synthesize final answer
            yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
            await _raise_if_disconnected(http_request)
            stage3_result = await _retry_once_on_transient(
                lambda: stage3_synthesize_final(request.content, stage1_results, stage2_results),
                context={
                    "conversation_id": conversation_id,
                    "stage": 3,
                },
            )
            await _raise_if_disconnected(http_request)
            log_event({
                "event": "api.stream.stage3.done",
                "run_id": run_id,
                "conversation_id": conversation_id,
                "final_len": len((stage3_result or {}).get("response") or ""),
            })
            yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"

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
