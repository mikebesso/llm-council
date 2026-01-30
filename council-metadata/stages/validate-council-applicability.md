+++
id = "validate-council-applicability"
version = "1.0"
name = "Validate Council Applicability"
kind = "single"
purpose = "Short-circuit misuse: decide whether this council workflow is appropriate for the user query and suggest alternatives when it is not."

inputs_required = ["user_query", "council_prompt"]
inputs_optional = []

[response_format]
type = "json"
must_include_keys = ["applicable", "decision", "reason", "alternatives"]

[failure_policy]
on_refusal = "record_error"
on_parse_error = "retry_once"

[[prompt.parts]]
source = "council_prompt"
label = "Council Purpose"
required = true
render_style = "markdown"

[[prompt.parts]]
source = "user_query"
label = "User Query"
required = true
render_style = "markdown"

[[prompt.parts]]
source = "instructions"
label = "Task"
required = true
render_style = "markdown"
content = """
You are a workflow gatekeeper. Determine whether a council of LLMs is an appropriate tool for this user query.

Guidance:
- This council workflow is most useful for tasks that benefit from multiple perspectives, critique, and synthesis.
- It is usually NOT appropriate for tasks that are purely mechanical (e.g., average a list of numbers), require authoritative real-time facts without browsing, or have a clear single deterministic answer.
- If the query is ambiguous, assume we can proceed (applicable=true) and mention the ambiguity in the reason unless the ambiguity prevents any meaningful attempt.

Output MUST be valid JSON with exactly these keys:
- applicable: boolean
- decision: string (MUST be exactly one of: "CONTINUE" or "STOP")
- reason: string (short, concrete explanation)
- alternatives: array of strings (if decision is STOP, provide 2–5 better approaches; if CONTINUE, return an empty array)

Rules:
- If applicable is true, decision MUST be "CONTINUE".
- If applicable is false, decision MUST be "STOP".
- Do not include markdown, backticks, or any extra keys in the JSON.
"""
+++

## What this stage does

This stage is the council’s **gate**.

- **Input:** the user query and the council purpose.
- **Process:** decide whether a council workflow is the right tool for the job.
- **Output:** strict JSON indicating CONTINUE/STOP, with a short reason and suggested alternatives when stopping.

This stage prevents us from wasting tokens and time on tasks the council is not well suited for.

## Output contract

Strict JSON with keys:

- `applicable` (boolean)
- `decision` ("CONTINUE" | "STOP")
- `reason` (short string)
- `alternatives` (array; usually empty unless STOP)

## Prompt assembly

The engine renders the prompt by concatenating these parts in order:

1. **Council Purpose** (`council_prompt`)
2. **User Query** (`user_query`)
3. **Task** (`instructions`)

