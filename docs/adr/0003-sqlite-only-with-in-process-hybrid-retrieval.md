# SQLite is the only datastore, including the duplicate index

Bindings, Subscriptions, pending Drafts, the Issue index, embedding vectors, and LLM logs all live in one SQLite file. Duplicate retrieval is hybrid: FTS5 keyword search (built into SQLite) and cosine similarity over embedding blobs computed in numpy, merged with reciprocal rank fusion, top three handed to the LLM judge. We chose this over ChromaDB, Pinecone, or Postgres with pgvector because a repo has at most a few thousand open issues, brute-force cosine over that is milliseconds, and a second datastore is a second thing for every adopter to run and back up.

## Consequences

- Backup is copying one file.
- If a repo ever exceeds tens of thousands of indexed issues, retrieval should move to a vector extension; nothing else needs to change.
