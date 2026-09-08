# One LLM interface: the OpenAI-compatible chat and embeddings API

"Any LLM" is a requirement: adopters may use Anthropic, OpenAI, OpenRouter, Groq, or a local Ollama server depending on budget and hardware. Rather than an abstraction library such as LangChain or LiteLLM, we talk to every provider through the OpenAI-compatible endpoint they all expose, configured by three variables: base URL, API key, model. Embeddings follow the same pattern, with fastembed (local ONNX) as the default so no key is needed. Structured output is enforced by code, not the provider: request JSON, validate with Pydantic, retry once with the validation error fed back.

## Consequences

- A single model setting serves both LLM tasks (structuring and judging). Per-task overrides were deliberately left out for now.
- Provider-specific features (native tool use, JSON schema mode) are not used, so the same code path works on small local models.
