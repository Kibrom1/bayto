# Per-agent context with retrieval (RAG)

Status: first part built (2026-10-23): per-seat selection of attachment passages. It differs from the plan below in one way: chunks are made and ranked with BM25 in the orchestrator when the prompt is built (`retrieval.py`, `attachment_prompt.py`), not stored in an `attachment_chunk` table with Postgres full-text search. Attachments are capped at 100,000 characters, so this is cheap, needs no migration or backfill, and stays correct when an attachment changes. A table becomes worth it if attachments grow far beyond the cap or embeddings are added. Small attachments (under 4,000 characters) are always sent whole; with no query or no keyword match the old behaviour (start of each attachment) applies. Role visibility per attachment is built too (2026-10-23, see below). Not built: "context used" notes, embeddings. Written 2026-10-23.

## Problem

Attachments (file, folder, web page) are flattened to text when attached (`context_extract.py`, 100,000 characters kept per attachment). `attachment_prompt.py` then puts a share of one fixed character budget in front of **every** seat, identical for all of them, and cuts what does not fit ("[…cut to fit]", "Not shown"). So:

- A big folder or PDF is seen only from the start. Later parts are invisible to every agent.
- The lawyer and the architect get the same text, whatever each is asked.
- Earlier turns reach an agent only through the rolling summary, which drops detail.

Goal: each seat gets the passages most relevant to **its role and the current question**, within the same prompt budget, with no new infrastructure to run for the pilot.

## Design in one paragraph

Split each attachment into chunks when it is attached, store them with a Postgres full-text index, and at each turn build a query from the seat's role brief, the task, and the latest messages. Rank chunks with Postgres full-text search, take the best ones up to the seat's budget, and show them as labelled excerpts (attachment name + position). Embeddings are an optional second stage, added only if keyword ranking proves too weak.

## Why keyword first

