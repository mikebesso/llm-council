+++
id = "improve-prompt"
version = "1.0"
name = "Improve Prompt"
kind = "single"
purpose = "Normalize and clarify the user’s query once before handing it to the council, reducing divergence caused by prompt drift."

inputs_required = ["user_query"]
inputs_optional = ["council_prompt"]

[response_format]
type = "json"
must_include_keys = ["improved_query", "notes", "assumptions", "clarifying_questions"]

[failure_policy]
on_refusal = "record_error"
on_parse_error = "retry_once"

[[prompt.parts]]
source = "council_prompt"
label = "Council Context (Optional)"
required = false
render_style = "markdown"

[[prompt.parts]]
source = "user_query"
label = "Original User Query"
required = true
render_style = "markdown"

[[prompt.parts]]
source = "instructions"
label = "Task"
required = true
render_style = "markdown"
content = """
You are improving a user’s query for clarity, precision, and downstream usefulness.

Goals:
- Preserve the user’s original intent and tone.
- Remove ambiguity and missing constraints where possible.
- Make the query easy for multiple independent council members to answer consistently.
- Avoid adding extra scope. Do NOT “helpfully” broaden the task unless the user clearly asked for that.

Output MUST be valid JSON with exactly these keys:
- improved_query: string
- notes: string (brief explanation of what you changed and why)
- assumptions: array of strings (only assumptions you had to make; keep short)
- clarifying_questions: array of strings (ONLY if truly required to proceed; otherwise return an empty array)

Rules:
- If the user’s request is already clear, improved_query may be a lightly edited version.
- If the user asks for something impossible, unsafe, or mismatched for this council, do not refuse here; keep improved_query faithful and surface concerns in notes.
- Do not include markdown, backticks, or any extra keys in the JSON.
"""
+++

## What this stage does

This stage produces a **single improved query** before the council diverges into multiple member responses.

Why:
- If each model “improves” the prompt independently, we start the workflow with unnecessary entropy.
- A single normalization pass reduces friction and makes peer review more meaningful.

## Output contract

This stage returns strict JSON:

- `improved_query`: the improved prompt we may pass to later stages
- `notes`: what changed and why (short)
- `assumptions`: explicit assumptions made (usually empty)
- `clarifying_questions`: only if the workflow truly cannot proceed without answers (usually empty)

## Prompt assembly

The engine renders the prompt by concatenating these parts in order:

1. **Council Context (Optional)** (`council_prompt`, if present)
2. **Original User Query** (`user_query`)
3. **Task** (`instructions`)