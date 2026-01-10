from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Iterable


def slugify(text: str, max_len: int = 80) -> str:
    """
    Convert text into a filesystem-friendly slug.
    """
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)         # remove punctuation
    text = re.sub(r"[\s_-]+", "-", text)         # collapse whitespace/underscores
    text = text.strip("-")
    return text[:max_len].rstrip("-") or "untitled"


def derive_doc_title(convo: Dict[str, Any]) -> str:
    """
    Best-effort title selection.
    1) Use convo['title'] if present and non-empty.
    2) Else: use first user message (trimmed).
    3) Else: fallback.
    """
    title = (convo.get("title") or "").strip()
    if title:
        return title

    # Try first user message
    for msg in convo.get("messages", []):
        if msg.get("role") == "user":
            content = (msg.get("content") or "").strip()
            if content:
                # use first sentence-ish chunk
                first = re.split(r"[\n\.!?]", content, maxsplit=1)[0].strip()
                return (first[:100] + "…") if len(first) > 100 else first

    return "Untitled Conversation"


def derive_filename(convo: Dict[str, Any], ext: str = ".md") -> str:
    """
    A practical filename format:
    YYYY-MM-DD__<slugified-title>__<short-id>.md
    """
    created_at = (convo.get("created_at") or "").strip()
    date_part = "unknown-date"
    try:
        # supports ISO timestamps like 2026-01-10T20:42:32.317395
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        date_part = dt.date().isoformat()
    except Exception:
        pass

    title = derive_doc_title(convo)
    slug = slugify(title)
    short_id = (convo.get("id") or "noid")[:8]

    return f"{date_part}__{slug}__{short_id}{ext}"


def render_conversation_markdown(convo: Dict[str, Any]) -> str:
    """
    Render one conversation JSON into a clean single markdown document.
    """
    title = derive_doc_title(convo)
    created_at = convo.get("created_at", "")
    convo_id = convo.get("id", "")

    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- **Conversation ID:** `{convo_id}`")
    if created_at:
        lines.append(f"- **Created At:** `{created_at}`")
    lines.append("")

    # Find the main council output message (the assistant message containing stage1/2/3)
    council_msg: Optional[Dict[str, Any]] = None
    user_prompt: Optional[str] = None

    for msg in convo.get("messages", []):
        if msg.get("role") == "user" and not user_prompt:
            user_prompt = msg.get("content", "")
        if msg.get("role") == "assistant" and ("stage1" in msg or "stage3" in msg):
            council_msg = msg

    if user_prompt:
        lines.append("## Original Prompt")
        lines.append("")
        lines.append(user_prompt.strip())
        lines.append("")

    if not council_msg:
        lines.append("> No council stages found in this conversation JSON.")
        return "\n".join(lines).strip() + "\n"

    stage1 = council_msg.get("stage1", []) or []
    stage2 = council_msg.get("stage2", []) or []
    stage3 = council_msg.get("stage3", {}) or {}

    if stage1:
        lines.append("## Stage 1 — Member Responses")
        lines.append("")
        for i, r in enumerate(stage1, start=1):
            member = r.get("member_name") or r.get("model") or f"Member {i}"
            model_id = r.get("model_id") or ""
            lines.append(f"### {member}")
            if model_id:
                lines.append(f"- **Model:** `{model_id}`")
            lines.append("")
            lines.append((r.get("response") or "").strip())
            lines.append("")

    if stage2:
        lines.append("## Stage 2 — Rankings & Critique")
        lines.append("")
        for i, r in enumerate(stage2, start=1):
            judge = r.get("member_name") or r.get("model") or f"Judge {i}"
            model_id = r.get("model_id") or ""
            parsed = r.get("parsed_ranking")
            lines.append(f"### {judge}")
            if model_id:
                lines.append(f"- **Model:** `{model_id}`")
            if parsed:
                lines.append(f"- **Parsed ranking:** {', '.join(parsed)}")
            lines.append("")
            lines.append((r.get("ranking") or "").strip())
            lines.append("")

    if stage3:
        lines.append("## Stage 3 — Chairman Synthesis")
        lines.append("")
        chair = stage3.get("chairman_member_name") or stage3.get("member_name") or stage3.get("model") or "Chairman"
        chair_model = stage3.get("chairman_model_id") or stage3.get("model_id") or ""
        lines.append(f"### {chair}")
        if chair_model:
            lines.append(f"- **Model:** `{chair_model}`")
        lines.append("")
        lines.append((stage3.get("response") or "").strip())
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def export_conversation_json_to_markdown(json_path: str | Path, out_dir: str | Path) -> Path:
    """
    Reads a single conversation JSON file and writes one Markdown file.
    Returns the written path.
    """
    json_path = Path(json_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    convo = json.loads(json_path.read_text(encoding="utf-8"))
    md = render_conversation_markdown(convo)
    filename = derive_filename(convo, ext=".md")
    out_path = out_dir / filename
    out_path.write_text(md, encoding="utf-8")
    return out_path


def _project_root() -> Path:
    """Return the project root assuming this script lives under `<root>/scripts/`."""
    # scripts/ is expected to be directly under the repository root
    return Path(__file__).resolve().parents[1]


def _timestamp_folder_name(dt: Optional[datetime] = None) -> str:
    """Filesystem-friendly timestamp folder name."""
    dt = dt or datetime.now()
    return dt.strftime("%Y%m%d_%H%M%S")


def iter_conversation_json_files(conversations_dir: Path) -> Iterable[Path]:
    """Yield conversation JSON files in a deterministic order."""
    if not conversations_dir.exists():
        return []
    return sorted(p for p in conversations_dir.glob("*.json") if p.is_file())


def export_all_conversations_to_timestamped_folder(
    conversations_dir: str | Path | None = None,
    output_root_dir: str | Path | None = None,
) -> Path:
    """Export every JSON in data/conversations to a timestamped folder under data/output."""
    root = _project_root()

    conversations_dir = Path(conversations_dir) if conversations_dir else (root / "data" / "conversations")
    output_root_dir = Path(output_root_dir) if output_root_dir else (root / "data" / "output")

    out_dir = output_root_dir / _timestamp_folder_name()
    out_dir.mkdir(parents=True, exist_ok=True)

    json_files = list(iter_conversation_json_files(conversations_dir))
    if not json_files:
        print(f"No conversation JSON files found in: {conversations_dir}")
        print(f"Created empty output folder: {out_dir}")
        return out_dir

    ok = 0
    failed: list[tuple[Path, str]] = []

    for json_path in json_files:
        try:
            out_path = export_conversation_json_to_markdown(json_path=json_path, out_dir=out_dir)
            ok += 1
            print(f"Wrote: {out_path.relative_to(root)}")
        except Exception as e:
            failed.append((json_path, str(e)))
            print(f"FAILED: {json_path} -> {e}")

    print("")
    print(f"Export complete: {ok}/{len(json_files)} succeeded")
    if failed:
        print("Failures:")
        for p, err in failed:
            print(f"- {p.name}: {err}")

    return out_dir


def main() -> int:
    """CLI entrypoint."""
    import argparse

    parser = argparse.ArgumentParser(description="Export council conversation JSON to Markdown.")
    parser.add_argument(
        "--conversations-dir",
        default=None,
        help="Path to the conversations folder (default: <root>/data/conversations)",
    )
    parser.add_argument(
        "--output-root-dir",
        default=None,
        help="Path to the output root folder (default: <root>/data/output)",
    )

    args = parser.parse_args()
    export_all_conversations_to_timestamped_folder(
        conversations_dir=args.conversations_dir,
        output_root_dir=args.output_root_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())