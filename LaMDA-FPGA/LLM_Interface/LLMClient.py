"""
Core LLM Interface for different model providers.

The provider is always chosen explicitly (``LLMClient(model, provider=...)`` or the
``LLM_PROVIDER`` environment variable); the model name is passed through unchanged.

Each provider is one entry in ``PROVIDERS``. OpenAI-compatible services only need a
new entry; a provider with its own SDK also needs a ``_setup_<backend>`` and a
``_generate_<backend>`` method.
"""

import os
from collections import namedtuple

Provider = namedtuple("Provider", ["api_key_env", "base_url", "backend"])

# name -> Provider(API key env var, default base_url or None, backend).
# Select one with LLM_PROVIDER=<name> (e.g. `make LLM_PROVIDER=elm MODEL=gpt-4o ...`).
# LLM_BASE_URL overrides the base_url of OpenAI-compatible providers except "openai".
PROVIDERS = {
    "openai": Provider("OPENAI_API_KEY", None, "openai"),
    "openrouter": Provider("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1", "openai"),
    "elm": Provider("ELM_API_KEY", "https://elm.edina.ac.uk/api/v1", "openai"),
    "gemini": Provider("GEMINI_API_KEY", None, "gemini"),
    "anthropic": Provider("ANTHROPIC_API_KEY", None, "anthropic"),
}


class LLMClient:
    """Unified interface for different LLM providers."""

    def __init__(self, model_name, provider=None):
        self.model_name = model_name
        self.provider = self._resolve_provider(provider)
        self.config = PROVIDERS[self.provider]
        self.client = None
        self._setup_client()

    @staticmethod
    def _resolve_provider(provider):
        """Return the validated provider name from the argument or LLM_PROVIDER."""
        name = (provider or os.getenv("LLM_PROVIDER", "")).strip().lower()
        if not name:
            raise ValueError(
                "No LLM provider selected. Pass provider=... or set LLM_PROVIDER "
                f"to one of: {', '.join(sorted(PROVIDERS))}."
            )
        if name not in PROVIDERS:
            raise ValueError(
                f"Unsupported LLM provider '{name}'. Use one of: {', '.join(sorted(PROVIDERS))}."
            )
        return name

    # ------------------------------------------------------------------ setup

    def _setup_client(self):
        api_key = os.getenv(self.config.api_key_env)
        if not api_key:
            raise ValueError(
                f"API key not found. Please set the {self.config.api_key_env} environment variable."
            )
        getattr(self, f"_setup_{self.config.backend}")(api_key)

    def _setup_openai(self, api_key):
        from openai import OpenAI

        kwargs = {"api_key": api_key}
        base_url = self.config.base_url
        if self.provider != "openai":
            base_url = os.getenv("LLM_BASE_URL", base_url)
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)

    def _setup_gemini(self, api_key):
        from google import genai
        from google.genai import types

        self.client = genai.Client(api_key=api_key)
        self.genai_types = types

    def _setup_anthropic(self, api_key):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)

    # ------------------------------------------------------------- generation

    def generate_content(self, prompt, system_prompt, max_tokens=1024, temperature=0.7, top_p=1.0):
        """Generate content. Returns (text, output_token_count)."""
        generate = getattr(self, f"_generate_{self.config.backend}")
        return generate(prompt, system_prompt, max_tokens, temperature, top_p)

    def _generate_openai(self, prompt, system_prompt, max_tokens, temperature, top_p):
        stream = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            stream=True,
            stream_options={"include_usage": True},
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )

        parts = []
        token_count = None
        finish_reason = None
        for chunk in stream:
            if chunk.choices:
                parts.append(chunk.choices[0].delta.content or "")
                finish_reason = chunk.choices[0].finish_reason or finish_reason
            if getattr(chunk, "usage", None):
                token_count = chunk.usage.completion_tokens

        text = "".join(parts)
        if not text:
            print(
                f"[LLMClient] WARNING: empty response from {self.provider}/{self.model_name} "
                f"(finish_reason={finish_reason}, completion_tokens={token_count}, "
                f"max_tokens={max_tokens}). finish_reason=length means the token budget was "
                "used up (e.g. by reasoning); try a larger MAX_TOKENS.",
                flush=True,
            )
        return text, self._token_count_or_words(token_count, text)

    def _generate_gemini(self, prompt, system_prompt, max_tokens, temperature, top_p):
        config = self.genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )
        stream = self.client.models.generate_content_stream(
            model=self.model_name,
            contents=prompt,
            config=config,
        )

        parts = []
        token_count = None
        for chunk in stream:
            if chunk.text:
                parts.append(chunk.text)
            if chunk.usage_metadata:
                token_count = chunk.usage_metadata.candidates_token_count

        text = "".join(parts)
        return text, self._token_count_or_words(token_count, text)

    def _generate_anthropic(self, prompt, system_prompt, max_tokens, temperature, top_p):
        # top_p is not sent: newer Claude models reject temperature and top_p together.
        with self.client.messages.stream(
            model=self.model_name,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        ) as stream:
            message = stream.get_final_message()

        text = "".join(block.text for block in message.content if block.type == "text")
        return text, self._token_count_or_words(message.usage.output_tokens, text)

    @staticmethod
    def _token_count_or_words(token_count, text):
        """Use the provider-reported token count; fall back to a word count if absent."""
        return token_count if token_count else len(text.split())
