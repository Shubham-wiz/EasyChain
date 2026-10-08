"""AI Model providers that Easy Chain knows how to call.

Each provider maps to a LangChain partner package and to the ``provider:model``
strings accepted by ``langchain.chat_models.init_chat_model``.
Prices are list prices in USD per million tokens, used for cost estimates only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str
    input_per_m: float | None = None
    output_per_m: float | None = None
    # Some newer models reject sampling settings such as temperature.
    accepts_temperature: bool = True


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    package: str
    import_hint: str
    key_env: str | None
    key_label: str
    models: tuple[ModelInfo, ...] = field(default_factory=tuple)
    host_env: str | None = None
    key_url: str | None = None
    supports_reasoning_effort: bool = False
    # Settings besides the key, as (environment variable, label) pairs.
    settings_env: tuple[tuple[str, str], ...] = ()
    # For providers that use cloud credentials instead of an API key.
    credentials: str | None = None
    # The pip extra that installs this provider's package (None: always installed).
    extra: str | None = None

    def installed(self) -> bool:
        import importlib.util

        return importlib.util.find_spec(self.import_hint) is not None

    def model(self, model_id: str) -> ModelInfo | None:
        for model in self.models:
            if model.id == model_id:
                return model
        return None


def _m(model_id: str, label: str, inp: float | None = None, out: float | None = None) -> ModelInfo:
    return ModelInfo(model_id, label, inp, out)


PROVIDERS: dict[str, Provider] = {
    p.id: p
    for p in [
        Provider(
            id="openai",
            label="OpenAI",
            package="langchain-openai==1.6.7",
            import_hint="langchain_openai",
            key_env="OPENAI_API_KEY",
            key_label="OpenAI API key",
            key_url="https://platform.openai.com/api-keys",
            supports_reasoning_effort=True,
            models=(
                ModelInfo("gpt-4o-mini", "GPT-4o mini (fast, cheap)", 0.15, 0.60),
                ModelInfo("gpt-4o", "GPT-4o", 2.50, 10.00),
                ModelInfo("gpt-4.1-mini", "GPT-4.1 mini", 0.40, 1.60),
                ModelInfo("gpt-4.1", "GPT-4.1", 2.00, 8.00),
            ),
        ),
        Provider(
            id="anthropic",
            label="Anthropic",
            package="langchain-anthropic==1.7.5",
            import_hint="langchain_anthropic",
            key_env="ANTHROPIC_API_KEY",
            key_label="Anthropic API key",
            key_url="https://console.anthropic.com/settings/keys",
            models=(
                ModelInfo(
                    "claude-opus-5-5", "Claude Opus 5.5", 4.00, 20.00, accepts_temperature=False
                ),
                ModelInfo(
                    "claude-sonnet-5-5", "Claude Sonnet 5.5", 2.00, 10.00, accepts_temperature=False
                ),
                ModelInfo("claude-haiku-4-5", "Claude Haiku 4.5 (fast, cheap)", 1.00, 5.00),
            ),
        ),
        Provider(
            id="ollama",
            label="Ollama (local)",
            package="langchain-ollama==1.1.0",
            import_hint="langchain_ollama",
            key_env=None,
            key_label="",
            host_env="OLLAMA_HOST",
            models=(
                ModelInfo("llama3.2", "Llama 3.2", 0, 0),
                ModelInfo("qwen2.5", "Qwen 2.5", 0, 0),
                ModelInfo("mistral", "Mistral", 0, 0),
            ),
        ),
        Provider(
            id="google_genai",
            label="Google Gemini",
            package="langchain-google-genai==4.4.0",
            import_hint="langchain_google_genai",
            key_env="GOOGLE_API_KEY",
            key_label="Google AI Studio API key",
            key_url="https://aistudio.google.com/app/apikey",
            extra="providers",
            models=(
                _m("gemini-2.5-flash", "Gemini 2.5 Flash (fast, cheap)", 0.30, 2.50),
                _m("gemini-2.5-pro", "Gemini 2.5 Pro", 1.25, 10.00),
                _m("gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite", 0.10, 0.40),
            ),
        ),
        Provider(
            id="google_vertexai",
            label="Google Vertex AI",
            package="langchain-google-vertexai==3.2.4",
            import_hint="langchain_google_vertexai",
            key_env=None,
            key_label="",
            credentials="Google Cloud credentials: run `gcloud auth application-default login`, "
            "or set GOOGLE_APPLICATION_CREDENTIALS to a service-account file.",
            extra="vertex",
            models=(
                _m("gemini-2.5-flash", "Gemini 2.5 Flash", 0.30, 2.50),
                _m("gemini-2.5-pro", "Gemini 2.5 Pro", 1.25, 10.00),
            ),
        ),
        Provider(
            id="bedrock_converse",
            label="AWS Bedrock",
            package="langchain-aws==1.8.0",
            import_hint="langchain_aws",
            key_env=None,
            key_label="",
            credentials="AWS credentials: an AWS profile, or AWS_ACCESS_KEY_ID and "
            "AWS_SECRET_ACCESS_KEY, plus AWS_DEFAULT_REGION.",
            extra="providers",
            models=(
                _m("amazon.nova-lite-v1:0", "Amazon Nova Lite (fast, cheap)", 0.06, 0.24),
                _m("amazon.nova-pro-v1:0", "Amazon Nova Pro", 0.80, 3.20),
                _m("meta.llama3-1-70b-instruct-v1:0", "Llama 3.1 70B", 0.72, 0.72),
            ),
        ),
        Provider(
            id="azure_openai",
            label="Azure OpenAI",
            package="langchain-openai==1.6.7",
            import_hint="langchain_openai",
            key_env="AZURE_OPENAI_API_KEY",
            key_label="Azure OpenAI API key",
            key_url="https://portal.azure.com/",
            settings_env=(
                ("AZURE_OPENAI_ENDPOINT", "Azure OpenAI endpoint (https://NAME.openai.azure.com)"),
                ("OPENAI_API_VERSION", "Azure OpenAI API version, e.g. 2024-10-21"),
            ),
            models=(
                _m("gpt-4o-mini", "gpt-4o-mini (your deployment name)", 0.15, 0.60),
                _m("gpt-4o", "gpt-4o (your deployment name)", 2.50, 10.00),
            ),
        ),
        Provider(
            id="mistralai",
            label="Mistral",
            package="langchain-mistralai==1.1.6",
            import_hint="langchain_mistralai",
            key_env="MISTRAL_API_KEY",
            key_label="Mistral API key",
            key_url="https://console.mistral.ai/api-keys",
            extra="providers",
            models=(
                _m("mistral-small-latest", "Mistral Small (fast, cheap)", 0.10, 0.30),
                _m("mistral-large-latest", "Mistral Large", 2.00, 6.00),
            ),
        ),
        Provider(
            id="groq",
            label="Groq",
            package="langchain-groq==1.1.3",
            import_hint="langchain_groq",
            key_env="GROQ_API_KEY",
            key_label="Groq API key",
            key_url="https://console.groq.com/keys",
            extra="providers",
            models=(
                _m("llama-3.3-70b-versatile", "Llama 3.3 70B", 0.59, 0.79),
                _m("llama-3.1-8b-instant", "Llama 3.1 8B (fast)", 0.05, 0.08),
                _m("openai/gpt-oss-120b", "GPT-OSS 120B", 0.15, 0.75),
            ),
        ),
        Provider(
            id="together",
            label="Together AI",
            package="langchain-together==0.4.0",
            import_hint="langchain_together",
            key_env="TOGETHER_API_KEY",
            key_label="Together API key",
            key_url="https://api.together.ai/settings/api-keys",
            extra="providers",
            models=(
                _m("meta-llama/Llama-3.3-70B-Instruct-Turbo", "Llama 3.3 70B Turbo", 0.88, 0.88),
                _m("deepseek-ai/DeepSeek-V3", "DeepSeek V3", 1.25, 1.25),
            ),
        ),
        Provider(
            id="fireworks",
            label="Fireworks AI",
            package="langchain-fireworks==1.7.0",
            import_hint="langchain_fireworks",
            key_env="FIREWORKS_API_KEY",
            key_label="Fireworks API key",
            key_url="https://fireworks.ai/account/api-keys",
            extra="providers",
            models=(
                _m(
                    "accounts/fireworks/models/llama-v3p3-70b-instruct", "Llama 3.3 70B", 0.90, 0.90
                ),
            ),
        ),
        Provider(
            id="openrouter",
            label="OpenRouter",
            package="langchain-openrouter==0.2.9",
            import_hint="langchain_openrouter",
            key_env="OPENROUTER_API_KEY",
            key_label="OpenRouter API key",
            key_url="https://openrouter.ai/keys",
            extra="providers",
            models=(
                _m("openai/gpt-4o-mini", "GPT-4o mini via OpenRouter"),
                _m("anthropic/claude-haiku-4.5", "Claude Haiku 4.5 via OpenRouter"),
                _m("meta-llama/llama-3.3-70b-instruct", "Llama 3.3 70B via OpenRouter"),
            ),
        ),
        Provider(
            id="deepseek",
            label="DeepSeek",
            package="langchain-deepseek==1.1.1",
            import_hint="langchain_deepseek",
            key_env="DEEPSEEK_API_KEY",
            key_label="DeepSeek API key",
            key_url="https://platform.deepseek.com/api_keys",
            extra="providers",
            models=(
                _m("deepseek-chat", "DeepSeek Chat", 0.27, 1.10),
                _m("deepseek-reasoner", "DeepSeek Reasoner", 0.55, 2.19),
            ),
        ),
        Provider(
            id="xai",
            label="xAI",
            package="langchain-xai==1.3.0",
            import_hint="langchain_xai",
            key_env="XAI_API_KEY",
            key_label="xAI API key",
            key_url="https://console.x.ai/",
            extra="providers",
            models=(
                _m("grok-3-mini", "Grok 3 Mini (fast, cheap)", 0.30, 0.50),
                _m("grok-4", "Grok 4", 3.00, 15.00),
            ),
        ),
    ]
}


@dataclass(frozen=True)
class EmbeddingModel:
    id: str  # provider:model, as init_embeddings takes it ("keywords" needs no model)
    label: str
    dims: int
    provider: str | None = None
    per_m: float | None = None


EMBEDDING_MODELS: dict[str, EmbeddingModel] = {
    m.id: m
    for m in [
        EmbeddingModel("keywords", "Keywords (no model or key; for trying things out)", 256),
        EmbeddingModel(
            "openai:text-embedding-3-small", "OpenAI text-embedding-3-small", 1536, "openai", 0.02
        ),
        EmbeddingModel(
            "openai:text-embedding-3-large", "OpenAI text-embedding-3-large", 3072, "openai", 0.13
        ),
        EmbeddingModel(
            "ollama:nomic-embed-text", "Ollama nomic-embed-text (local)", 768, "ollama", 0
        ),
        EmbeddingModel(
            "ollama:mxbai-embed-large", "Ollama mxbai-embed-large (local)", 1024, "ollama", 0
        ),
        EmbeddingModel(
            "google_genai:models/gemini-embedding-001",
            "Google Gemini embedding",
            3072,
            "google_genai",
        ),
        EmbeddingModel("mistralai:mistral-embed", "Mistral embed", 1024, "mistralai", 0.10),
        EmbeddingModel(
            "bedrock:amazon.titan-embed-text-v2:0",
            "Amazon Titan embeddings v2",
            1024,
            "bedrock_converse",
        ),
        EmbeddingModel(
            "azure_openai:text-embedding-3-small",
            "Azure OpenAI text-embedding-3-small (your deployment)",
            1536,
            "azure_openai",
        ),
    ]
}


def default_embedding_model() -> str:
    """OpenAI when its key is set, Ollama when it is running locally, else keywords."""
    import os

    if os.environ.get("OPENAI_API_KEY"):
        return "openai:text-embedding-3-small"
    if os.environ.get("OLLAMA_HOST"):
        return "ollama:nomic-embed-text"
    return "keywords"


def split_model(model: str) -> tuple[str | None, str]:
    """Split ``provider:model``; returns (None, model) when there is no provider prefix."""
    if ":" in model:
        provider, _, name = model.partition(":")
        return provider.strip().lower(), name.strip()
    return None, model.strip()


def get_provider(model: str) -> Provider | None:
    """The provider of a chat or embedding model (None for keywords or one we don't know)."""
    embedding = EMBEDDING_MODELS.get(model)
    if embedding is not None:
        return PROVIDERS.get(embedding.provider or "")
    provider, _ = split_model(model)
    return PROVIDERS.get(provider or "")


def flow_models(spec: Any) -> list[tuple[str, str, str]]:
    """Every model a flow's steps call, as (step id, setting, model).

    AI Models, AI Decisions, Agents (and their fallback models), Memory steps that
    summarise, and Knowledge Base searches (the embedding model and the re-ranking model).
    This is how Easy Chain works out which API keys a flow needs.
    """
    found: list[tuple[str, str, str]] = []
    for step in spec.steps:
        s = step.settings
        if (
            step.type in ("ai_model", "agent")
            or (step.type == "decision" and s.mode == "ai")
            or (step.type == "memory" and s.action == "summarise")
        ):
            found.append((step.id, "model", s.model))
        if step.type == "agent":
            found += [(step.id, "addons", model) for model in s.addons.fallback_models]
        if step.type == "knowledge_search":
            if s.embedding_model != "keywords":
                found.append((step.id, "embedding_model", s.embedding_model))
            if s.rerank_model:
                found.append((step.id, "rerank_model", s.rerank_model))
    return [(step_id, key, model) for step_id, key, model in found if model]


def model_info(model: str) -> ModelInfo | None:
    provider_id, name = split_model(model)
    provider = PROVIDERS.get(provider_id or "")
    return provider.model(name) if provider else None


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    info = model_info(model)
    if info is None or info.input_per_m is None or info.output_per_m is None:
        return None
    return (input_tokens * info.input_per_m + output_tokens * info.output_per_m) / 1_000_000
