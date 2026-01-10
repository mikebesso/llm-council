"""Configuration for the LLM Council."""

import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _require_env_var(name: str) -> str:
    """Return required env var value or raise a clear startup error."""
    value = os.getenv(name)
    if value:
        return value

    msg = (
        f"CONFIG ERROR: Required environment variable {name} is not set. "
        "The backend cannot start without it. "
        "Set it in your shell environment or add it to the .env file in the project root."
    )

    # Log once, then fail fast.
    logger.error(msg)
    raise RuntimeError(msg)

# OpenRouter API key
OPENROUTER_API_KEY = _require_env_var("OPENROUTER_API_KEY")

# Council members - explicit members with a model + persona.
# Personas are uniquely named (even if prompts are similar) so councils can be mixed and reused.
# An optional per-member persona addendum can be provided, which will be appended to the base persona prompt when present.
# NOTE: For now, we keep backward compatibility by deriving COUNCIL_MODELS from COUNCIL_MEMBERS.
COUNCIL_MEMBERS = [
    {
        "name": "Atlas",
        "model_id": "openai/gpt-5.1",
        "persona": "Stage1Member",
        "persona_addendum": None,
    },
    {
        "name": "Kestrel",
        "model_id": "openai/gpt-4o",
        "persona": "Stage1Member",
        "persona_addendum": None,
    },
    {
        "name": "Minerva",
        "model_id": "openai/gpt-4.1-mini",
        "persona": "Stage1Member",
        "persona_addendum": None,
    },
    # {
    #     "name": "Orchid",
    #     "model_id": "google/gemini-3-pro-preview",
    #     "persona": "Stage1Member",
    #     #     "persona_addendum": None,
    # },
    # {
    #     "name": "Spark",
    #     "model_id": "google/gemini-3-flash-preview",
    #     "persona": "Stage1Member",
    #     #     "persona_addendum": None,
    # },
    # {
    #     "name": "Solomon",
    #     "model_id": "anthropic/claude-3.5-sonnet",
    #     "persona": "Stage1Member",
    #     #     "persona_addendum": None,
    # },
    # {
    #     "name": "Sable",
    #     "model_id": "anthropic/claude-sonnet-4.5",
    #     "persona": "Stage1Member",
    #     #     "persona_addendum": None,
    # },
    # {
    #     "name": "Grok",
    #     "model_id": "x-ai/grok-4",
    #     "persona": "Stage1Member",
    #     #     "persona_addendum": None,
    # },
    # {
    #     "name": "Qwen",
    #     "model_id": "qwen/qwen-2.5-72b-instruct",
    #     "persona": "Stage1Member",
    #     #     "persona_addendum": None,
    # },
    # {
    #     "name": "DeepSeek",
    #     "model_id": "deepseek/deepseek-r1",
    #     "persona": "Stage1Member",
    #     #     "persona_addendum": None,
    # },
    # {
    #     "name": "Mistral",
    #     "model_id": "mistralai/mistral-large",
    #     "persona": "Stage1Member",
    #     #     "persona_addendum": None,
    # },
    # {
    #     "name": "Llama",
    #     "model_id": "meta-llama/llama-3.1-70b-instruct",
    #     "persona": "Stage1Member",
    #     #     "persona_addendum": None,
    # },
]

# Backward compatibility (existing code may import/use COUNCIL_MODELS).
COUNCIL_MODELS = [m["model_id"] for m in COUNCIL_MEMBERS]

# Chairman model - synthesizes final response
CHAIRMAN_MEMBER = {
    "name": "Chair",
    "model_id": "google/gemini-3-pro-preview",
    "persona": "Chairman",
    "persona_addendum": None,
}

# Backward compatibility
CHAIRMAN_MODEL = CHAIRMAN_MEMBER["model_id"]

# OpenRouter API endpoint
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Data directory for conversation storage
DATA_DIR = "data/conversations"
