# Roadmap

Where Swatter goes after the current build. Each entry names what exists today, because that is
what makes the gap concrete; vocabulary is in [CONTEXT.md](../CONTEXT.md).

Nothing here is scheduled. The order below is the order the ideas were raised, not a priority.

## 1. Knowledge base

**Today:** retrieval searches one thing — the `issues` table, open Issues plus those closed within
the lookback window (ADR 0003). Every Report therefore ends in one of two places: appended to an
existing Issue, or filed as a new one.

**The gap:** plenty of reports are neither. They are a known limitation, a config mistake, a
workaround already written down in a runbook, or a question whose answer lives in the repo's docs.
Swatter files those as bugs because a bug is the only thing it knows how to produce.

**The shape of it:** index more than Issues — repo Markdown, a curated FAQ, the resolutions of past
Issues — into the same hybrid index, and give the pipeline a third outcome next to Append and File
new: *this is already answered, here is the answer*. Open questions: a separate table or a `source`
column on `issues`; who curates the FAQ and how it stays current; how a documentation hit is
presented when it has no Issue number to append to; whether the existing judge decides this or a
new narrow job does.

## 2. Better evals

**Today:** `swatter eval` replays `eval/golden.jsonl` through structuring, retrieval, and the judge,
and reports one pair of numbers — dedup precision and recall. The golden set is hand-written.

**The gap:** three of the four LLM jobs have no score. Routing accuracy, field quality from
structuring, and whether a Clarification asked the right question are all invisible; a regression
shows up as a worse Issue, noticed by a person, not as a number that moved. There is also no cost
or latency tracking, so "this model is cheaper" is currently an unmeasured claim.

**The shape of it:** per-job eval sets rather than one end-to-end set; field-level scoring for
structuring; routing accuracy for multi-Binding channels; the judge scored in both directions,
since a false *same bug* is much worse than a false *new bug*. The raw material already exists —
every prompt, response, and validation failure is in `llm_log`, so eval cases can be mined from
real traffic instead of written by hand. Then a threshold in CI, so a model swap has to pass.

## 3. Agent routing

**Today:** when a channel has more than one Binding, one LLM call picks the repo from the repos'
GitHub descriptions and nothing else (`structuring.route_report`). DESIGN.md says as much: the
description is the only hint, so adopters are told to write a good one.

**The gap:** a description is a thin signal, and the repo is the only routing decision made. Which
component, which team, how urgent, who should look — all of that is left to whoever triages the
Issue afterwards.

**The shape of it:** route on more than prose — the repo's own labels and past Issues, CODEOWNERS,
the component names that appear in the Report. Then route further than the repo: a component
label, a suggested assignee or team, a priority the reporter's words actually support. And when
the signal is weak, ask instead of guessing — a routing question is cheaper than an Issue filed in
the wrong repo. This one needs [better evals](#2-better-evals) first: routing changes are not worth
shipping if nobody can tell whether they helped.

## 4. Agent memory

**Today:** every Report starts cold. The confirm modal is where people fix what the LLM got wrong,
and those corrections go into the Issue and nowhere else — `file_draft` writes the Issue and
forgets the edit.

**The gap:** teams are consistent. The same channel calls the same subsystem by the same nickname,
labels the same class of bug the same way, and corrects the same field week after week. Swatter
relearns none of it, so the same edit gets made by hand forever.

**The shape of it:** remember per-channel and per-repo conventions, built from the edits people
actually make and the Drafts that settled — this channel's "kiosk" means that repo, this team
always labels billing bugs `billing`, this reporter's environment line is always a device name.
Storage is not the hard part; SQLite is already there. The hard parts are what is worth
remembering, how it enters a prompt without becoming an unbounded context dump, how a wrong
memory gets corrected, and how someone sees what the bot thinks it knows about their channel.

## 5. Better filing, appending, and updating

**Today:** Append posts one templated comment with the verbatim quote and a permalink, reopening
the Issue first if it was closed. That is the whole of it. Filing is one-shot: Swatter creates the
Issue and never edits it again.

**The gap:** the second report about a bug usually knows something the first did not — another
platform, a screenshot, a reliable set of steps. All of that lands as a comment nobody folds back
into the Issue. Duplicates also carry a signal about severity that goes unused, and a reporter who
spots a mistake in a filed Issue has no way to fix it from the thread.

**The shape of it:** an Append that can improve the Issue and not just append to it — copy new
attachments in, fill a field the original left empty, count the reports. Then updates after
filing: correct an Issue from its Slack thread, adjust labels or priority as duplicates
accumulate. The constraint is the one DESIGN.md already sets — a Report is quoted verbatim and
never rewritten — so anything here edits Swatter's own rendering, never a person's words.

## Smaller, already scoped

- **Reconciliation on a timer.** Deleted and transferred Issues are dropped on sight and by
  `swatter sync`, but the two-minute poll never sweeps, so a deleted Issue can sit in the index
  until someone runs a sync. Picking it up means a cadence and a setting.
  See `.flux/plans/01-deleted-issues-and-help.md`.
- **Subscriptions to a pruned Issue** are left in place and are inert. Cleaning them up means
  deciding what, if anything, to tell the people who were watching.
