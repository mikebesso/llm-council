"""Configuration for the LLM Council."""

import os
from dotenv import load_dotenv

load_dotenv()

# OpenRouter API key
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Council members - list of OpenRouter model identifiers
COUNCIL_MODELS = [
    "openai/gpt-5.1",
    "openai/gpt-4o",
    "openai/gpt-4.1-mini",
    "google/gemini-3-pro-preview",
    "google/gemini-3-flash-preview",
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-sonnet-4.5",
    "x-ai/grok-4",
    "qwen/qwen-2.5-72b-instruct",
    "deepseek/deepseek-r1",
    "mistralai/mistral-large",
    "meta-llama/llama-3.1-70b-instruct",
]

# Chairman model - synthesizes final response
CHAIRMAN_MODEL = "google/gemini-3-pro-preview"

# OpenRouter API endpoint
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Data directory for conversation storage
DATA_DIR = "data/conversations"
