"""AI Model providers that Easy Chain knows how to call.

Each provider maps to a LangChain partner package and to the ``provider:model``
strings accepted by ``langchain.chat_models.init_chat_model``.
Prices are list prices in USD per million tokens, used for cost estimates only.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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

    def model(self, model_id: str) -> ModelInfo | None:
        for model in self.models:
            if model.id == model_id:
                return model
        return None


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
    ]
}


def split_model(model: str) -> tuple[str | None, str]:
    """Split ``provider:model``; returns (None, model) when there is no provider prefix."""
    if ":" in model:
        provider, _, name = model.partition(":")
        return provider.strip().lower(), name.strip()
    return None, model.strip()


def get_provider(model: str) -> Provider | None:
    provider, _ = split_model(model)
    return PROVIDERS.get(provider or "")


def model_info(model: str) -> ModelInfo | None:
    provider_id, name = split_model(model)
    provider = PROVIDERS.get(provider_id or "")
    return provider.model(name) if provider else None


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    info = model_info(model)
    if info is None or info.input_per_m is None or info.output_per_m is None:
        return None
    return (input_tokens * info.input_per_m + output_tokens * info.output_per_m) / 1_000_000
