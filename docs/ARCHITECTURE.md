# How Swatter works

One Python process, no inbound network traffic, one SQLite file. Vocabulary is in
[CONTEXT.md](../CONTEXT.md); the reasoning behind each choice is in [adr/](adr/).

## Components

```mermaid
flowchart LR
    subgraph Slack
        S[Slack workspace]
    end

    subgraph Swatter process
        direction TB
        B[Bolt app<br/>Socket Mode handler<br/>thread pool]
        P[Poller thread<br/>every 2 minutes]
        PL[pipeline.py<br/>deterministic flow]
        R[retrieval.py<br/>FTS5 + cosine + RRF]
        E[embeddings.py<br/>fastembed, in process]
        DB[(SQLite<br/>Bindings, Drafts, Issue index,<br/>Subscriptions, LLM log)]
    end

    subgraph External
        L[LLM<br/>any OpenAI-compatible endpoint]
        G[GitHub REST API]
    end

    S -- "WebSocket (app-level token):<br/>mentions, shortcuts, commands,<br/>button clicks, modal submits" --> B
    B -- "Web API (bot token):<br/>read thread, post replies,<br/>open modal, DM" --> S
    B --> PL
    PL --> R
    R --> E
    R --> DB
    PL -- "4 narrow jobs: route, structure,<br/>judge, clarify (JSON in, JSON out)" --> L
    PL -- "create issue, comment, reopen,<br/>upload attachment" --> G
    PL --> DB
    P -- "list issues updated since cursor" --> G
    P --> DB
    P --> E
    P -- "close / reopen notices" --> S
```

### The two Slack tokens

- The **app-level token** (`xapp-...`) opens one outbound WebSocket to Slack. Slack pushes every
  event the app subscribed to in its manifest down that socket: app mentions, the "File as bug"
  shortcut, the `/swatter` command, button clicks, and modal submissions. Nothing else arrives;
  Swatter does not see channel traffic beyond what the manifest asks for.
- The **bot token** (`xoxb-...`) is used the other way, for ordinary Web API calls: read a
  thread, post a reply, update a message, open a modal, send a DM.

Because the socket is outbound, the process can run behind NAT with no public URL. GitHub has no
equivalent of Socket Mode, so instead of webhooks the poller asks GitHub every two minutes for
Issues updated since a stored cursor (ADR 0001).

### Where the LLM is and is not used

The LLM never sees a raw Slack payload and never decides what happens next. Code fetches the
thread, strips the mention, decides which message is the Report, and then makes at most four
kinds of call, each returning JSON that is validated against a Pydantic schema and retried once
with the error fed back (ADR 0002, ADR 0004):

| Job | When | Input | Output |
|---|---|---|---|
| route | channel has more than one Binding | repo descriptions, Report | one repo from an enum |
| structure | every Report | Template fields, repo labels, Report, thread | field values, title, labels from an enum |
| judge | for each of up to three Candidates | Draft fields, one existing Issue | same bug yes/no, one-line reason |
| clarify | required fields empty and no Candidates | Report, missing field ids | up to N questions, screenshot flag |

Everything else is deterministic: which repos are searched, how Candidates are ranked, how many
questions are allowed, when the Clarification ends, how the Issue body is laid out, who gets
notified. Swapping the model changes the quality of the field values, never the layout or the flow.

### Where GitHub is used

Plain REST calls through PyGithub, not GitHub Actions:

- Read: repo description (routing hint), labels, issue template, Issues updated since the cursor,
  and whether one Issue still exists.
- Write: create an Issue, comment on one, reopen one, commit an image to the `swatter-assets`
  orphan branch so the Issue can embed it.

The existence check is there because deletion has no other signal. A deleted or transferred Issue
simply stops being listed, with no `updated_at` bump and no tombstone, so polling alone can never
notice it. Swatter therefore asks directly, in two places: before a Candidate is judged or shown,
and before an Append comments on one. A 404, or a redirect to another repo, drops the Issue from
the index; any other error keeps it, because a rate limit is not a deletion.

### Storage and search

One SQLite file holds everything. The Issue index is searched two ways without any external
service: an FTS5 table for keywords and an embedding vector per Issue for meaning, computed
in-process by fastembed. Results are merged with reciprocal rank fusion and the top three go to the
judge (ADR 0003). Rows leave the index only when GitHub says the Issue is gone — on sight during a
Report, or in bulk during `swatter sync`, which asks about every indexed Issue its full fetch did
not return. Every LLM prompt and response is logged, so `swatter eval` can measure a model
swap against the golden set.

## One Report, end to end

```mermaid
sequenceDiagram
    autonumber
    participant U as Reporter
    participant S as Slack
    participant W as Swatter
    participant L as LLM
    participant G as GitHub

    U->>S: "@Swatter the kiosk shows session expired"
    S-->>W: app_mention event (WebSocket)
    W->>S: conversations.replies (whole thread)
    W->>S: post "Looking at this report..."
    W->>G: repo descriptions, labels, template
    opt channel has several Bindings
        W->>L: route: which repo?
    end
    W->>L: structure: fill the Template's fields
    W->>W: FTS5 + cosine over the local index, RRF, top 3
    W->>G: does each Candidate still exist?
    Note over W,G: a deleted or transferred Issue is dropped from the index here
    loop each surviving Candidate
        W->>L: judge: same bug?
    end
    alt a Candidate is confirmed
        W->>S: Append / Reopen buttons + File as new
        U->>S: presses Append
        S-->>W: block_actions
        W->>G: comment (reopen first if closed)
        Note over W,S: buttons stay until the comment lands,<br/>so a gone Issue can still be filed as new
    else required fields empty
        W->>L: clarify: write up to 3 questions
        W->>S: questions + Done / Skip
        U->>S: replies in thread, presses Done (or 30 min pass)
        S-->>W: block_actions (or poller timer)
        W->>S: re-read thread
        W->>L: structure again
    else ready
        W->>S: "Review and file" button
    end
    U->>S: presses Review and file, edits, submits
    S-->>W: view_submission
    W->>S: fetch thread again, reporter name, permalink
    W->>G: upload screenshots to swatter-assets
    W->>G: create Issue (rendered Template)
    W->>W: save Subscription, index the new Issue
    W->>S: "Filed as owner/repo#67"
    Note over W,G: later, every 2 minutes
    W->>G: issues updated since cursor
    G-->>W: #67 closed as completed
    W->>S: DM the reporter + reply in the thread
```

## Threads inside the process

- Bolt runs each incoming event on a worker thread and must acknowledge within three seconds.
  Handlers acknowledge first, then do the slow work (LLM and GitHub calls) on the same thread.
- The poller is one daemon thread. Besides syncing the index it ends Clarifications whose timer
  ran out, so no per-Draft timers are needed.
- Both share the SQLite connection through one lock (`db.py`). Draft state lives in the database,
  never in memory, so a button pressed hours later, or after a restart, still works.
