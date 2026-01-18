+++
id = "peer-review-responses"
name = "Peer Review Responses"
+++

You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

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

Now provide your evaluation and ranking:
