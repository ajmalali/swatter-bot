# The LLM fills fields; code renders the Issue

Every Issue must follow the same layout every time. The LLM is therefore never asked to write an Issue body. It returns values for the Template's fields as JSON, constrained to the repo's real label set. Deterministic code renders the Template, quotes the original Report verbatim, adds reporter name, channel, and permalink, and marks empty fields as not provided. The LLM's second job is equally narrow: given a Draft and up to three Candidates, say whether each is the same bug and why. Everything else (routing, retrieval, thresholds, Clarification limits, notifications) is code.

## Consequences

- Swapping models changes field quality, never layout.
- Prompts and verdicts are logged to SQLite so model swaps are measured with the golden evaluation set rather than guessed.
