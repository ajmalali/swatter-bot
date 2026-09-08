# Zero inbound traffic: Slack Socket Mode and GitHub polling

The bot must run anywhere an adopter chooses, including a local server behind NAT with no public URL. We therefore use Slack Socket Mode for events and interactivity, and poll GitHub every two minutes using the `since` parameter, instead of exposing HTTP endpoints for Slack events and GitHub webhooks. This removes ngrok, TLS, webhook secrets, per-repo webhook setup, retry idempotency, and the web server entirely. The cost is up to two minutes of latency on resolution notices, which is invisible for this tool.

## Considered Options

- FastAPI endpoints for Slack events and GitHub webhooks: instant, but needs a public HTTPS URL and per-repo webhook configuration.
- Socket Mode for Slack plus webhooks for GitHub: still needs a public URL for GitHub.
- Socket Mode plus polling (chosen).
