# Swatter

A self-hosted bot that turns bug reports made in Slack into well-formed GitHub issues, checks them against existing issues before filing, and tells the reporter when the issue is resolved.

## Language

**Report**:
What a person said in Slack about a bug: the message and its thread. It is quoted verbatim in the Issue and never rewritten.
_Avoid_: Bug, complaint, ticket

**Draft**:
The structured form of a Report before anything is filed. It can be edited, clarified, cancelled, appended to an Issue, or filed as a new Issue.
_Avoid_: Parsed issue, structured report

**Issue**:
The GitHub-side record that the bot files, comments on, and watches for close and reopen.
_Avoid_: Ticket, bug, GitHub thread

**Candidate**:
An existing Issue that retrieval surfaced as a possible duplicate of a Draft. Only a suggestion until the judge agrees and the reporter picks it.
_Avoid_: Match, duplicate, hit

**Binding**:
The rule that a Slack channel may file into a given repo. A channel can have several; together they define which repos get indexed and which the LLM may choose between.
_Avoid_: Mapping, connection, config

**Subscription**:
The link between a Slack user and an Issue that says the user is told when it closes or reopens. Filing creates one; appending creates another on the same Issue.
_Avoid_: Mapping, watcher, reporter record

**Template**:
The layout every Issue body follows. The repo's own GitHub issue template when it has one, otherwise Swatter's default.
_Avoid_: Format, schema, layout

**Clarification**:
The bounded exchange, at most three questions in one message, in which the bot asks the reporter for what a Draft is missing. Unanswered questions are ignored and logged.
_Avoid_: Follow-up, interview, chat

**Attachment**:
A screenshot or file from a Report, copied into the target repo so the Issue can embed it inline.
_Avoid_: Screenshot, upload, asset
