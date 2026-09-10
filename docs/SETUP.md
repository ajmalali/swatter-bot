# Running Swatter locally

Ten minutes from nothing to a filed Issue. You need Python 3.12 and [uv](https://docs.astral.sh/uv/),
or Docker.

## 1. Slack app

1. Open <https://api.slack.com/apps>, **Create New App**, **From a manifest**, pick your workspace,
   paste `manifests/slack-app.yaml`, create.
2. **Basic Information** → **App-Level Tokens** → generate one with scope `connections:write`.
   Copy it: this is `SLACK_APP_TOKEN` (`xapp-...`).
3. **Install App** → install to workspace. Copy the **Bot User OAuth Token**: this is
   `SLACK_BOT_TOKEN` (`xoxb-...`).

## 2. GitHub token

<https://github.com/settings/personal-access-tokens> → **Generate new token** (fine-grained).
Choose the repositories Swatter may file into, and grant:

| Permission | Access |
|---|---|
| Issues | Read and write |
| Contents | Read and write (screenshots are committed to a branch) |
| Metadata | Read |

Copy it: this is `GITHUB_TOKEN`. A GitHub App works too; see the README.

## 3. LLM

Any OpenAI-compatible endpoint. The defaults in `.env.example` are Anthropic's; for OpenAI,
OpenRouter, or a local Ollama, use the commented lines there. Embeddings run locally by default and
need no key.

## 4. Configure and run

```sh
git clone https://github.com/ajmalali/swatter-bot && cd swatter-bot
cp .env.example .env        # fill in the three tokens and the LLM key
uv sync
uv run swatter health --llm # every line should say ok
uv run swatter run
```

Or with Docker: `docker compose up -d` after filling `.env`.

## 5. Connect a channel

In Slack:

1. Invite the bot to a channel: `/invite @Swatter`.
2. `/swatter connect owner/repo`. Repeat for a second repo if the channel reports bugs for several;
   Swatter then picks the repo per report from the repos' GitHub descriptions.
3. Post a bug and mention the bot in it, or reply `@Swatter` under an existing message, or use the
   **File as bug** message shortcut.
4. `@Swatter help` (or `/swatter help`) prints what the bot does and which repos this channel
   files into — the fastest thing to point a new reporter at.

The first poll indexes the repo's open Issues, so duplicate detection works from the first report.

## Useful commands

| Command | Purpose |
|---|---|
| `uv run swatter health --llm` | Check Slack, GitHub, database, and the LLM |
| `uv run swatter sync [owner/repo]` | Full re-index of a repo's Issues, and prune any GitHub no longer has |
| `uv run swatter eval` | Replay `eval/golden.jsonl`, print dedup precision and recall |
| `uv run pytest` | Run the tests |

## If something fails

- `health` says Slack FAIL: the bot token is wrong or the app is not installed.
- `/swatter connect` says it cannot reach the repo: the PAT does not cover that repo, or the name is
  not exactly `owner/repo` as in the GitHub URL.
- The bot never answers a mention: it is not running, or it is not in that channel.
- The Issue footer shows a channel id instead of its name: reinstall the app after updating the
  manifest, so the `channels:read` scope is granted.
