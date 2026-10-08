# Knowledge Bases

A **Knowledge Base** is a set of your documents, split into passages and indexed so a flow can
find the ones that answer a question. A [Knowledge Base search](steps/knowledge_search.md) step
gives those passages to an AI Model (or an Agent) to answer from, numbered so the answer can cite
them, and the run panel and chat show the sources behind each `[1]`.

![The Knowledge page: documents and a test search](images/knowledge.png)

## Making one

Open **Knowledge** in the top bar, then **New Knowledge Base**:

| Setting | What it does |
|---|---|
| Name, What's in it | For people; the description also helps when an agent searches it. |
| Embedding model | Turns text into numbers that capture its meaning. Pick it once: changing it means building the Knowledge Base again. **The best one available here** picks OpenAI `text-embedding-3-small` when an OpenAI key is set, else Ollama `nomic-embed-text` when `OLLAMA_HOST` is set, else **Keywords**. Others: OpenAI large, Ollama (`nomic-embed-text`, `mxbai-embed-large`, local), Google Gemini, Mistral, Amazon Titan, Azure OpenAI. **Keywords** needs no model or key and is good for trying things out; it matches words, not meaning. |
| Chunk size, Overlap *(More options)* | Passages of about this many characters (default 1,000), overlapping a little (default 150) so nothing is cut in half. |

## Adding documents

- **Files**: drop PDF, Word (`.docx`), HTML, Markdown, CSV, text, JSON or YAML files, up to
  50 MB each. Text files may be UTF-8, UTF-16 or Windows-1252 (Latin-1).
- **Web pages**: one address per line; the page is fetched and its readable text kept. Pages
  over 50 MB are not read.
- **Text**: paste it with a title.

Each document is read in the background: *queued*, *processing*, then *ready* with its number of
passages, or *error* with a reason you can act on ("manual.pdf has no text to read (it may be scanned
images)"). If the server restarts while reading a document, the document says so; add it again. Open a document to see its passages. Where a passage came from is kept with it: the
document, the PDF **page**, and the Markdown or Word **heading** it sits under.

**Chunk preview** (on a Knowledge Base's page) splits a file or page with a chunk size and
overlap you choose, without saving anything, so you can see what the AI will get.

## Searching

**Try a search** on a Knowledge Base's page shows what a flow would find. In the step, **Match**
picks how:

- **Meaning and words** (default, "hybrid"): both searches below, merged with reciprocal rank
  fusion, so a passage that matches well either way comes first.
- **Meaning**: embeddings; finds passages that say the same thing in other words.
- **Words**: full-text search; finds exact words, names and codes.

**Skip weak matches below** drops passages that were found only by meaning and aren't similar
enough, so an off-topic question finds nothing (and a Decision can hand it to a person) instead
of something vaguely related. **Re-rank with** asks an AI model to put the most useful passages
first.

## Citations

The search step saves two fields:

- `context`: the passages as text, numbered: `[1] Returns and refunds › Refunds (returns.md)`
  then the passage. Put it in an Instructions step and ask the model to cite like `[1]`.
- `sources`: the same passages as a list (title, source, page, heading, text, score, how it
  matched). Return it from the flow and the run panel and chat link each `[n]` in the answer to
  its source card.

The **Support bot over docs** template puts this together: a chat that searches the sample help
centre, answers with citations, and hands questions the help centre doesn't cover to a person.

![The Support bot answering with citations](images/support-bot.png)

## Where it is stored

Knowledge Bases live in their own tables (`kb_bases`, `kb_documents`, `kb_chunks`) in the same
database as runs, or the one in `EASYCHAIN_KNOWLEDGE_URL`:

| Database | Meaning search | Words search |
|---|---|---|
| Postgres with **pgvector** | a `vector` column with an HNSW index per Knowledge Base (see below) | `tsvector` full-text search |
| Postgres without pgvector | embeddings stored as bytes, compared with numpy | `tsvector` full-text search |
| SQLite (local) | embeddings stored as bytes, compared with numpy | FTS5 |

Uploaded files are also kept under the Easy Chain data folder (`knowledge/`). Docker Compose
uses the `pgvector/pgvector:pg16` image, so pgvector is there; on your own Postgres, run
`CREATE EXTENSION vector` once (or let Easy Chain do it, if its user may).

pgvector's HNSW index takes embeddings of up to 2,000 numbers. Bigger ones, such as the 3,072
of OpenAI `text-embedding-3-large` and Google Gemini, are indexed at half precision
(`halfvec`, pgvector 0.7 or later), which goes up to 4,000. Where no index can be made, search
still works: it compares every passage, which is slower on large Knowledge Bases.

## In exported code

The exported project includes the search functions (the same code Easy Chain runs) and reads
the Knowledge Base from `EASYCHAIN_KNOWLEDGE_URL`, or `EASYCHAIN_DATABASE_URL`, or the local
SQLite file. Point it at the database that holds your Knowledge Bases, set the embedding
provider's key, and it searches on its own.

## The API

| | |
|---|---|
| `GET /api/knowledge`, `POST /api/knowledge` | List and create Knowledge Bases. |
| `GET`, `PATCH`, `DELETE /api/knowledge/{id}` | One Knowledge Base with its documents. |
| `POST /api/knowledge/{id}/files` (multipart), `/urls`, `/text` | Add documents; they are read in the background. |
| `GET /api/knowledge/{id}/documents/{doc}/chunks`, `DELETE …/documents/{doc}` | A document's passages; remove a document. |
| `POST /api/knowledge/{id}/search` | `{"query": "…", "top_k": 5, "mode": "hybrid"}` |
| `POST /api/knowledge-preview` | Chunk preview of a file or URL, nothing saved. |

## Memory is different

A Knowledge Base holds documents everyone shares. For facts about one user ("prefers
decaf", "lives in Lisbon") that last across conversations, use the [Memory](steps/memory.md)
step or the Agent's **Long-term memory** add-on.
