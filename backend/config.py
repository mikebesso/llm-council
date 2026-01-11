"""Configuration for the LLM Council."""

import os
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Project root is the parent of the backend/ folder.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Ensure we load .env from the project root (not CWD).
load_dotenv(PROJECT_ROOT / ".env")

# Council metadata lives in the project root (sibling of backend/).
COUNCILS_DIR = PROJECT_ROOT / "councils"


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


def _read_text(path: Path) -> str:
    if not path.exists():
        msg = f"CONFIG ERROR: Missing required file: {path}"
        logger.error(msg)
        raise RuntimeError(msg)
    return path.read_text(encoding="utf-8")


def _parse_toml_front_matter(
    md_text: str, *, source: str, require: bool = True
) -> Tuple[Dict[str, Any], str]:
    """Parse TOML front matter delimited by +++ ... +++ in a markdown file.

    If `require` is False and the file does not start with +++, returns ({}, full_text).

    Returns: (front_matter_dict, body_text)
    """
    lines = md_text.splitlines()

    # Optional front matter support (useful for simple persona markdown files).
    if not lines or lines[0].strip() != "+++":
        if require:
            msg = (
                f"CONFIG ERROR: {source} is missing TOML front matter start delimiter (+++)."
            )
            logger.error(msg)
            raise RuntimeError(msg)
        # No front matter; treat whole file as body.
        return {}, md_text.lstrip("\n")

    end_idx: Optional[int] = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "+++":
            end_idx = i
            break

    if end_idx is None:
        msg = f"CONFIG ERROR: {source} is missing TOML front matter end delimiter (+++)."
        logger.error(msg)
        raise RuntimeError(msg)

    toml_text = "\n".join(lines[1:end_idx]).strip()
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


def _require_key(d: Dict[str, Any], key: str, *, source: str) -> Any:
    if key in d and d[key] not in (None, ""):
        return d[key]
    msg = f"CONFIG ERROR: Missing required key '{key}' in {source}."
    logger.error(msg)
    raise RuntimeError(msg)


def _load_persona_prompt(persona_id: str) -> str:
    persona_file = COUNCILS_DIR / "personas" / f"{persona_id}.md"
    md = _read_text(persona_file)
    fm, body = _parse_toml_front_matter(md, source=str(persona_file), require=False)
    # Persona content is the markdown body; front matter is optional metadata.
    if not body.strip():
        msg = f"CONFIG ERROR: Persona file has empty body: {persona_file}"
        logger.error(msg)
        raise RuntimeError(msg)
    return body.strip()


def _load_member(member_id: str) -> Dict[str, Any]:
    member_file = COUNCILS_DIR / "members" / f"{member_id}.md"
    md = _read_text(member_file)
    fm, _body = _parse_toml_front_matter(md, source=str(member_file))

    name = _require_key(fm, "name", source=str(member_file))
    model_id = _require_key(fm, "model_id", source=str(member_file))
    persona_id = _require_key(fm, "persona", source=str(member_file))
    persona_addendum = fm.get("persona_addendum")

    persona_prompt = _load_persona_prompt(str(persona_id))

    return {
        "name": str(name),
        "model_id": str(model_id),
        # Back-compat: keep persona as an identifier.
        "persona": str(persona_id),
        # New: resolved prompt text (the runtime can choose to use this).
        "persona_prompt": persona_prompt,
        "persona_addendum": str(persona_addendum) if persona_addendum else None,
    }


def _load_chairman(chairman_id: str) -> Dict[str, Any]:
    chairman_file = COUNCILS_DIR / "chairmen" / f"{chairman_id}.md"
    md = _read_text(chairman_file)
    fm, _body = _parse_toml_front_matter(md, source=str(chairman_file))

    name = _require_key(fm, "name", source=str(chairman_file))
    model_id = _require_key(fm, "model_id", source=str(chairman_file))
    persona_id = _require_key(fm, "persona", source=str(chairman_file))
    persona_addendum = fm.get("persona_addendum")

    persona_prompt = _load_persona_prompt(str(persona_id))

    return {
        "name": str(name),
        "model_id": str(model_id),
        "persona": str(persona_id),
        "persona_prompt": persona_prompt,
        "persona_addendum": str(persona_addendum) if persona_addendum else None,
    }


def _load_council_from_slug(slug: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    council_file = COUNCILS_DIR / f"{slug}.md"
    md = _read_text(council_file)
    fm, _body = _parse_toml_front_matter(md, source=str(council_file))

    chairman_id = _require_key(fm, "chairman", source=str(council_file))
    members = _require_key(fm, "members", source=str(council_file))

    if not isinstance(members, list) or not members:
        msg = f"CONFIG ERROR: {council_file} 'members' must be a non-empty list."
        logger.error(msg)
        raise RuntimeError(msg)

    # Preserve explicit ordering from the council file.
    member_dicts: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for m_id in members:
        m_id_str = str(m_id)
        if m_id_str in seen_ids:
            msg = f"CONFIG ERROR: Duplicate member id '{m_id_str}' in {council_file}."
            logger.error(msg)
            raise RuntimeError(msg)
        seen_ids.add(m_id_str)
        member_dicts.append(_load_member(m_id_str))

    chairman_dict = _load_chairman(str(chairman_id))
    return chairman_dict, member_dicts


# OpenRouter API key
OPENROUTER_API_KEY = _require_env_var("OPENROUTER_API_KEY")

# Council selection (slug) from env
COUNCIL = _require_env_var("COUNCIL")

# Build the council from metadata at startup. Any configuration error aborts the backend.
try:
    CHAIRMAN_MEMBER, COUNCIL_MEMBERS = _load_council_from_slug(COUNCIL)
except Exception as e:
    msg = f"CONFIG ERROR: Failed to load council '{COUNCIL}' from {COUNCILS_DIR}: {e}"
    logger.error(msg)
    raise

# Backward compatibility (existing code may import/use COUNCIL_MODELS).
COUNCIL_MODELS = [m["model_id"] for m in COUNCIL_MEMBERS]

# Backward compatibility
CHAIRMAN_MODEL = CHAIRMAN_MEMBER["model_id"]

# OpenRouter API endpoint
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Data directory for conversation storage
DATA_DIR = str(PROJECT_ROOT / "data" / "conversations")
