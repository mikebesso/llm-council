# Prompt Schema — Prompts as Products

This system treats **prompts as first-class products**, not disposable strings.

A prompt is a durable artifact that:
- Is written and reviewed as Markdown
- Can be rendered by standard tooling
- Can be executed repeatedly under different rules
- Can evolve independently from execution configuration

The core idea is simple:

> **Content is Markdown. Execution is configuration.**

---

## Design Principles

### 1. Separation of concerns

We intentionally separate:
- **Prompt content** (Markdown) — the intellectual work
- **Execution configuration** (TOML) — how that work is run

This keeps:
- Prompt diffs clean and readable
- Configuration machine-friendly and automatable
- Tooling flexible as the system evolves

---

### 2. Optional configuration by design

A prompt **does not require** a TOML file to run.

If a TOML file is missing:
- Defaults are applied
- The environment’s default council is used
- The run proceeds normally
- The run report records that defaults were used

This supports:
- Fast experimentation
- Incremental adoption
- Safe batch execution across folders

Missing configuration is **a supported state**, not an error.

---

### 3. Folder-based execution with reporting

When a runner processes a folder:
- Each prompt is treated as an independent unit of work
- A run report is generated
- Validation findings are recorded explicitly:
  - Missing TOML
  - Defaults applied
  - Invalid or unknown fields
  - Disabled prompts skipped

Nothing fails silently.  
Nothing is assumed.

---

### 4. Prompts as reusable products

This model allows:
- One prompt → many execution contexts
- One canonical prompt reused across clients
- Programmatic generation of configuration
- Prompt libraries that evolve independently of deployment rules

---

## File Layout

A prompt product may consist of:

The Markdown file is required.  
The TOML file is optional.

Both share the same base name.

---

## TOML Configuration Schema (v1)

The following illustrates the **canonical TOML schema** used to control prompt execution.

```toml
# Prompt configuration schema (v1)
# This file is OPTIONAL.
# If absent, the runner will execute the prompt using defaults.

version = "1.0"

# Stable identifier for this prompt product
id = "weekly-risk-brief"

# Human-friendly name
title = "Weekly Risk Brief"

# Allows prompts to be disabled without deletion
enabled = true

# Free-form categorization
tags = ["briefing", "risk", "executive"]

# ---------------------------------
# Execution controls
# ---------------------------------
[execution]

# Council to use for this prompt.
# If omitted, the environment default council is used.
council = "risk_engineering"

# Stages to execute (defaults apply if omitted)
stages = [1, 2, 3]

# Optional priority for batch execution
priority = 50

# Optional seed for deterministic sampling
seed = 12345

# ---------------------------------
# Inputs (future-facing)
# ---------------------------------
[inputs]

# Arbitrary variables for later interpolation
[inputs.vars]
client_name = "Acme Manufacturing"
region = "North America"

# Optional file-based inputs
files = [
  "data/risk_register.json",
  "data/incident_summary.md"
]

# Additional context not part of the prompt body
context = "Focus on material operational and supply-chain risks."

# ---------------------------------
# Output controls
# ---------------------------------
[output]

# Primary output format
format = "markdown"

# Suggested output file base name
basename = "acme-weekly-risk-brief"

# Optional output folder override
folder = "clients/acme"

# Emit multiple artifacts if desired
emit = ["markdown", "json"]

# ---------------------------------
# Scheduling (reserved for later)
# ---------------------------------
[schedule]

# Currently ignored by the runner
mode = "weekly"
timezone = "America/Chicago"
rule = "MON 07:00"
```