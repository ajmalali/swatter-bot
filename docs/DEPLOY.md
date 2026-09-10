# Hosting Swatter

Swatter makes only outbound connections: a WebSocket to Slack and HTTPS requests to GitHub and
the LLM. Any machine with internet access works, with no port forwarding, domain, certificate, or
firewall rule. Run exactly one instance.

## Requirements

- Linux with Docker, or Python 3.12 and `uv`. A Raspberry Pi 4 or 5 works on a **64-bit** OS.
- 1 GB RAM free, 1 GB disk. The embedding model (about 300 MB) downloads once on first start.
- A small VPS is enough. LLM spend is under $1 a month at under 100 reports a month.

## Deploy with Docker

```sh
git clone https://github.com/ajmalali/swatter-bot && cd swatter-bot
scp laptop:swatter-bot/.env .                   # secrets, never in git
scp laptop:swatter-bot/data/swatter.db data/    # optional: keep Bindings and Subscriptions
docker compose up -d
docker compose logs -f
```

`compose.yaml` mounts `./data` for the database and model cache, restarts the container after a
reboot, and runs `swatter health` as the container health check (`docker compose ps` shows it).

Moving from another machine: stop the old instance first. Two instances would both connect to
Slack and split the events between them.

## Deploy without Docker

```sh
uv sync --no-dev
uv run swatter run
```

To keep it running, a systemd unit:

```ini
# /etc/systemd/system/swatter.service
[Unit]
Description=Swatter
After=network-online.target

[Service]
WorkingDirectory=/opt/swatter-bot
ExecStart=/usr/local/bin/uv run swatter run
Restart=always
User=swatter

[Install]
WantedBy=multi-user.target
```

```sh
sudo systemctl enable --now swatter
journalctl -u swatter -f
```

## Operating

| Task | Command |
|---|---|
| Update | `git pull && docker compose up -d --build` |
| Back up | copy `data/swatter.db` |
| Check | `docker compose exec swatter uv run swatter health --llm` |
| Re-index a repo, prune deleted Issues | `docker compose exec swatter uv run swatter sync owner/repo` |
| Logs | `docker compose logs -f` |

Network drops are fine: Bolt reconnects the Slack socket on its own, and the poller resumes from
its stored cursor, so GitHub changes made while offline are still noticed.

## Local models

Point `LLM_BASE_URL` and `EMBEDDING_BASE_URL` at an Ollama server to run with no API keys. See the
README for model size guidance.
