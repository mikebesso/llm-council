+++
id = "delegate-prompt"
version = "1.0"
name = "Delegate Prompt"
kind = "fanout_members"
purpose = "Each council member answers the question independently using the council purpose and their persona."

inputs_required = ["user_query", "council_prompt"]
inputs_optional = ["improved_query"]

[response_format]
type = "text"

[failure_policy]
on_refusal = "record_error"
on_parse_error = "fallback_text"

[[prompt.parts]]
source = "council_prompt"
label = "Council Purpose"
required = true
render_style = "markdown"

[[prompt.parts]]
source = "persona_prompt"
label = "Your Persona"
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
You are a member of an LLM Council assembled to provide insight.

You have been given:
- A user query: the question or concept under investigation
- A council purpose: why this council is being asked to weigh in
- Your persona: the lens you should use when responding

Write your best answer to the user query. Be specific and useful. Avoid filler and avoid repeating the question back verbatim.

If an improved query is provided elsewhere in context, you may use it to clarify intent, but do not ignore the original user query.
"""
+++

## What this stage does

This is the council’s **primary divergence stage**.

- **Input:** the user query and the council purpose, plus each member’s persona (and optional persona addendum).
- **Process:** members answer independently, without seeing each other’s responses.
- **Output:** a set of parallel responses that will be labeled and passed into peer review.

## Prompt assembly

The engine renders the prompt by concatenating these parts in order:

1. **Council Purpose** (`council_prompt`)
2. **Your Persona** (`persona_prompt`)
3. **User Query** (`user_query`)
4. **Task** (`instructions`)