- Postgres is already required. `tsvector` + GIN needs no extension, no model download, no second store, no new network access (the orchestrator has no outbound need today beyond the Claude API and optional URL fetches).
- The corpora here are small (a few documents per task), so recall from keyword ranking is usually good. Role briefs and task text are rich in the same vocabulary as the documents.
- Embeddings cost an extra dependency (pgvector, or a hosted embeddings API that sends the user's documents to a third party). Make that a deliberate later choice.

## Data model

New table `attachment_chunk` (Alembic migration):

| column | notes |
|---|---|
| id, attachment_id (FK, cascade delete), task_id | task_id denormalised for filtering |
| ordinal | position within the attachment |
| heading | nearest heading / file path inside a folder snapshot, for labels |
| content | the chunk text, about 800–1,200 characters |
| tsv | `tsvector` generated from content (+ heading, weighted higher), GIN index |

`task_attachment.content` stays as the source of truth and as the fallback.

Chunking rules: split on headings and blank lines first, then pack to the size target with about 10% overlap; for folder snapshots, never cross a file boundary and keep the file path as the heading; code files split on top-level definitions where cheap, otherwise by lines.

Re-chunk when an attachment is added, replaced or deleted. Delete chunks with the attachment and with the task.

## Retrieval

New module `retrieval.py`, one function: `select_context(task_id, seat, view, budget) -> list[Excerpt]`.

1. Query text = seat role brief keywords + task title/brief + the last 2–3 messages addressed to or from this seat. Reduce to distinct content words, drop stop words, cap length. Build with `websearch_to_tsquery` (or OR-joined terms) so no user text is interpreted as query syntax.
2. `ts_rank_cd` over the task's chunks, best first.
3. Greedy fill up to the seat's character budget, with a per-attachment cap so one big file cannot crowd out the rest, and a floor so a small attachment is always included whole (below about 4,000 characters, send the whole thing; retrieval only kicks in when it would otherwise be cut).
4. Render in the existing `<attachment …>` format, now with `excerpt="3 of 41"` and the heading, and keep the "Not shown" line, naming what was left out so the agent can ask for it.
5. If no chunk scores, fall back to today's behaviour (head of each attachment).

Per-seat context requires `_build_context` to know who is about to speak. It already receives the view and a grant, so pass the granted seat's role brief down to `attachment_prompt`. Pipeline (Build) seats get the same treatment: the developer sees files relevant to the change, the reviewer sees the spec and the diff context.

## Optional per-agent source scoping

Separate from ranking: let a task mark an attachment as visible to **all** (default) or to **chosen roles**. This is the privacy-style control ("the contract is for the lawyer only"). It filters before ranking. UI: a role picker on each attachment. Ship after ranking works, because ranking alone already gives most of the value.

Built (2026-10-23): `task_attachment.roles` (null means everyone; migration `c9d0e1f2a3b4`). Roles are set with `roles` when attaching a file, folder or link, and changed with `PATCH /tasks/{id}/attachments/{attachment_id}` (audited as `attachment.visibility_changed`). Roles must belong to existing agents. The filter runs first in `attachment_prompt` (`visible_to`), before ranking and before the budget is shared, so a hidden attachment does not appear in a seat's excerpts or in the "Not shown" line. With no seat known only open attachments are shown. Limits to know: a restricted attachment is not announced to the team (the note would name it), and what an agent says in the shared discussion is still read by the others, so this keeps the file out of other seats' prompts but cannot stop a lawyer from quoting it aloud.

## Earlier turns and earlier sessions (phase 3)

The same machinery can index the session's own transcript and previous sessions' syntheses, so a seat can recall "what did the lawyer say about clause 7" instead of depending on the rolling summary. Index message bodies as they are mirrored; retrieve the top few per turn and label them as quotes from named speakers. Keep the rolling summary as the always-present spine.

## UI

- Attachment list shows chunk count ("41 passages") and, per attachment, who can see it.
- Room: a collapsed "Context used" note on each agent turn listing which excerpts it was given (name + position). This makes retrieval inspectable and is the main debugging tool when an agent misses something.
- Budget warning in setup changes from "too much attached, some will be cut" to "N passages; each agent gets the most relevant ones".

## Limits and risks

- **Missed passages.** Keyword ranking can miss a relevant passage with different vocabulary. Mitigations: whole-document inclusion for small attachments, the "Not shown" list, the "Context used" note, and the optional embedding stage.
- **Prompt injection.** Chunks are untrusted text exactly as attachments are today. Keep the existing `_safe()` escaping and the `<attachment>` wrapper; never let chunk text become instructions.
- **Secrets.** Folder snapshots already withhold credential-looking files; chunking runs on the snapshot, so nothing new is exposed.
- **Cost.** Retrieval is a database query per turn, no model calls. Prompt size stays at today's budget, so cost per turn does not rise.
- **Determinism.** Same query and data give the same ranking. Log the chunk ids used per turn in the audit trail (ids and scores only, not text).

## Optional embedding stage (decide later)

Add `embedding vector(N)` to `attachment_chunk` (pgvector in the Postgres image) and blend: take the top K by keyword and top K by cosine similarity, merge with reciprocal-rank fusion. Provider options: a local embedding model (no data leaves the machine, but heavier to run), or a hosted API (simple, but documents leave the machine, so make it opt-in per task). Do not start here.

## Phases and priority

| Phase | Work | Size |
|---|---|---|
| P1 | `attachment_chunk` table, chunking at attach time, backfill existing attachments | small |
| P1 | `retrieval.select_context` with Postgres full-text ranking and the whole-document floor; per-seat budget; fallback to current behaviour | medium |
| P1 | "Context used" note on turns, passage counts in the attachment list | small |
| P2 | Per-role visibility on attachments (filter before ranking) | small |
| P2 | Audit entry listing chunk ids per turn | small |
| P3 | Index session transcripts and earlier syntheses for recall | medium |
| P4 | Embedding stage (pgvector or hosted), opt-in per task | medium |

## Tests

- Chunker: boundaries, overlap, folder file boundaries, empty and huge inputs.
- Ranking: a two-document task where the lawyer's brief pulls the contract chunk and the architect's pulls the design chunk, from the same attachments.
- Floor and fallback: small attachments included whole; no-match queries fall back to the head of each attachment.
- Budget: output never exceeds the seat's budget; per-attachment cap holds.
- Safety: query text containing tsquery operators, `<`/`>` and quotes is handled; chunk text is escaped in the prompt.
- Visibility filter (P2): a role-restricted attachment never reaches another role.
- Migration: up and down, and backfill of an existing attachment.

## Open questions

1. Should an attachment's role visibility default to all (simple) or require a choice (safer)? Recommendation: all.
2. Per-seat budget: keep one total budget split across seats' needs, or raise it for seats with narrower roles? Recommendation: keep today's budget.
3. Is hosted embedding ever acceptable for this product's users (property-management and insurance documents)? Decide before P4.
