Decide whether a new bug report describes the same underlying bug as an existing GitHub issue.

Rules:
- "Same bug" means fixing the existing issue would also fix the new report. Similar area or component alone is not enough.
- Different symptoms of one root cause count as the same bug only when the report makes that link clear.
- A new report can match a closed issue; that indicates a possible regression and still counts as the same bug.
- Give a one-sentence reason a developer could check.
- Return a single JSON object matching the schema. No prose before or after it.
