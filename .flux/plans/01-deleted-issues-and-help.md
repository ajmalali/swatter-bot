---
phase: 01-deleted-issues-and-help
status: done
files:
  - src/swatter/github.py
  - src/swatter/store.py
  - src/swatter/poller.py
  - src/swatter/pipeline.py
  - src/swatter/cli.py
  - src/swatter/slack/handlers.py
  - src/swatter/slack/blocks.py
  - tests/fakes.py
  - tests/test_poller.py
  - tests/test_pipeline.py
  - tests/test_handlers.py
  - tests/test_blocks.py
  - README.md
  - docs/DESIGN.md
---

## objective
Issues GitHub no longer has stop being indexed and stop being offered as Candidates, and
`@swatter help` explains what the bot does instead of filing a bug report about the word "help".

## context
GitHub gives no signal when an Issue is deleted or transferred: it simply stops appearing in
`list_issues_since`, with no `updated_at` bump and no tombstone. `poller.poll_repo` only ever
upserts, so the row lives on forever. Retrieval (`searchable_issue_filter`) then surfaces it as a
Candidate with a dead `html_url`, and pressing *Append* calls `github.comment` on a 404 — after
`handlers.on_action` has already stripped the message's buttons, leaving the reporter with an
active Draft and nothing to press.

`handlers.on_mention` starts a Draft for every mention, so there is no way to ask the bot what it
does; `/swatter` with an unknown verb prints a one-line usage string that mentions only the three
binding subcommands.

## acceptance criteria
AC-1 — Given an indexed Issue that GitHub now 404s, when a Report retrieves it as a Candidate,
then the row is deleted from `issues` and `issues_fts`, the judge never sees it, and it is not
shown to the reporter.

AC-2 — Given a Candidate that GitHub 404s at the moment its Append button is pressed, when the
button is pressed, then no comment is attempted, the Draft stays in `awaiting_choice`, the reporter
is told the Issue is gone, and the original message keeps its buttons so *File as new issue* still
works.

AC-3 — Given a repo whose index holds Issues GitHub 404s, when `swatter sync <repo>` runs, then
exactly those rows are gone and the printed line reports how many were pruned; a row whose
existence check raised (rate limit, 5xx) is kept.

AC-4 — Given `@swatter help` or `/swatter help`, when it is sent, then no Draft row is created and
the reply names both ways to file a Report, the steps that follow, this channel's repos, and the
slash commands.

## tasks

### T1 — Detect and prune Issues GitHub no longer has
files: src/swatter/github.py, src/swatter/store.py, src/swatter/poller.py, src/swatter/cli.py,
tests/fakes.py, tests/test_poller.py, docs/DESIGN.md
do:
- `GitHubService.issue_exists(repo, number) -> bool`: `self._repo(repo).get_issue(number)`;
  return False on `UnknownObjectException`, and False when the returned issue's `html_url` no
  longer contains `f"/{repo}/issues/"` (GitHub redirects a transferred Issue to its new repo).
  Let every other `GithubException` propagate — a rate limit or a 5xx must never read as a
  deletion.
- `store.delete_issue(db, repo, number) -> bool`: `DELETE FROM issues WHERE repo=? AND number=?`
  inside `db.tx()`, returning `rowcount > 0`. DELETE, not REPLACE, so the `issues_ad` trigger
  keeps FTS in sync.
- `poller.reconcile_repo(ctx, repo, seen: set[int]) -> int`: select the index rows for `repo`
  inside the searchable window (`store.searchable_issue_filter`) whose number is not in `seen`;
  for each — capped at a new module constant `MAX_PRUNE_CHECKS = 200`, logging the overflow —
  call `ctx.github.issue_exists` and `store.delete_issue` only when it returns False; log and keep
  the row on any exception. Return how many were deleted.
- `poll_repo` calls it only when `full=True`, after the upsert loop, passing the fetched numbers.
  The incremental path is untouched.
