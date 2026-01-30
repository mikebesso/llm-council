
+++
id = "synthesize-output"
version = "1.0"
name = "Synthesize Output"
kind = "single"
purpose = "The Chairman synthesizes the council’s responses and peer reviews into a single final answer."

inputs_required = ["user_query", "council_prompt", "stage_context"]
inputs_optional = []

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
label = "Original Question"
required = true
render_style = "markdown"

[[prompt.parts]]
source = "stage_context"
label = "Council Inputs"
required = true
render_style = "markdown"

[[prompt.parts]]
source = "instructions"
label = "Task"
required = true
render_style = "markdown"
content = """
You are the Chairman of an LLM Council. Multiple AI models have provided responses to a user's question, and then reviewed and ranked each other's responses.

Your task as Chairman is to synthesize all of this information into a single, comprehensive, accurate answer to the user's original question.

In your synthesis, you MUST:
- Preserve the best insights across the council, but also correct blind spots.
- Explicitly surface “constraint asymmetry” when present (e.g., organizers/officials retaining bathrooms, exits, water, warmth, or privileges that attendees do not).
- Include embodied constraints and dignity impacts (humans have bodies; plans must respect biology).
- Include second-order logistics (waste handling/disposal, cleanup, enforcement residue, downstream bottlenecks).
- If the discussion implies ad-hoc coping (e.g., diapers), explicitly address the operational consequences (biohazard collection, containment, and disposal) and why that signals a planning failure.

Provide a clear, well-reasoned final answer that represents the council's collective wisdom.
"""
+++

## What this stage does

This stage is the council’s **final synthesis pass**.

- **Input:** the user query, the council purpose, and the accumulated council context (responses + peer reviews).
- **Process:** the Chairman integrates the strongest insights, resolves conflicts, and corrects operational blind spots.
- **Output:** a single final answer suitable for the end user.

## Prompt assembly

The engine renders the prompt by concatenating these parts in order:

1. **Council Purpose** (`council_prompt`)
2. **Your Persona** (`persona_prompt`)
3. **Original Question** (`user_query`)
4. **Council Inputs** (`stage_context` rendered as a structured digest of prior stages)
5. **Task** (`instructions`)

## Notes on placeholders

- `stage_context` should include both:
  - Stage 1: the labeled responses from each member (e.g., `Response A`, `Response B`, …)
  - Stage 2: the peer reviews/rankings (and parsed rankings, if available)
- We intentionally avoid separate placeholders like `stage1_text` and `stage2_text` so later workflows can reuse the same template contract.
