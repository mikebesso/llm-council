"""chatgpt-to-obsidian

Convert a ChatGPT export (`conversations.json`) into an Obsidian-friendly folder structure.

What this script does
---------------------
- Reads the official ChatGPT export format (a list of conversation objects).
- For each conversation it creates an output folder and writes a Markdown transcript.
- Detects embedded "Canvas blobs" (JSON objects that contain keys like `name` and `content`)
  inside message text, extracts them, and writes each canvas to its own Markdown file.
- Replaces the canvas JSON blob in the transcript with an Obsidian wikilink to the extracted
  canvas note.

Why this exists
---------------
ChatGPT exports are great for backups, but not great for *human navigation*.
Obsidian thrives on:
- Many small Markdown files
- Predictable filenames
- Links between notes

This script turns "one huge JSON export" into a tidy set of Markdown notes that are:
- Greppable
- Linkable
- Friendly to Obsidian's graph and search

Output layout
-------------
By default, conversations are written into date folders:

    <output_dir>/
      2026-01-22/
        <conversation-slug>--<id8>/
          <conversation-slug>.md          # transcript
          canvas-<canvas-slug>-<id8>.md   # 0..n extracted canvases
          <conversation-slug>.json        # optional raw JSON (flag)

Notes on naming
---------------
- Obsidian tolerates underscores, but they look noisy in filenames and some themes.
  This script uses hyphens where possible.
- A short 8-character id suffix is used to prevent collisions when titles repeat.

Canvas blob format
------------------
A "canvas blob" is a JSON object embedded as plain text in a message part, shaped like:

    {
      "type": "...",
      "name": "My Canvas",
      "content": "# Markdown or other text..."
    }

Only blobs that:
- are valid JSON objects
- contain `"type"`, `"name"`, and `"content"`
- have string values for `name` and `content`
will be extracted.

Usage
-----
From the command line:

    python chatgpt-to-obsidian.py \
      /path/to/conversations.json \
      /path/to/output \
      --use-date-folders \
      --include-conversation-json

Or run the file directly after editing the hard-coded example at the bottom.

Caveats
-------
- This is a best-effort parser: if the export format changes, adjust `get_conversation()`.
- Extraction is conservative: we only treat JSON objects as canvases when they match the
  expected shape.

"""
import json
import os
import argparse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
import re
import unicodedata

def sanitize_filename(filename: Optional[str]) -> str:
    """Remove characters that are invalid on common filesystems.

    Args:
        filename: Raw title string from the export.

    Returns:
        A safe filename (not a full path). Falls back to 'noname'.
    """
    if filename is None or filename.strip() == "":
        return "noname"
    invalid_characters = '<>:"/\\|?*\n\t'
    for char in invalid_characters:
        filename = filename.replace(char, '')
    return filename


def slugify(text: str, max_len: int = 70) -> str:
    """Convert arbitrary text into a lowercase, hyphenated slug.

    This is used for folder and note names so that links are stable and predictable.

    Args:
        text: Source text (e.g., conversation title).
        max_len: Maximum length of the slug.

    Returns:
        A slug safe for filenames and Obsidian wikilinks.
    """
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    text = text.strip("-")
    return text[:max_len] or "untitled"


def try_parse_canvas_blob(text: str) -> Optional[Dict[str, Any]]:
    """Attempt to parse a message part as a serialized canvas blob.

    We only treat the text as a canvas if it:
    - looks like a JSON object
    - contains the keys 'type' and 'content'
    - has string values for 'name' and 'content'

    Args:
        text: A message part (string).

    Returns:
        Parsed canvas dict if recognized, otherwise None.
    """
    if not text:
        return None
    s = text.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    if '"type"' not in s or '"content"' not in s:
        return None
    try:
        obj = json.loads(s)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    if not isinstance(obj.get("name"), str):
        return None
    if not isinstance(obj.get("content"), str):
        return None
    return obj


def find_json_object_spans(text: str):
    """Find spans of top-level JSON objects in a string.

    This is a lightweight brace-matching scanner that ignores braces inside strings.
    It returns (start, end) spans for each detected top-level `{ ... }` object.

    Args:
        text: Input text.

    Returns:
        List of (start, end) spans where end is exclusive.
    """
    spans = []
    in_string = False
    escape = False
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    spans.append((start, i + 1))
                    start = None
    return spans


def extract_canvas_blobs(text: str):
    """Extract all recognizable canvas blobs from a text block.

    Returns a list of tuples:
      [((start, end), parsed_canvas_dict), ...]

    Args:
        text: Message text.

    Returns:
        List of hits with their character spans and parsed JSON objects.
    """
    hits = []
    for (s, e) in find_json_object_spans(text):
        chunk = text[s:e].strip()
        parsed = try_parse_canvas_blob(chunk)
        if parsed:
            hits.append(((s, e), parsed))
    return hits


def write_canvas_file(out_dir: Path, name: str, content: str, convo_id: str) -> Path:
    """Write a single extracted canvas to its own Markdown file.

    Args:
        out_dir: Conversation folder to write into.
        name: Canvas title.
        content: Canvas body (often Markdown).
        convo_id: Full conversation id; used to derive a short collision-resistant suffix.

    Returns:
        Path to the written canvas note.
    """
    safe = slugify(name)
    short = convo_id[:8] if convo_id else "noid"
    path = out_dir / f"canvas-{safe}-{short}.md"
    body = content.strip()
    if not body.lstrip().startswith("---"):
        body = f"# {name}\n\n" + body
    path.write_text(body + "\n", encoding="utf-8")
    return path


