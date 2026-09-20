"""
LLM Interface for RF circuit design automation.
Provides a unified client for OpenAI-compatible API endpoints.
"""

import os
import time

from openai import OpenAI


class LLMClient:
    """Unified interface for OpenAI-compatible LLM endpoints."""

    def __init__(self, model_name):
        self.model_name = model_name
        self.client = self._setup_client()

    def _is_openai_family_model(self):
        """Return True for OpenAI-family models that should use OPENAI_API_KEY."""
        model_lower = self.model_name.lower()
        return (
            model_lower.startswith("gpt-")
            or model_lower.startswith("chatgpt-")
            or model_lower.startswith("o3")
            or model_lower.startswith("o4")
            or model_lower.startswith("o-")
        )

    def _resolve_provider(self):
        """Resolve provider from explicit override or model naming policy."""
        model_lower = self.model_name.lower()
        provider_override = os.getenv("LLM_PROVIDER", "").strip().lower()
        if provider_override:
            if provider_override == "openrouter":
                return "openrouter"
            if provider_override in {"openai", "default", "custom"}:
                return "openai"
            raise ValueError(
                "Unsupported LLM_PROVIDER. Use one of: openai, openrouter, default, custom."
            )

        if self._is_openai_family_model():
            return "openai"
        return "openrouter"

    def _setup_client(self):
        """Initialize the OpenAI-compatible client from environment variables."""
        provider = self._resolve_provider()

        if provider == "openrouter":
            api_key = os.getenv("OPENROUTER_API_KEY")
            if api_key is None:
                raise ValueError(
                    "OPENROUTER_API_KEY not found. Please set it in your .env file."
                )
            base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        else:
            api_key = os.getenv("LLM_API_KEY")
            if api_key is None:
                raise ValueError(
                    "LLM_API_KEY not found. Please set it in your .env file."
                )
            base_url = os.getenv("LLM_BASE_URL", "https://elm.edina.ac.uk/api/v1")

        print(f"[LLMClient] Model: {self.model_name} | Provider: {provider} | Endpoint: {base_url}")
        return OpenAI(base_url=base_url, api_key=api_key)

    def generate_content(self, messages, temperature=1.0, top_p=1.0):
        """
        Send a messages list to the LLM and return the response.

        Args:
            messages (list): List of {'role': ..., 'content': ...} dicts.
            temperature (float): Sampling temperature.
            top_p (float): Nucleus sampling parameter.

        Returns:
            dict: {
                'content': str,
                'time_seconds': float,
                'prompt_tokens': int,
                'completion_tokens': int,
                'total_tokens': int,
            }
        """
        try:
            start_time = time.time()

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature,
                top_p=top_p,
            )

            elapsed = time.time() - start_time
            content = response.choices[0].message.content.strip()

            return {
                "content": content,
                "time_seconds": elapsed,
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        except Exception as e:
            return {
                "content": f"Error: {str(e)}",
                "time_seconds": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }
