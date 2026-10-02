"""Knowledge Base search: find the passages that answer a question, ready to cite."""

from __future__ import annotations

from typing import Any

from ..compiler.issues import Fix, Issue, error, warning
from ..compiler.pycode import docstring, py_str
from ..providers import EMBEDDING_MODELS, split_model
from .ai import check_model, missing_field_issue
from .base import FormField, StepCode, StepHandler

MODES = {
    "hybrid": "by meaning and by words",
    "meaning": "by meaning",
    "words": "by words",
}


def embeddings_call(model: str, ctx: Any) -> str:
    """Source that creates the embedding model a Knowledge Base was built with."""
    from ..knowledge.ingest import embedding_kwargs

    if model == "keywords":
        return f"{ctx.helper('KeywordEmbeddings')}()"
    ctx.imports.add_from("langchain.embeddings", "init_embeddings")
    info = EMBEDDING_MODELS.get(model)
    provider = info.provider if info else split_model(model)[0]
    if provider:
        ctx.module.providers.add(provider)
    extra = "".join(f", {k}={v!r}" for k, v in embedding_kwargs(model).items())
    return f"init_embeddings({py_str(model)}{extra})"


class KnowledgeSearchHandler(StepHandler):
    type = "knowledge_search"
    label = "Knowledge Base search"
    technical = "Retriever · hybrid search"
    category = "knowledge"
    icon = "library"
    summary = "Finds the passages of your documents that answer a question, ready to cite."
    default_name = "Search the docs"
    form = [
        FormField(
            key="knowledge_base",
            label="Knowledge Base",
            kind="knowledge_base",
            help="Which documents to search. Make Knowledge Bases on the Knowledge page.",
        ),
        FormField(
            key="query",
            label="Search for",
            kind="field",
            help="The field with the question. Empty means: whatever the previous step saved.",
            placeholder="From the previous step",
            example="question",
        ),
        FormField(
            key="top_k",
            label="Passages to keep",
            kind="number",
            min=1,
            max=50,
            help="More passages give the AI more to go on, but cost more tokens.",
            technical="k",
        ),
        FormField(
            key="mode",
            label="Match",
            kind="select",
            options=[
                {"value": "hybrid", "label": "By meaning and by words (best)"},
                {"value": "meaning", "label": "By meaning only"},
                {"value": "words", "label": "By words only"},
            ],
            help="Meaning finds passages that say the same thing in other words; words finds "
            "exact terms like product names and error codes. Both together work best.",
            technical="vector + full-text search, reciprocal rank fusion",
            advanced=True,
        ),
        FormField(
            key="min_similarity",
            label="Skip weak matches below",
            kind="slider",
            min=0,
            max=1,
            step=0.05,
            help="Passages found only by meaning must be at least this similar to the question "
            "(0 to 1). Use it to notice when the documents don't cover a question.",
            technical="cosine similarity threshold",
            advanced=True,
        ),
        FormField(
            key="rerank_model",
            label="Re-rank with",
            kind="model",
            help="An AI model reads the best matches and puts the most useful first. Slower, "
            "and usually more accurate.",
            technical="LLM re-ranking",
            advanced=True,
        ),
        FormField(
            key="save_as",
            label="Save the passages as",
            kind="field_name",
            help="Numbered passages ([1], [2], …) for an AI Model to answer from and cite.",
            example="context",
        ),
        FormField(
            key="sources_as",
            label="Save the sources as",
            kind="field_name",
            help="The same passages as a list (title, source, page, text), to show as citations.",
            example="sources",
            advanced=True,
        ),
    ]

    def query_field(self, step: Any, an: Any) -> str | None:
        if step.settings.query:
            return step.settings.query
        if step.id in an.tool_of:
            return "query"
        return an.upstream_output(step.id)

    def writes(self, step: Any, an: Any) -> dict[str, str]:
        return {step.settings.save_as: "text", step.settings.sources_as: "list"}

    def reads(self, step: Any, an: Any) -> set[str]:
        field = self.query_field(step, an)
        return {field} if field else set()

    def check(self, step: Any, an: Any) -> list[Issue]:
        s = step.settings
        issues: list[Issue] = []
        if not s.knowledge_base:
            issues.append(
                error(
                    "no_knowledge_base",
                    "Pick the Knowledge Base to search.",
                    step=step.id,
                    setting="knowledge_base",
                    hint="Make one on the Knowledge page and add your documents first.",
                )
            )
        field = self.query_field(step, an)
        if not field:
            issues.append(
                error(
                    "no_query",
                    "This search has no question to look for.",
                    step=step.id,
                    setting="query",
                    hint="Connect it after Input, or pick the field with the question.",
                )
            )
        elif field not in an.available_fields(step.id):
            issues.append(missing_field_issue(step, field, an, "query", "This search looks for"))
        if s.rerank_model:
            issues += check_model(step.id, s.rerank_model, setting="rerank_model")
        if s.save_as == s.sources_as:
            issues.append(
                error(
                    "same_field_twice",
                    "The passages and the sources need different field names.",
                    step=step.id,
                    setting="sources_as",
                    fix=Fix(
                        "set_setting", "Use `sources`", {"key": "sources_as", "value": "sources"}
                    ),
                )
            )
        if s.embedding_model != "keywords" and ":" not in s.embedding_model:
            issues.append(
                warning(
                    "embedding_model_format",
                    f"“{s.embedding_model}” isn't an embedding model I recognise.",
                    step=step.id,
                    setting="knowledge_base",
                )
            )
        return issues

    def emit(self, step: Any, ctx: Any) -> StepCode:
        s = step.settings
        fn = ctx.fn(step.id)
        field = self.query_field(step, ctx.an) or "question"
        if ctx.field_type(field) == "messages":
            query = f'data[{py_str(field)}][-1].text if data.get({py_str(field)}) else ""'
        else:
            query = f'str(data.get({py_str(field)}) or "")'
        search = ctx.helper("search_knowledge")
        cite = ctx.helper("cite_passages")
        pool = s.top_k * 3 if s.rerank_model else s.top_k
        mode = f", mode={py_str(s.mode)}" if s.mode != "hybrid" else ""
        if s.min_similarity:
            mode += f", min_similarity={s.min_similarity}"
        lines = [
            f"    query = {query}",
            f"    embeddings = {embeddings_call(s.embedding_model, ctx)}",
            f"    hits = {search}({py_str(s.knowledge_base)}, query, embeddings, top_k={pool}{mode})",
        ]
        if s.rerank_model:
            rerank = ctx.helper("rerank_passages")
            model = ctx.model_call(s.rerank_model, {"temperature": 0})
            lines.append(f"    hits = {rerank}({model}, query, hits, {s.top_k})")
        lines.append(
            f"    return {{{py_str(s.save_as)}: {cite}(hits), {py_str(s.sources_as)}: hits}}"
        )
        lines = [
            line
            if len(line) <= 96
            else line.replace(f"top_k={pool}", f"\n        top_k={pool}").replace(
                f"{search}(", f"{search}(\n        ", 1
            )
            for line in lines
        ]
        rerank_note = f", re-ranked by {s.rerank_model}" if s.rerank_model else ""
        doc = docstring(
            f"{self.title(step)}\n\nFinds the {s.top_k} passages of the Knowledge Base "
            f"`{s.knowledge_base}` that best match `{field}` ({MODES[s.mode]}{rerank_note}), and "
            f"saves them numbered for citing as `{s.save_as}` and as a list as `{s.sources_as}`."
        )
        code = f"def {fn}(data: {ctx.data_class}) -> dict[str, Any]:\n{doc}\n" + "\n".join(lines)
        return StepCode([code], node=fn)
