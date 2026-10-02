# Memory

**Remembers facts about a user across conversations, or keeps a long chat short.** In the
Knowledge and memory group. LangChain terms: the LangGraph store (long-term memory),
`trim_messages` and summarisation (short-term memory).

## What to do

| Action | What it does |
|---|---|
| Recall what I know about the user | Finds the user's saved facts that match a text (usually the question) and saves them one per line, for your Instructions. |
| Remember something about the user | Saves a text field as a fact about the user. |
| Keep only the latest chat messages | Trims a messages field to the latest N. |
| Summarise older chat messages | When a chat has more than N messages, a model summarises the older ones into a field and the latest N are kept. |

## Settings

| Setting | What it does |
|---|---|
| Text | Remember: the field to save. Recall: what to match. Empty means: what the previous step saved. |
| User id from | Memories belong to one user. Empty: the `user_id` field when there is one, otherwise the conversation. |
| Facts to recall | How many facts to bring back (default 5). |
| Chat field | Trim and Summarise: the messages field (default `messages`). |
| Recent messages to keep | Trim and Summarise. |
| Model | Summarise: the model that writes the summary. |
| Save as | Recall: the facts. Summarise: the summary. |

Facts are kept in the flow's store (the same database as Save Points), under the user's id, so
they last across conversations and runs. An **Agent** can get the same memory as two tools
(`remember`, `recall`) with the **Long-term memory** add-on.

## Example

```yaml
- id: recall
  type: memory
  settings:
    action: recall
    text: question
    save_as: known_facts
- id: tidy_chat
  type: memory
  settings:
    action: summarise
    keep: 12
    save_as: chat_summary
```

## Errors you might see

| Message | What to do |
|---|---|
| There's nothing to remember yet / nothing to match | Pick the field, or connect a step before this one. |
| `…` isn't a chat (messages) field | Trim and Summarise work on a messages field. |
