"""OpenRouter API client for making LLM requests."""

import httpx
import asyncio
from typing import List, Dict, Any, Optional
from .config import OPENROUTER_API_KEY, OPENROUTER_API_URL


async def query_model(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0
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

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                OPENROUTER_API_URL,
                headers=headers,
                json=payload
            )
            response.raise_for_status()

            data = response.json()
            message = data['choices'][0]['message']

            return {
                'content': message.get('content'),
                'reasoning_details': message.get('reasoning_details')
            }

    except Exception as e:
        print(f"Error querying model {model}: {e}")
        return None


async def query_models_parallel(
    models: List[str],
    messages: List[Dict[str, str]]
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
    tasks = [query_model(model, messages) for model in models]

    # Wait for all to complete
    responses = await asyncio.gather(*tasks)

    # Map models to their responses
    return {model: response for model, response in zip(models, responses)}


# New function: query_models_parallel_per_model
async def query_models_parallel_per_model(
    model_to_messages: Dict[str, List[Dict[str, str]]],
    timeout: float = 120.0
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
            return model, await query_model(model, messages, timeout=timeout)
        except Exception as e:
            print(f"Error querying model {model}: {e}")
            return model, None

    tasks = [
        _call(model, messages)
        for model, messages in model_to_messages.items()
    ]

    results = await asyncio.gather(*tasks)
    return {model: response for model, response in results}
