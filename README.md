# Swatter

Swatter turns bug reports made in Slack into well-formed GitHub Issues, checks each one against
existing Issues before filing, and tells the reporter when the Issue is closed or reopened.

- Mention `@swatter` or use the **File as bug** message shortcut.
- The LLM fills a template; code renders it. Every Issue looks the same.
- Duplicates are found with hybrid search (keyword and embeddings) and confirmed by the LLM.
- Missing details trigger at most one clarifying message with up to three questions.
- No inbound traffic: Slack Socket Mode plus GitHub polling. Runs on a VPS, a home server, or a laptop.
- Any OpenAI-compatible LLM: Anthropic, OpenAI, OpenRouter, Groq, or a local Ollama model.

Design and vocabulary: [docs/DESIGN.md](docs/DESIGN.md), [CONTEXT.md](CONTEXT.md), [docs/adr](docs/adr).

## Quick start

1. **Slack app.** At <https://api.slack.com/apps> choose *From a manifest* and paste
   `manifests/slack-app.yaml`. Generate an app-level token with `connections:write`, then install
   the app to your workspace. Note the bot token and the app-level token.
2. **GitHub.** Create a fine-grained PAT with Issues, Contents, and Metadata on the repos you want,
   or create a GitHub App from `manifests/github-app.json` (see below).
3. **LLM.** Pick a provider and note its OpenAI-compatible base URL, key, and model.
4. Copy `.env.example` to `.env` and fill it in.
5. Run it:

   ```sh
   docker compose up -d          # or, without Docker:
   uv sync && uv run swatter run
   ```

6. In Slack, invite the bot to a channel and run `/swatter connect owner/repo`.

`uv run swatter health` checks Slack, GitHub, and the database.

## GitHub App instead of a PAT

A GitHub App files Issues as `swatter[bot]` and installs on many repos at once. Open
<https://github.com/settings/apps/new>, fill in the fields from `manifests/github-app.json`
(webhooks off, permissions Issues write, Contents write, Metadata read), generate a private key,
install the app on your repos, and set the three `GITHUB_APP_*` variables instead of `GITHUB_TOKEN`.
App names are global on GitHub, so pick a suffix if `swatter` is taken.

## Local models

Both the LLM and the embeddings can run on Ollama. Point `LLM_BASE_URL` and `EMBEDDING_BASE_URL`
at it. The judge task works well on 7B models; structuring is more demanding, so prefer 8B or
larger with a recent instruction-tuned model. The Pydantic validate-and-retry loop catches most
formatting slips from smaller models.

## Commands

| Command | What it does |
|---|---|
| `swatter run` | Start the bot: Slack socket connection plus the GitHub poller |
| `swatter health` | Check Slack auth, GitHub auth, and the database |
| `swatter sync [owner/repo]` | Full re-index of a bound repo's Issues |
| `swatter eval` | Replay `eval/golden.jsonl` and report dedup precision and recall |
