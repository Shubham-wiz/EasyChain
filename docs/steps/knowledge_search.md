# Knowledge Base search

**Finds the passages of your documents that answer a question, ready to cite.** In the
Knowledge and memory group. LangChain term: a retriever (here hybrid vector + full-text search).

Make a Knowledge Base on the **Knowledge** page first and add documents to it (see
[Knowledge Bases](../knowledge.md)). The step saves two things: numbered passages for an AI
Model to answer from (`[1] …`, `[2] …`), and the same passages as a list of sources (title,
source, page, heading, text) that the run panel and chat show as citations. An answer that
cites `[1]` links to source 1.

## Settings

| Setting | What it does |
|---|---|
| Knowledge Base | Which documents to search. |
| Search for | The field with the question, or a chat (the latest message is used). Empty means: what the previous step saved. |
| Passages to keep | How many passages to keep (default 4). More give the AI more to go on but cost more tokens. |
| Match | **Meaning and words** (default): both searches, merged by reciprocal rank fusion. **Meaning**: passages that say the same thing in other words (embeddings). **Words**: exact words and names (full-text search). |
| Skip weak matches below *(More options)* | Passages found only by meaning must be at least this similar to the question (0 to 1). Raise it so a question the documents don't cover finds nothing, instead of something vaguely related. |
| Re-rank with *(More options)* | An AI model reads the best matches and puts the most useful first. Slower, often better. |
| Save the passages as | Numbered text for an Instructions step (default `context`). |
| Save the sources as | The list for citations (default `sources`). |

## Example

```yaml
- id: search
  type: knowledge_search
  settings:
    knowledge_base: help_centre
    query: question
    top_k: 3
    min_similarity: 0.2
- id: write_prompt
  type: instructions
  settings:
    system: |-
      Answer only from these passages and cite them like [1].

      {context}
```

A **Decision** on `sources` *is not empty* sends questions the documents don't cover somewhere
else (the Support bot template hands them to a person).

## As an agent tool

Connect it to an Agent's Tools handle and the agent can search as often as it needs, then cite
the numbered passages it gets back. Set **Search for** to a field the agent can't see yet, such as
`search_query`, and describe that field in the Flow Data panel ("What to look for in the help
centre"): it becomes the tool's argument, so the agent writes its own queries. Left on the
question, the tool always searches the question.

```yaml
- id: search
  type: knowledge_search
  description: Searches the help centre. Returns numbered passages to cite.
  settings:
    knowledge_base: help_centre
    query: search_query
```

## Errors you might see

| Message | What to do |
|---|---|
| Pick the Knowledge Base to search | Choose one, or make one on the Knowledge page. |
| This search has no question to look for | Connect it after Input, or pick the field. |
| The passages and the sources need different field names | Use the one-click fix. |
| … isn't installed on this server | The Knowledge Base's embedding model needs a provider package; install it as the message says. |
| … didn't accept the API key | The embedding model's provider needs a key: Settings → Keys and providers. |