- `cli._sync` prints the pruned count alongside `fetched` / `index holds` / `cursor`.
- `tests/fakes.FakeGitHub`: add `self.deleted: set[tuple[str, int]]` and
  `issue_exists(repo, number)` returning `(repo, number) not in self.deleted`.
- `docs/DESIGN.md` step 7: one sentence that a full sync prunes Issues GitHub no longer has,
  since polling alone cannot see a deletion.
verify: `uv run pytest tests/test_poller.py -q` with two new tests — (a) two indexed Issues, one
marked deleted in FakeGitHub, `poll_repo(full=True)` leaves one row and no FTS hit for the pruned
title (`SELECT count(*) FROM issues_fts WHERE issues_fts MATCH ?`); (b) `issue_exists` raising
leaves the row in place.
done: AC-3 when `swatter sync` on a repo with a deleted Issue removes that row alone and prints
the count.

### T2 — Stop referencing Issues that are gone
files: src/swatter/pipeline.py, src/swatter/slack/handlers.py, tests/test_pipeline.py,
tests/test_handlers.py
do:
- `pipeline._verify_candidates(ctx, found)`, called in `_decide` between `find_candidates` and
  `judge_candidates`: drop a Candidate whose `issue_exists` is False and `store.delete_issue` it;
  keep it on any exception (fail open, logged). Costs at most `swatter_max_candidates` (default 3)
  GitHub GETs per Report — accepted.
- `append_draft`: call `ctx.github.issue_exists(repo, number)` before anything else that writes —
  ahead of the reopen and the comment. On False, `store.delete_issue` and raise
  `PipelineError("That issue no longer exists on GitHub, so I dropped it from my index. Use *File
  as new issue* on the message above.")`, leaving the Draft state unchanged.
- `handlers.on_action`, append/reopen branch only: call `pipeline.append_draft` first and `settle`
  only after it returns, so a failure leaves the buttons in place. This is the ordering DESIGN.md
  already promises ("their buttons keep working indefinitely"); the cost is a few seconds of no
  visible feedback after the click, which the other branches keep paying differently.
verify: `uv run pytest tests/test_pipeline.py tests/test_handlers.py -q` with new tests —
(a) a deleted Candidate is absent from the blocks shown and from `issues`, and FakeLLM records no
judge call for it; (b) pressing Append on a deleted Issue records no entry in `FakeGitHub.comments`
or `.reopened`, leaves the Draft in `awaiting_choice`, and does not call `respond` with
`replace_original=True`.
done: AC-1 and AC-2.

### T3 — Answer `@swatter help` and `/swatter help`
files: src/swatter/slack/blocks.py, src/swatter/slack/handlers.py, tests/test_blocks.py,
tests/test_handlers.py, README.md
do:
- `blocks.help_text(bindings: list[Binding], default_repo: str | None, max_questions: int) -> str`,
  one mrkdwn string used by both surfaces, covering: what Swatter does; the two triggers (mention
  in a thread, *File as bug* message shortcut) and that it reads the whole thread; what follows
  (duplicate Candidates → at most `max_questions` questions in one message answered in-thread then
  *Done* → the confirmation modal → DM and thread reply on close or reopen); this channel's repos,
  or the default repo, or `/swatter connect owner/repo` when there are none; and the four
  slash commands.
- `handlers.on_mention`: a module-level `_HELP = re.compile(r"^\s*(?:<@[^>]+>\s*)*(?:help|halp|\?|usage|commands)[\s!.?]*$", re.I)` matched against `event.get("text", "")`. Whole-string match only,
  so "help, the login page 500s" still files a bug. On a match, post `help_text` in the thread and
  return before `pipeline.start_draft`.
- `_run_command`: a `help` verb and the unknown-verb fallback both return `help_text`; the no-arg
  default stays `list`.
