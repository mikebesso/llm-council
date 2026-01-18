# OpenRouter model shortlist (straw man)

Purpose: a *starter* set of OpenRouter model IDs worth experimenting with for councils, routing, and “diversity of thought” demos. This is not an endorsement, just a pragmatic menu.

## How to use this list
- Pick **1–2 “default” generalists**.
- Add **1 fast/cheap** model for bulk stage-1 runs.
- Add **1 deep reasoning** model for hard synthesis.
- Add **1 open-weight** model for predictable cost / local parity.

---

## Tier A: General-purpose “do most things well”

### `anthropic/claude-sonnet-4.5`
**Strengths**
- Strong writing + analysis balance
- Often excellent instruction-following
- Good at multi-step work (agent-ish workflows)

**Weaknesses**
- Can be expensive vs smaller models
- Sometimes overly cautious / verbose

**Best use**: Chairperson, final synthesis, “responsible adult” voice.

---

### `openai/gpt-5.1`
**Strengths**
- Very strong general reasoning and planning
- Good coding, especially architecture + refactors
- Reliable tool/function calling

**Weaknesses**
- Cost can add up
- May be less “stylistically diverse” unless you push personas

**Best use**: Systems architecture council anchor, “principal engineer” member.

---

### `google/gemini-3-pro-preview`
**Strengths**
- Broad competence; good structured outputs
- Often fast for its capability tier
- Good at summarization and large-context work

**Weaknesses**
- Output style can feel “flat” without persona pressure
- Occasionally inconsistent adherence to strict schemas

**Best use**: Big-context reviewer, neutral analyst.

---

## Tier B: Fast + cheap workhorses (bulk Stage 1)

### `openai/gpt-4.1-nano`
**Strengths**
- Extremely fast and inexpensive
- Great for classification, extraction, short opinions
- Useful as a “crowd” of cheap voices

**Weaknesses**
- Weaker on deep reasoning and long coherence
- More likely to miss subtle constraints

**Best use**: Cheap council members, first-pass reviews.

---

### `google/gemini-2.0-flash-001`
**Strengths**
- Very fast, strong for the price
- Often good at coding snippets + instruction following
- Handles large prompts reasonably well

**Weaknesses**
- Can be shallow on highly technical architecture debates
- May “confidently summarize” without enough justification

**Best use**: Bulk reviewers, rapid brainstorming.

---

## Tier C: Deep reasoning / “slow thinking”

### `deepseek/deepseek-r1`
**Strengths**
- Strong reasoning performance for the price tier
- Great at deliberate tradeoff analysis
- Often produces surprisingly useful structured thinking

**Weaknesses**
- Slower, can be long-winded
- Occasional overconfidence; verify critical facts

**Best use**: Philosophy council anchor, “skeptical logician,” hard synthesis.

---

## Tier D: Strong coding specialists

### `openai/gpt-5.1-codex-max`
**Strengths**
- Excellent at codebase-scale changes and debugging
- Good at spec-to-implementation mapping
- Strong for refactors and test scaffolding

**Weaknesses**
- Overkill for non-coding prompts
- Price/latency not ideal for bulk runs

**Best use**: “Coder” member in AI council, tooling/framework work.

---

### `mistralai/mistral-large`
**Strengths**
- Strong reasoning + code + JSON
- Often concise and practical
- Good alternative perspective vs OpenAI/Anthropic

**Weaknesses**
- Can be less consistent than top-tier models on very complex tasks
- Tool calling / structured outputs can vary by provider setup

**Best use**: Engineer/architect member, second opinion.

---

## Tier E: Open-weight / flexible “control” options

### `qwen/qwen-2.5-72b-instruct`
**Strengths**
- Strong open model; good cost/perf
- Often good at structured reasoning and math-ish tasks
- Adds diversity (different training mix)

**Weaknesses**
- More sensitive to prompt phrasing
- May need more explicit constraints to stay on rails

**Best use**: “Independent reviewer” council member.

---

### `meta-llama/llama-3.1-70b-instruct`
**Strengths**
- Stable, widely used open model family
- Good general writing and summarization
- Predictable behavior across many deployments

**Weaknesses**
- Not always top-tier on hardest reasoning
- Can get repetitive without style controls

**Best use**: Baseline council member, consistency check.

---

## Tier F: Specialty add-ons (optional)

### `perplexity/sonar` (or similar “online/search” model)
**Strengths**
- Can be strong for web-grounded answers *when you enable search tooling*

**Weaknesses**
- Depends on plugin/tooling setup
- Not what you want for pure “committee debate” unless your prompt requires citations

**Best use**: Researcher member when prompts explicitly require sources.

---

## Suggested “starter pool” for your councils

**Default generalist (pick 1):**
- `anthropic/claude-sonnet-4.5` OR `openai/gpt-5.1`

**Fast/cheap bulk model (pick 1):**
- `google/gemini-2.0-flash-001` OR `openai/gpt-4.1-nano`

**Reasoning specialist (pick 1):**
- `deepseek/deepseek-r1`

**Coding specialist (optional):**
- `openai/gpt-5.1-codex-max`

**Open-weight diversity (pick 1):**
- `qwen/qwen-2.5-72b-instruct` OR `meta-llama/llama-3.1-70b-instruct`

---

## Practical notes for your demo
- If the goal is **diversity of thought**, mix providers *and* mix model families.
- Keep **member addendums** short but opinionated (that’s where the personality lives).
- For bulk demo runs, do **Stage 1 only** and show council-by-council contrast.

