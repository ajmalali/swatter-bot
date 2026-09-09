# Evaluation set

`golden.jsonl` holds Reports with the Issue number they should match, or `null` when they are new.
The Issues referenced must exist in the index for the named repo. `swatter eval` replays each Report
through structuring, retrieval, and the judge, then prints precision and recall for the dedup verdicts.

Add a row whenever the bot gets one wrong in real use. Run the eval before and after changing the
model, the prompts, or the retrieval settings.

Row format: `{"repo": "owner/repo", "report": "...", "thread": "...", "expected": 42}`.
See `golden.example.jsonl`.
