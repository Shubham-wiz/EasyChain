"""The Easy Chain flow spec, version 1.

A flow spec is the single source of truth for a flow. The canvas edits it, the
compiler turns it into LangGraph code, and it is saved as human-readable YAML
(see ``easychain.spec.io``). The JSON Schema published at ``spec/flow.schema.json``
is generated from these models.

Defaults are part of the spec version: changing a default value means bumping
``SPEC_VERSION``, because saved files omit settings that equal their default.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SPEC_VERSION = 1

# Step ids and Flow Data field names become Python identifiers in generated code.
IDENT_PATTERN = r"^[a-z][a-z0-9_]{0,62}$"
Ident = Annotated[str, Field(pattern=IDENT_PATTERN)]

FieldType = Literal["text", "number", "yes_no", "list", "object", "file", "messages", "any"]
UpdateRule = Literal["replace", "append", "merge", "add", "custom"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ── Flow Data ────────────────────────────────────────────────────────────────


class DataField(_Model):
    """A named field of Flow Data (LangGraph state key)."""

    name: Ident
    type: FieldType = "text"
    update: UpdateRule = Field(
        default="replace",
        description="How a new value combines with the old one: replace, append (lists and "
        "messages), merge (objects), add (numbers) or custom (a Python function).",
    )
    description: str = ""
    combine: str = Field(
        default="",
        description="For update: custom, Python code defining combine(old, new) -> value.",
    )


# ── Steps ────────────────────────────────────────────────────────────────────


class RunPolicy(_Model):
    """How a step runs: retries, time limit, cache and joining parallel branches."""

    retries: int = Field(default=0, ge=0, le=10, description="Extra attempts after a failure.")
    retry_wait: float = Field(
        default=1.0, gt=0, le=600, description="Seconds before the first retry; doubles each time."
    )
    timeout: float | None = Field(
        default=None, gt=0, description="Seconds before the step is stopped."
    )
    cache: bool = Field(
        default=False, description="Reuse the result when the step gets the same inputs again."
    )
    cache_ttl: int | None = Field(
        default=None, gt=0, description="Seconds a cached result stays valid."
    )
    wait_for_all: bool = Field(
        default=False, description="Wait until every parallel branch has finished before running."
    )


class _StepBase(_Model):
    id: Ident = Field(description="Unique id; also the LangGraph node name.")
    name: str = Field(default="", description="Label shown on the canvas.")
    description: str = ""
    run: RunPolicy = Field(default_factory=RunPolicy)


class InputField(_Model):
    name: Ident
    type: Literal["text", "number", "yes_no", "list", "object", "file"] = "text"
    description: str = ""
    example: Any = None
    default: Any = None
    required: bool = True


class InputSettings(_Model):
    mode: Literal["form", "chat"] = Field(
        default="form",
        description="form: the flow takes named fields. chat: the flow takes chat messages, "
        "kept per conversation in the `messages` field.",
    )
    fields: list[InputField] = Field(default_factory=list)


class InputStep(_StepBase):
    """Where a run starts. Declares the fields a run takes (LangGraph START + input schema)."""

    type: Literal["input"]
    settings: InputSettings = Field(default_factory=InputSettings)


class OutputSettings(_Model):
    fields: list[Ident] = Field(
        default_factory=list, description="Flow Data fields returned when the run finishes."
    )


class OutputStep(_StepBase):
    """Where a run ends. Picks the fields to return (LangGraph END + output schema)."""

    type: Literal["output"]
    settings: OutputSettings = Field(default_factory=OutputSettings)


SchemaFieldType = Literal["text", "number", "whole_number", "yes_no", "choice", "list", "object"]


class SchemaField(_Model):
    """One field of a structured reply (becomes a Pydantic field)."""

    name: Ident
    type: SchemaFieldType = "text"
    description: str = Field(default="", description="Tells the model what to put here.")
    required: bool = True
    options: list[str] = Field(default_factory=list, description="For choice: the allowed values.")
    items: Literal["text", "number", "whole_number", "yes_no", "object"] = Field(
        default="text", description="For list: what each item is."
    )
    fields: list[SchemaField] = Field(
        default_factory=list, description="For object, or a list of objects: their fields."
    )


class StructuredOutput(_Model):
    """Make the model reply in a fixed shape (with_structured_output / response_format)."""

    fields: list[SchemaField] = Field(default_factory=list)
    description: str = Field(default="", description="What the reply is, for the model.")
    spread: bool = Field(
        default=False, description="Also save each field as its own Flow Data field."
    )
    retries: int = Field(
        default=1, ge=0, le=5, description="Ask again this many times when a reply doesn't fit."
    )


class AIModelSettings(_Model):
    model: str = Field(
        default="openai:gpt-4o-mini",
        description="provider:model, for example openai:gpt-4o-mini, "
        "anthropic:claude-haiku-4-5 or ollama:llama3.2.",
    )
    prompt: Ident | None = Field(
        default=None,
        description="Flow Data field to send to the model (text or messages). "
        "Empty means: what the previous step saved.",
    )
    save_as: Ident = "answer"
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0)
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    stop: list[str] = Field(default_factory=list)
    timeout: float | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0)
    base_url: str | None = Field(
        default=None, description="Custom endpoint, e.g. an OpenAI-compatible server."
    )
    api_key: str | None = Field(
        default=None,
        description="Name of the secret holding the key, for a custom endpoint "
        "(empty: the provider's usual key).",
    )
    output: StructuredOutput | None = Field(
        default=None, description="Reply in a fixed shape instead of free text."
    )


class AIModelStep(_StepBase):
    """Calls a chat model (LangChain chat model via init_chat_model)."""

    type: Literal["ai_model"]
    settings: AIModelSettings = Field(default_factory=AIModelSettings)


class ExampleMessage(_Model):
    role: Literal["user", "assistant"]
    content: str


class InstructionsSettings(_Model):
    system: str = "You are a helpful assistant."
    user: str = ""
    examples: list[ExampleMessage] = Field(
        default_factory=list, description="Few-shot example turns placed before the user message."
    )
    history: Ident | None = Field(
        default=None, description="Messages field to insert as chat history (e.g. messages)."
    )
    save_as: Ident = "prompt"


class InstructionsStep(_StepBase):
    """Fills a prompt template with Flow Data (LangChain ChatPromptTemplate)."""

    type: Literal["instructions"]
    settings: InstructionsSettings = Field(default_factory=InstructionsSettings)


class HttpRequestSettings(_Model):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = ""
    response: Literal["readable_text", "text", "json"] = Field(
        default="readable_text",
        description="readable_text strips HTML down to the words on the page.",
    )
    max_chars: int | None = Field(default=20000, gt=0)
    timeout: float = Field(default=30, gt=0)
    save_as: Ident = "response"
    side_effect: bool | None = Field(
        default=None,
        description="Changes something elsewhere (sends, pays, deletes): send it at most once, with "
        "an Idempotency-Key. Empty means: yes for POST, PUT, PATCH and DELETE.",
    )


class HttpRequestStep(_StepBase):
    """Action: calls a URL over HTTP."""

    type: Literal["http_request"]
    settings: HttpRequestSettings = Field(default_factory=HttpRequestSettings)


DEFAULT_CODE = '''def run(data):
    """Return the Flow Data fields to update."""
    text = data.get("answer", "")
    return {"word_count": len(text.split())}
'''


class CodeSettings(_Model):
    code: str = DEFAULT_CODE
    writes: list[Ident] = Field(
        default_factory=list,
        description="Fields this code sets. Empty means: worked out from the returned dict.",
    )
    requirements: list[str] = Field(default_factory=list)
    side_effect: bool = Field(
        default=False,
        description="The code changes something elsewhere: run it at most once, even after a "
        "retry or a crash.",
    )


class CodeStep(_StepBase):
    """Action: runs a Python function over Flow Data."""

    type: Literal["code"]
    settings: CodeSettings = Field(default_factory=CodeSettings)


ConditionOp = Literal[
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "matches",
    "is_empty",
    "is_not_empty",
    "greater_than",
    "less_than",
    "longer_than",
    "shorter_than",
    "is_true",
    "is_false",
]


class Condition(_Model):
    field: Ident | None = None
    op: ConditionOp = "equals"
    value: Any = None
    expression: str | None = Field(
        default=None,
        description="Pro: a safe Python expression over field names, e.g. len(page) > 5000.",
    )


class DecisionExit(_Model):
    label: str = Field(min_length=1, max_length=60)
    when: Condition | None = None
    description: str = ""


class DecisionSettings(_Model):
    mode: Literal["rules", "ai"] = "rules"
    exits: list[DecisionExit] = Field(
        default_factory=lambda: [DecisionExit(label="Yes", when=Condition(op="is_not_empty"))]
    )
    otherwise: str = Field(default="Otherwise", min_length=1, max_length=60)
    # AI mode only
    model: str = "openai:gpt-4o-mini"
    input: Ident | None = Field(
        default=None, description="Field the AI reads. Empty means: what the previous step saved."
    )
    instructions: str = ""
    save_as: Ident = "choice"
    # Loop guard
    max_rounds: int | None = Field(
        default=None, ge=1, le=1000, description="Leave a loop after this many rounds."
    )
    when_max: str | None = Field(
        default=None, description="Exit to take when max_rounds is reached (default: otherwise)."
    )


class DecisionStep(_StepBase):
    """Picks the next step (LangGraph conditional edge)."""

    type: Literal["decision"]
    settings: DecisionSettings = Field(default_factory=DecisionSettings)


class AskHumanSettings(_Model):
    kind: Literal["approve", "edit", "answer", "choose"] = Field(
        default="approve",
        description="approve: Approve/Reject. edit: change a field, then Approve/Reject. "
        "answer: type a reply. choose: pick one of the options.",
    )
    question: str = "Please review and approve."
    show: list[Ident] = Field(default_factory=list, description="Fields shown to the reviewer.")
    field: Ident | None = Field(
        default=None, description="For edit: the field the reviewer can change."
    )
    options: list[str] = Field(
        default_factory=list, description="For choose: the choices (also the exits)."
    )
    save_as: Ident = "human_answer"
    notify: bool = Field(
        default=True, description="Send the configured notifications when it pauses."
    )


class AskHumanStep(_StepBase):
    """Pauses the run until a person answers in the Inbox (LangGraph interrupt)."""

    type: Literal["ask_human"]
    settings: AskHumanSettings = Field(default_factory=AskHumanSettings)


class ForEachSettings(_Model):
    items: Ident | None = Field(
        default=None,
        description="List field to go through. Empty means: what the previous step saved.",
    )
    item_name: Ident = Field(default="item", description="Field that holds the current item.")
    save_as: Ident = Field(
        default="results", description="List of what each item produced, in order."
    )
    concurrency: int | None = Field(
        default=None, ge=1, le=100, description="Items worked on at once."
    )


class ForEachStep(_StepBase):
    """Runs a step once for every item in a list, in parallel (LangGraph Send)."""

    type: Literal["for_each"]
    settings: ForEachSettings = Field(default_factory=ForEachSettings)


class SubflowSettings(_Model):
    flow: str = Field(default="", description="Id of the flow to run as this step.")
    share_data: bool = Field(
        default=False,
        description="Share Flow Data with the sub-flow (same field names) instead of mapping it.",
    )
    inputs: dict[Ident, str] = Field(
        default_factory=dict, description="Sub-flow input field -> value, e.g. {page}."
    )
    outputs: dict[Ident, Ident] = Field(
        default_factory=dict, description="Field here -> sub-flow output field."
    )


class SubflowStep(_StepBase):
    """Runs another flow as one step (LangGraph subgraph)."""

    type: Literal["subflow"]
    settings: SubflowSettings = Field(default_factory=SubflowSettings)


class FieldUpdate(_Model):
    field: Ident
    value: str = Field(default="", description="Text with {field} placeholders.")
    expression: str | None = Field(
        default=None, description="Pro: a safe expression, e.g. count + 1."
    )


class JumpSettings(_Model):
    updates: list[FieldUpdate] = Field(default_factory=list)
    exits: list[DecisionExit] = Field(default_factory=list)
    otherwise: str = Field(default="Next", min_length=1, max_length=60)


class JumpStep(_StepBase):
    """Sets Flow Data and picks the next step in one move (LangGraph Command)."""

    type: Literal["jump"]
    settings: JumpSettings = Field(default_factory=JumpSettings)


class KnowledgeSearchSettings(_Model):
    knowledge_base: str = Field(default="", description="Id of the Knowledge Base to search.")
    embedding_model: str = Field(
        default="keywords",
        description="The embedding model the Knowledge Base was built with (set when you pick it).",
    )
    query: Ident | None = Field(
        default=None,
        description="Field with the question. Empty means: what the previous step saved "
        "(or what the agent asks, when this step is a tool).",
    )
    top_k: int = Field(default=4, ge=1, le=50, description="How many passages to keep.")
    mode: Literal["hybrid", "meaning", "words"] = Field(
        default="hybrid",
        description="hybrid: by meaning and by words, merged. meaning: embeddings only. "
        "words: full-text search only.",
    )
    rerank_model: str | None = Field(
        default=None, description="An AI model that re-orders the passages by relevance."
    )
    save_as: Ident = Field(default="context", description="The passages, numbered for citing.")
    sources_as: Ident = Field(
        default="sources", description="The passages as a list (title, source, page, text)."
    )


class KnowledgeSearchStep(_StepBase):
    """Finds the passages of a Knowledge Base that best match a question (a retriever)."""

    type: Literal["knowledge_search"]
    settings: KnowledgeSearchSettings = Field(default_factory=KnowledgeSearchSettings)


PIIType = Literal["email", "credit_card", "ip", "mac_address", "url"]


class AgentAddons(_Model):
    """Agent Add-ons: LangChain agent middleware, switched on with a toggle."""

    approve_tools: list[str] = Field(
        default_factory=list,
        description="Tools that wait for a person to approve, edit or reject each call "
        "(HumanInTheLoopMiddleware). The run pauses and the request appears in the Inbox.",
    )
    max_model_calls: int | None = Field(
        default=None, ge=1, le=1000, description="Stop after this many model calls in one run."
    )
    max_tool_calls: int | None = Field(
        default=None, ge=1, le=1000, description="Stop calling tools after this many in one run."
    )
    tool_errors: Literal["tell_agent", "stop"] = Field(
        default="tell_agent",
        description="When a tool fails: tell the agent so it can try another way, or stop the run.",
    )
    tool_retries: int = Field(
        default=0, ge=0, le=10, description="Try a failing tool again this many times."
    )
    model_retries: int = Field(
        default=0, ge=0, le=10, description="Try a failing model call again this many times."
    )
    fallback_models: list[str] = Field(
        default_factory=list, description="Models to try, in order, when the main one fails."
    )
    summarise: bool = Field(
        default=False, description="Summarise older messages when the conversation gets long."
    )
    summarise_after: int = Field(
        default=4000, ge=500, description="Summarise when the messages pass this many tokens."
    )
    summarise_keep: int = Field(
        default=20, ge=1, description="Keep this many recent messages as they are."
    )
    clear_tool_results: int | None = Field(
        default=None,
        ge=500,
        description="Clear old tool results when the conversation passes this many tokens.",
    )
    pii: list[PIIType] = Field(
        default_factory=list, description="Personal data to catch in what the user sends."
    )
    pii_strategy: Literal["redact", "mask", "hash", "block"] = "redact"
    select_tools: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="With many tools: let a model pick this many relevant ones for each request.",
    )
    todo_list: bool = Field(
        default=False, description="Give the agent a to-do list for planning longer tasks."
    )
    emulate_tools: bool = Field(
        default=False,
        description="For testing: an AI model pretends to be the tools instead of running them.",
    )
    memory: bool = Field(
        default=False,
        description="Long-term memory: the agent can remember facts about the user and recall "
        "them in later conversations (LangGraph store).",
    )


class McpTools(_Model):
    """Tools from an MCP server (Settings → MCP servers)."""

    server: str = Field(description="Id of the MCP server.")
    tools: list[str] = Field(
        default_factory=list, description="Tool names to use; empty means all of them."
    )


DEFAULT_AGENT_INSTRUCTIONS = (
    "You are a helpful assistant. Use the tools when they help, and say so when you can't "
    "find an answer."
)


class AgentSettings(_Model):
    model: str = Field(
        default="openai:gpt-4o-mini", description="The model that thinks and picks tools."
    )
    instructions: str = Field(
        default=DEFAULT_AGENT_INSTRUCTIONS,
        description="Role and rules (the system prompt). Put Flow Data in with {field}.",
    )
    input: Ident | None = Field(
        default=None,
        description="What the agent works on: a text field, or a messages field for a chat. "
        "Empty means: what the previous step saved.",
    )
    tools: list[Ident] = Field(
        default_factory=list, description="Steps the agent can use as tools (their ids)."
    )
    mcp: list[McpTools] = Field(default_factory=list)
    save_as: Ident = "answer"
    output: StructuredOutput | None = Field(
        default=None, description="Answer in a fixed shape instead of free text."
    )
    save_messages: Ident | None = Field(
        default=None,
        description="Also save the agent's whole conversation (tool calls included) here.",
    )
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_steps: int = Field(
        default=40, ge=5, le=1000, description="Most rounds of thinking and tool calls in one run."
    )
    addons: AgentAddons = Field(default_factory=AgentAddons)


class AgentStep(_StepBase):
    """An AI that decides which tools to use, in a loop, until it has an answer (create_agent)."""

    type: Literal["agent"]
    settings: AgentSettings = Field(default_factory=AgentSettings)


Step = Annotated[
    InputStep
    | OutputStep
    | AIModelStep
    | InstructionsStep
    | HttpRequestStep
    | CodeStep
    | DecisionStep
    | AskHumanStep
    | ForEachStep
    | SubflowStep
    | JumpStep
    | AgentStep
    | KnowledgeSearchStep,
    Field(discriminator="type"),
]


# ── Connections and canvas ───────────────────────────────────────────────────


class Connection(_Model):
    source: str = Field(alias="from")
    target: str = Field(alias="to")
    exit: str | None = Field(
        default=None, description="Decision exit label this connection leaves from."
    )


class Position(_Model):
    x: float = 0
    y: float = 0


class StickyNote(_Model):
    id: str
    text: str = ""
    x: float = 0
    y: float = 0
    width: float = 220
    height: float = 120


class Canvas(_Model):
    """Visual-only data. Kept in its own section so flow diffs stay clean."""

    steps: dict[str, Position] = Field(default_factory=dict)
    notes: list[StickyNote] = Field(default_factory=list)
    viewport: dict[str, float] | None = None


class FlowSettings(_Model):
    """How runs of this flow behave."""

    max_steps: int = Field(
        default=25, ge=1, le=10000, description="Stop a run after this many rounds of steps."
    )
    max_parallel: int | None = Field(
        default=None, ge=1, le=1000, description="Most steps that run at the same time."
    )
    double_texting: Literal["reject", "queue", "interrupt", "rollback"] = Field(
        default="queue",
        description="When a new message arrives while a conversation is still running: reject it, "
        "queue it, interrupt the current run, or roll it back and start over.",
    )
    max_concurrent_runs: int | None = Field(
        default=None, ge=1, le=1000, description="Most runs of this flow at the same time."
    )


class FlowSpec(_Model):
    """An Easy Chain flow (a LangGraph StateGraph)."""

    version: Literal[1] = SPEC_VERSION
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    settings: FlowSettings = Field(default_factory=FlowSettings)
    data: list[DataField] = Field(default_factory=list, description="Flow Data fields.")
    steps: list[Step] = Field(default_factory=list)
    connections: list[Connection] = Field(default_factory=list)
    canvas: Canvas = Field(default_factory=Canvas)

    @field_validator("steps")
    @classmethod
    def _unique_step_ids(cls, steps: list[Any]) -> list[Any]:
        seen: set[str] = set()
        for step in steps:
            if step.id in seen:
                raise ValueError(f"Two steps share the id '{step.id}'. Step ids must be unique.")
            seen.add(step.id)
        return steps

    def step(self, step_id: str) -> Any:
        for step in self.steps:
            if step.id == step_id:
                return step
        raise KeyError(step_id)

    def step_map(self) -> dict[str, Any]:
        return {step.id: step for step in self.steps}


STEP_MODELS: dict[str, type[BaseModel]] = {
    "input": InputStep,
    "output": OutputStep,
    "ai_model": AIModelStep,
    "instructions": InstructionsStep,
    "http_request": HttpRequestStep,
    "code": CodeStep,
    "decision": DecisionStep,
    "ask_human": AskHumanStep,
    "for_each": ForEachStep,
    "subflow": SubflowStep,
    "jump": JumpStep,
    "agent": AgentStep,
    "knowledge_search": KnowledgeSearchStep,
}
