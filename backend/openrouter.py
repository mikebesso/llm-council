"""OpenRouter API client for making LLM requests."""

import httpx
import asyncio
from typing import List, Dict, Any, Optional
from fastapi import HTTPException
from time import perf_counter
from httpx import Timeout
from .config import OPENROUTER_API_KEY, OPENROUTER_API_URL
from .observability import log_event

TRANSIENT_RETRY_STATUS_CODES = {429, 502, 503}
NO_RETRY_STATUS_CODES = {400, 401, 403, 402}

def _log_http_status_error(*, response: httpx.Response, model: str, run_id: Optional[str], t0: float, err: Exception):
    _ms = int((perf_counter() - t0) * 1000)
    body = ""
    try:
        body = response.text or ""
    except Exception:
        body = ""

    log_event({
        "event": "openrouter.query_model",
        "model": model,
        "ok": False,
        "ms": _ms,
        "status": response.status_code,
        "error": str(err)[:200],
        "body": body[:400],
        "run_id": run_id,
    })

    print(f"Error querying model {model}: HTTP {response.status_code} {body[:400]}")

async def query_model(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0,
    run_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Query a single model via OpenRouter API.

    Args:
        model: OpenRouter model identifier (e.g., "openai/gpt-4o")
        messages: List of message dicts with 'role' and 'content'
        timeout: Request timeout in seconds

    Returns:
        Response dict with 'content' and optional 'reasoning_details', or None if failed
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
    }

    _t0 = perf_counter()

    try:
        httpx_timeout = Timeout(timeout, connect=10.0, read=timeout, write=10.0, pool=10.0)
        async with httpx.AsyncClient(timeout=httpx_timeout) as client:
            log_event({
                "event": "openrouter.query_model.start",
                "run_id": run_id,
                "model": model,
                "msg_count": len(messages) if messages is not None else None,
            })

            # Retry once on transient upstream/proxy/provider hiccups.
            attempt = 0
            while True:
                attempt += 1
                try:
                    response = await client.post(
                        OPENROUTER_API_URL,
                        headers=headers,
                        json=payload
                    )
                    response.raise_for_status()
                    break

                except asyncio.CancelledError:
                    # Cancellation should propagate so upstream can stop doing work.
                    _ms = int((perf_counter() - _t0) * 1000)
                    log_event({
                        "event": "openrouter.query_model.cancelled",
                        "model": model,
                        "ok": False,
                        "ms": _ms,
                        "run_id": run_id,
                    })
                    raise

                except httpx.HTTPStatusError as e:
                    status = getattr(e.response, "status_code", None) or getattr(response, "status_code", None)

                    # Credits exhausted: surface clearly (no retry).
                    if status == 402:
                        _log_http_status_error(response=response, model=model, run_id=run_id, t0=_t0, err=e)
                        raise HTTPException(
                            status_code=402,
                            detail="Upstream provider credits exhausted (402). Please check billing/credits or switch models.",
                        )

                    # Never retry on caller/auth issues.
                    if status in {400, 401, 403}:
                        _log_http_status_error(response=response, model=model, run_id=run_id, t0=_t0, err=e)
                        return None

                    # Retry once on transient codes.
                    if status in TRANSIENT_RETRY_STATUS_CODES and attempt == 1:
                        _log_http_status_error(response=response, model=model, run_id=run_id, t0=_t0, err=e)
                        log_event({
                            "event": "openrouter.query_model.retry.transient",
                            "model": model,
                            "status": status,
                            "run_id": run_id,
                        })
                        # Yield control so cancellation can cut in promptly.
                        await asyncio.sleep(0)
                        continue

                    _log_http_status_error(response=response, model=model, run_id=run_id, t0=_t0, err=e)
                    return None

            data = response.json()
            message = data['choices'][0]['message']

            _ms = int((perf_counter() - _t0) * 1000)
            try:
                msg_count = len(messages)
                msg_chars = sum(len(m.get("content", "") or "") for m in messages)
            except Exception:
                msg_count = None
                msg_chars = None

            log_event({
                "event": "openrouter.query_model",
                "model": model,
                "ok": True,
                "ms": _ms,
                "msg_count": msg_count,
                "msg_chars": msg_chars,
                "content_len": len(message.get('content') or ""),
                "run_id": run_id,
            })

            return {
                'content': message.get('content'),
                'reasoning_details': message.get('reasoning_details')
            }

    except asyncio.CancelledError:
        # Allow upstream cancellation to propagate.
        raise

    except Exception as e:
        _ms = int((perf_counter() - _t0) * 1000)
        log_event({
            "event": "openrouter.query_model",
            "model": model,
            "ok": False,
            "ms": _ms,
            "error": str(e)[:200],
            "run_id": run_id,
        })
        print(f"Error querying model {model}: {e}")
        return None


async def query_models_parallel(
    models: List[str],
    messages: List[Dict[str, str]],
    timeout: float = 120.0,
    run_id: Optional[str] = None,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Query multiple models in parallel.

    Args:
        models: List of OpenRouter model identifiers
        messages: List of message dicts to send to each model

    Returns:
        Dict mapping model identifier to response dict (or None if failed)
    """
    import asyncio

    # Create tasks for all models
    tasks = [query_model(model, messages, timeout=timeout, run_id=run_id) for model in models]

    # Wait for all to complete
    responses = await asyncio.gather(*tasks)

    # Map models to their responses
    return {model: response for model, response in zip(models, responses)}


# New function: query_models_parallel_per_model
async def query_models_parallel_per_model(
    model_to_messages: Dict[str, List[Dict[str, str]]],
    timeout: float = 120.0,
    run_id: Optional[str] = None,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Query multiple models in parallel, allowing different messages per model.

    Args:
        model_to_messages: Mapping of model identifier -> messages list
        timeout: Request timeout in seconds

    Returns:
        Dict mapping model identifier to response dict (or None if failed)
    """

    async def _call(model: str, messages: List[Dict[str, str]]):
        try:
            return model, await query_model(model, messages, timeout=timeout, run_id=run_id)
        except Exception as e:
            print(f"Error querying model {model}: {e}")
            return model, None

    tasks = [
        _call(model, messages)
        for model, messages in model_to_messages.items()
    ]

    results = await asyncio.gather(*tasks)
    return {model: response for model, response in results}