def get_conversation(node_id, mapping, out, last_author=None, out_dir: Optional[Path] = None, convo_id: str = ""):
    """Depth-first traversal that renders the conversation tree to Markdown.

    This walks the export's `mapping` graph starting from the root node and appends
    Markdown blocks into `out`.

    Canvas extraction behavior:
    - For each string part, we scan for embedded JSON objects.
    - If an object matches the canvas blob shape, we write it to a separate file and
      replace the blob with an Obsidian note link.

    Markdown formatting behavior:
    - Inserts `## <role>` headers when author role changes.
    - Skips system messages.

    Args:
        node_id: Current node id.
        mapping: Export mapping dict.
        out: List accumulator of Markdown chunks.
        last_author: Role of previous rendered message.
        out_dir: Where to write extracted canvases.
        convo_id: Conversation id (used for stable suffixes).
    """
    node = mapping[node_id]
    if node.get('message') and 'content' in node['message'] and 'parts' in node['message']['content']:
        content_parts = node['message']['content']['parts']
        parts_text = []
        for part in content_parts:
            if isinstance(part, str):
                text = part
                hits = extract_canvas_blobs(text)
                if hits and out_dir:
                    rendered = text
                    for (span, parsed) in reversed(hits):
                        (s, e) = span
                        canvas_path = write_canvas_file(out_dir, parsed['name'], parsed['content'], convo_id)
                        link = f"\n> [!note] Canvas: {parsed['name']}\n> [[{canvas_path.stem}]]\n"
                        rendered = rendered[:s] + link + rendered[e:]
                    parts_text.append(rendered)
                else:
                    parts_text.append(text)
            else:
                parts_text.append(str(part))

        if parts_text:
            author_role = node['message']['author']['role']
            text_block = ''.join(parts_text)
            if author_role != "system" and author_role != last_author:
                out.append(f"## {author_role}\n{text_block}")
            elif author_role != "system":
                out.append(text_block)
            last_author = author_role

    for child_id in node.get('children', []):
        get_conversation(child_id, mapping, out, last_author, out_dir, convo_id)

def generate_unique_filename(base_path, title):
    """Generate a unique Markdown filename in a folder.

    Note: This helper is currently unused by the main flow, but kept around in case
    you want to write top-level files without per-conversation folders.
    """
    version = 0
    title = title if title.strip() != "" else "noname"
    file_path = os.path.join(base_path, f"{title}.md")
    while os.path.exists(file_path):
        version += 1
        file_path = os.path.join(base_path, f"{title}-v{version}.md")
    return file_path

def main(input_file: str, output_dir: str, use_date_folders: bool, include_conversation_json: bool = False) -> None:
    """Convert a ChatGPT export to Obsidian-ready Markdown.

    Args:
        input_file: Path to `conversations.json` from the ChatGPT export.
        output_dir: Root folder where output is written.
        use_date_folders: If True, write conversations under YYYY-MM-DD folders.
            If False, write all conversation folders directly under `output_dir`.
        include_conversation_json: If True, also write the raw per-conversation JSON.

    Returns:
        None
    """
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)

    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.loads(f.read())
        for item in data:
            title = sanitize_filename(item.get("title") or "conversation")
            convo_id = item.get("id", "")
            short_id = convo_id[:8] if convo_id else "noid"

            # Date folder is optional. When disabled, everything goes directly under output_dir.
            date_iso = datetime.fromtimestamp(item["create_time"]).date().isoformat()
            base_dir = Path(output_dir)
            target_parent = (base_dir / date_iso) if use_date_folders else base_dir
            target_parent.mkdir(parents=True, exist_ok=True)

            # Folder base name (pretty) + suffix (collision-resistant)
            folder_base = slugify(title)
            convo_folder_name = f"{folder_base}--{short_id}"
            convo_folder = target_parent / convo_folder_name
            convo_folder.mkdir(parents=True, exist_ok=True)

            root_node_id = next(node_id for node_id, node in item['mapping'].items() if node.get('parent') is None)
            out = []
            get_conversation(root_node_id, item['mapping'], out, out_dir=convo_folder, convo_id=convo_id)

            if include_conversation_json:
                (convo_folder / f"{folder_base}.json").write_text(
                    json.dumps(item, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            md_path = convo_folder / f"{folder_base}.md"
            md_path.write_text('\n'.join(out), encoding="utf-8")

            print(f"Wrote conversation to: {md_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert a ChatGPT conversations.json export into Obsidian-friendly Markdown."
    )
    parser.add_argument(
        "input_file",
        help="Path to conversations.json from the ChatGPT export",
    )
    parser.add_argument(
        "output_dir",
        help="Directory to write the Obsidian-ready output",
    )
    parser.add_argument(
        "--use-date-folders",
        action="store_true",
        help="Store conversations under YYYY-MM-DD subfolders",
    )
    parser.add_argument(
        "--include-conversation-json",
        action="store_true",
        help="Also write per-conversation raw JSON alongside Markdown output",
    )

    args = parser.parse_args()

    main(
        args.input_file,
        args.output_dir,
        use_date_folders=args.use_date_folders,
        include_conversation_json=args.include_conversation_json,
    )

    # Example (kept for copy/paste). Uncomment and adjust paths if you prefer hard-coded runs.
    # main(
    #     "/Users/mike/Downloads/chatgpt-export-2026-01-22/conversations.json",
    #     "/Users/mike/projects/chatgpt-conversations",
    #     use_date_folders=True,
    #     include_conversation_json=False,
    # )