- `pipeline.start_draft`'s no-binding message gains a trailing pointer to `@swatter help`.
- README: one line under the usage bullets.
verify: `uv run pytest tests/test_handlers.py tests/test_blocks.py -q` with new tests — `@swatter
help` writes no `drafts` row and posts text naming "File as bug" and the bound repo; `@swatter the
login page 500s` still reaches `start_draft`; `/swatter help` returns the same string as the
mention.
done: AC-4.

## boundaries
do not change: `notify.py` and the `subscriptions` table — a Subscription for a pruned Issue is
inert (the poller never sees that Issue again), and deleting it drags in the question of what to
tell the subscriber. The incremental poll path: no full listing per cycle. The FTS triggers and
the `ON CONFLICT DO UPDATE` rule in `store.upsert_issue`.
out of scope: reconciling on a timer — `swatter sync` is the sweep for this phase; the judge's
prompts and thresholds; re-ordering settle for the `cancel`, `force_new`, and `clarify_*` branches;
following a transferred Issue to its new repo (we drop the row, we do not re-point the Candidate).

## verification
`uv run ruff check . && uv run pytest -q` green (`flux check` runs both), plus what check cannot
see: in a live workspace, delete a test Issue on GitHub, run `swatter sync owner/repo`, confirm the
printed prune count and that a Report which used to surface it no longer offers it; and send
`@swatter help` in a channel with two Bindings and confirm both repos are listed and no Draft
appears in the thread.

## outcome — 2026-09-09
shipped: An Issue GitHub no longer serves is dropped from the index two ways — on sight
(`pipeline._verify_candidates` before the judge, `pipeline._require_issue` before an Append's
reopen or comment) and in bulk (`poller.reconcile_repo`, run by `swatter sync`). Detection is
`github.issue_exists`: a 404 or a redirected `html_url` means gone, every other GithubException
propagates so an outage is never read as a deletion, and every caller fails open. A failed Append
now leaves the message's buttons in place. `@swatter help` and `/swatter help` (and any unknown
verb) answer with one `blocks.help_text` naming both triggers, the four steps that follow, the
channel's repos, and the commands. 66 tests, 14 of them new; `flux check` green.

deviated:
- `poll_repo` was split into `_poll` + `sync_repo() -> (touched, pruned)`. The plan had `poll_repo`
  reconciling directly, but the CLI needs the prune count and `poll_repo -> int` is asserted by
  existing tests; `run_once`'s incremental path is untouched either way.
- Reconciliation also runs when the full fetch returns nothing. The plan put it after the upsert
  loop, which `if not issues: return 0` skips — exactly the repo whose every Issue was deleted.
- `FakeGitHub` gained `exists_error`, and `list_issues_since` now hides `deleted` Issues. The plan
  specified only the `deleted` set; the first is needed for the "GitHub cannot answer" tests the
  plan itself asks for, the second models GitHub honestly.
- The Append refusal names the Issue (`o/web#7 no longer exists...`) instead of the plan's generic
  "That issue".
- Two helpers rather than inline logic: `pipeline._require_issue`, `handlers._help`.
- Tests reach the registered Slack handlers through `App._listeners` + `ack_function.__name__`,
  a seam the plan did not anticipate; without it AC-2's "keeps its buttons" clause was untestable.
- `tests/test_poller.py` also covers `cli._sync`'s printed line (with `_context` monkeypatched),
  which the plan left to a live check.

deferred:
- Reconciliation on a timer. `swatter sync` is still the only sweep, so a deleted Issue can sit in
  the index until someone runs it — the on-sight checks are what keep it from being referenced.
  Picking it up means a cadence (every Nth cycle, or a daily slot) and a setting to control it.
- Subscriptions to a pruned Issue are left in place, inert. Untouched per the plan's boundary.
- A transferred Issue is dropped, not followed to its new repo.
- The two live checks in `## verification` still want a real workspace: delete an Issue on GitHub
  and watch `swatter sync` report it, and read `@swatter help` in a channel with two Bindings.
