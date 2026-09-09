# Swatter design

Agreed 2026-09-08. Vocabulary is in `CONTEXT.md`; the reasoning behind the big choices is in `docs/adr/`.

## Purpose and shape

- Internal tool and applied-AI showcase. Self-hosted: clone, fill `.env`, run on a VPS or a local server. Single Slack workspace, not multi-tenant.
- Zero inbound traffic: Slack Socket Mode, GitHub polled every two minutes with `since`. No web server, no ngrok, no webhooks (ADR 0001).
- Python, `uv`, one process running the socket handler and a scheduler. Ships as a Docker image with a compose file, `.env.example`, a Slack app manifest, a GitHub App manifest, and a CLI health check wired to Docker's healthcheck.

## Configuration

- GitHub auth: fine-grained PAT for quick start, GitHub App as the documented upgrade. Same PyGithub client either way.
- LLM: one setting, OpenAI-compatible base URL, key, and model (ADR 0002). Anthropic, OpenAI, OpenRouter, Groq, or Ollama. No per-task override yet.
- Embeddings: fastembed locally by default; optional OpenAI-compatible embeddings endpoint.
- Bindings: env default repo, plus any number per channel added with `/swatter connect owner/repo [template]` and removed with `/swatter disconnect owner/repo`. Anyone may run them; the actor is logged. A channel with several Bindings lets the LLM choose the repo (see step 2); a channel with one behaves as before.
- Template: the repo's issue template whose filename contains "bug", or the one named on the Binding, else Swatter's default. Issue forms supply required fields; for Markdown templates, steps, expected, and actual count as required.

## Flow for one Report

1. Trigger by app mention or the "File as bug" message shortcut. Fetch the full thread.
2. Code fetches each bound repo's description, labels, and Template. When the channel has more than one Binding, a first small LLM call picks `repo` as an enum constrained to the bound list, from the repo descriptions alone; labels and Template then come from the chosen repo. (It is a separate call because the field schema depends on the chosen repo's Template.) The LLM then fills the fields as JSON, validated with Pydantic, one retry with the error fed back (ADR 0004).
3. Hybrid retrieval over every bound repo's open Issues and those closed within 30 days, so a duplicate filed in the other repo is still caught: FTS5 top 10, cosine top 10, reciprocal rank fusion, top three to the LLM judge (ADR 0003). Each Candidate shows its repo.
4. Candidates found: offer up to three Append buttons plus Force New Issue. Closed Candidates get reopen wording. Append posts a templated comment with the verbatim quote and Slack permalink. No Clarification on this path.
5. No Candidates and required fields empty: one Clarification message in the thread with up to three LLM-written questions and, when the LLM flags it, a screenshot request. Anyone in the thread may answer. Ends on Done, Skip, or a 30-minute timer. Unanswered questions are logged. Re-structure, retrieve once more.
6. Confirmation modal with editable fields and, for multi-Binding channels, a repo dropdown pre-selected to the LLM's choice; anyone in the channel may confirm. Code renders the Template, quotes the Report verbatim, adds reporter display name, channel, and permalink, embeds Attachments from the `swatter-assets` orphan branch, and marks empty fields as not provided.
7. Filing or appending creates a Subscription. Polling catches close, reopen, edits, and Issues filed outside Slack, and keeps the index current. Close notifies by DM and thread reply, with distinct wording for completed versus not planned. Reopen replies in the thread.

## Storage and evidence

- One SQLite file: Bindings, Subscriptions, pending Drafts, the Issue index with embedding blobs, and every LLM prompt, response, validation failure, and judge verdict.
- A golden set of Reports with expected dedup verdicts and a replay script reporting precision and recall, so model swaps are measured.

## Assumptions

- Unconfirmed Drafts never auto-file; their buttons keep working indefinitely.
- Labels and repo descriptions are fetched at Draft time so they are always current. A repo's GitHub description is the only routing hint the LLM gets, so adopters should write a good one.
- Recommended minimum local model sizes are documented in the README, not enforced.
- Private-repo inline rendering of Attachments via blob URLs is to be verified during the build.

## Slack scopes

`app_mentions:read`, `chat:write`, `im:write`, `commands`, `channels:history`, `channels:read`, `groups:history`, `groups:read`, `files:read`, `users:read`, plus Socket Mode with an app-level token. The two `:read` scopes exist only to print the channel name in the Issue footer; without them the footer shows the channel id.

## Cost

Under $1 a month in LLM spend at under 100 Reports a month, $0 with Ollama. Hosting is $0 on existing hardware or about $5 on a small VPS.

## Names

- GitHub repository: `swatter-bot`
- Slack app and bot user: Swatter, mentioned as `@swatter`
- Slash command: `/swatter`
- GitHub App slug: `swatter` (adopters suffix their own, since app names are global)
- Bot identity on Issues: `swatter[bot]` when using the GitHub App
- Python package: `swatter`
- Attachments branch: `swatter-assets`
