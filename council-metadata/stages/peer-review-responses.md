+++ 
id = "peer-review-responses"
version = "1.0"
name = "Peer Review Responses"
kind = "fanout_members"
purpose = "Each council member critiques peer responses and provides a strict final ranking."

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
source = "stage_context"
label = "Peer Responses (Anonymized)"
required = true
render_style = "markdown"

[[prompt.parts]]
source = "instructions"
label = "Task"
required = true
render_style = "markdown"
content = """
You are evaluating different responses to the following question.

Your task:
1. Evaluate each response individually. For each response, explain what it does well and what it does poorly.
2. When evaluating quality, you MUST explicitly check for the following common failure modes (and call them out if missing):
   - Constraint asymmetry: who bears the consequences vs who keeps an escape hatch (e.g., decision-makers retaining access/resources the public does not).
   - Embodied constraints: biological/physical limits (bathrooms, water, heat/cold, fatigue, mobility, disability access) and dignity impacts.
   - Second-order logistics: what happens afterward (waste, cleanup, disposal, enforcement residue, transport, bottlenecks).
   - Incentives and perverse optimizations: density, optics, throughput, or control prioritized over human needs.
   - One missing operational detail: explicitly name one concrete logistical or operational detail the response fails to address (e.g., waste removal, staffing, enforcement load, cleanup timing, accessibility edge cases).
3. Then, at the very end of your response, provide a final ranking.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response A")
- Do not add any other text or explanations in the ranking section

Example of the correct format for your ENTIRE response:

Response A provides good detail on X but misses Y...
Response B is accurate but lacks depth on Z...
Response C offers the most comprehensive answer...

FINAL RANKING:
1. Response C
2. Response A
3. Response B

Now provide your evaluation and ranking.
"""

+++

## What this stage does

This stage is the council’s **structured critique pass**.

- **Input:** the user query, the council purpose, and the anonymized peer responses (from the prior stage).
- **Process:** each council member reviews the peer responses, calling out strengths, weaknesses, and common failure modes.
- **Output:** a critique plus a strictly formatted `FINAL RANKING` section that can be parsed downstream.

This stage is intentionally strict about the ranking format so the engine can reliably extract it even when models are verbose.

## Prompt assembly

The engine renders the prompt by concatenating these parts in order:

1. **Council Purpose** (`council_prompt`)
2. **Your Persona** (`persona_prompt`)
3. **Peer Responses (Anonymized)** (`stage_context` rendered as labeled responses)
4. **Task** (`instructions`)

## Notes on placeholders

- `stage_context` should render peer responses as `Response A`, `Response B`, … along with their full text.
- The user question is assumed to be included in the `stage_context` rendering or the council purpose; if you want it explicit, add a `user_query` part above the `stage_context` part.
