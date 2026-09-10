# Swatter

Swatter turns bug reports made in Slack into well-formed GitHub Issues, checks each one against
existing Issues before filing, and tells the reporter when the Issue is closed or reopened.

- Mention `@swatter` or use the **File as bug** message shortcut.
  `@swatter help` says what it does and where this channel files.
- The LLM fills a template; code renders it. Every Issue looks the same.
- Duplicates are found with hybrid search (keyword and embeddings) and confirmed by the LLM.
- Missing details trigger at most one clarifying message with up to three questions.
- No inbound traffic: Slack Socket Mode plus GitHub polling. Runs on a VPS, a home server, or a laptop.
- Any OpenAI-compatible LLM: Anthropic, OpenAI, OpenRouter, Groq, or a local Ollama model.

How it works: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Design and vocabulary: [docs/DESIGN.md](docs/DESIGN.md), [CONTEXT.md](CONTEXT.md), [docs/adr](docs/adr).

## Quick start

1. Create the Slack app from `manifests/slack-app.yaml`, a fine-grained GitHub PAT, and pick an
   LLM provider.
2. `cp .env.example .env`, fill in the tokens.
3. `uv sync && uv run swatter run`, or `docker compose up -d`.
4. In Slack: `/invite @Swatter`, then `/swatter connect owner/repo`.

Step by step: [docs/SETUP.md](docs/SETUP.md). Hosting on a Pi, a server, or a VPS:
[docs/DEPLOY.md](docs/DEPLOY.md).

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
| `swatter sync [owner/repo]` | Full re-index of a bound repo's Issues, and drop any GitHub no longer has |
| `swatter eval` | Replay `eval/golden.jsonl` and report dedup precision and recall |

## In Slack

| Command | What it does |
|---|---|
| `@swatter` in a thread | File the thread's bug report |
| `@swatter help` | What Swatter does and where this channel files |
| `/swatter connect owner/repo [template]` | Let this channel file into that repo |
| `/swatter disconnect owner/repo` | Stop filing there |
| `/swatter list` | Show this channel's repos |

## Evaluation

`eval/golden.jsonl` has one JSON object per line: `repo`, `report`, optional `thread`, and
`expected` (the Issue number the report duplicates, or `null` for a new bug). Copy
`eval/golden.example.jsonl` to start. Run `swatter sync owner/repo` first so the Issues are
indexed, then `swatter eval`. Structuring, retrieval, and the judge run for real; the numbers are
comparable across model swaps because prompts and thresholds never change.

## Roadmap

Where this is going next: [docs/ROADMAP.md](docs/ROADMAP.md).

## License

MIT. See [LICENSE](LICENSE).
