import os
import sys
from pathlib import Path

module_dir = os.path.join(os.path.dirname(__file__), '..')
sys.path.append(module_dir)

from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

def create_conversation() -> str:
    """Create a conversation and return its id."""
    convo = client.post("/api/conversations", json={}).json()
    assert "id" in convo
    cid = convo["id"]
    assert cid
    return cid

def convene_council(json: dict) -> dict:
    """MVP runner: execute a prompt without creating/persisting a conversation."""
    # cid is currently unused for prompt runs, but we keep the signature so callers don't break.
    payload = {
        "content": json.get("content", ""),
        "council": json.get("council"),
        "stages": json.get("stages"),
        "prompt_id": json.get("prompt_id"),
        "title": json.get("title"),
    }
    # Drop null/None fields so the backend can apply defaults cleanly.
    payload = {k: v for k, v in payload.items() if v is not None}

    r = client.post("/api/prompts/run", json=payload)
    assert r.status_code == 200
    return r.json()

def load_prompt_config(path: Path) -> dict:
    """Load optional TOML config for a prompt product.

    Returns a dict suitable for passing directly to convene_council(),
    containing only supported MVP keys.

    If the TOML file is missing, returns an empty dict (defaults apply).
    """
    path = path.resolve()

    if not path.exists():
        return {}

    # Python 3.11+ has tomllib in the stdlib; fall back to tomli if needed.
    try:
        import tomllib  # type: ignore
        loads = tomllib.loads
    except Exception:  # pragma: no cover
        import tomli  # type: ignore
        loads = tomli.loads

    raw = loads(path.read_text(encoding="utf-8"))

    # Top-level metadata
    prompt_id = raw.get("id")
    title = raw.get("title")
    enabled = raw.get("enabled", True)

    if enabled is False:
        raise ValueError(f"Prompt is disabled via config: {path}")

    exec_cfg = raw.get("execution", {}) or {}
    council = exec_cfg.get("council")
    stages = exec_cfg.get("stages")

    # MVP validation
    if stages is not None:
        if not isinstance(stages, list) or not all(isinstance(x, int) for x in stages):
            raise ValueError("execution.stages must be a list of integers")

    if council is not None and not isinstance(council, str):
        raise ValueError("execution.council must be a string")

    out: dict = {}
    if prompt_id is not None:
        out["prompt_id"] = prompt_id
    if title is not None:
        out["title"] = title
    if council is not None:
        out["council"] = council
    if stages is not None:
        out["stages"] = stages

    return out


def load_prompt(path: Path) -> dict:
    path = (Path(__file__).parent.parent / 'prompts' / path).resolve()

    prompt_path = path.with_suffix('.prompt.md')
    toml_path = path.with_suffix('.prompt.toml')

    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt markdown not found: {prompt_path}")

    prompt_md = prompt_path.read_text(encoding="utf-8")

    # load and process toml (optional)
    config = load_prompt_config(toml_path)

    payload = {"content": prompt_md}
    payload.update(config)

    return payload
    


def main():


    prompt = load_prompt(Path('doing-math') / 'leap-year-and-network-days')
    response = convene_council(prompt)



    assert "stage1" in response # non-empty
    assert "stage2"  in response # non-empty
    assert "stage3"  in response # non-empty


if __name__ == '__main__':
    main()