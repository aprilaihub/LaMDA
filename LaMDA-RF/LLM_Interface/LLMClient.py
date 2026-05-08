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

    def _setup_client(self):
        """Initialize the OpenAI-compatible client from environment variables."""
        api_key = os.getenv("LLM_API_KEY")
        if api_key is None:
            raise ValueError(
                "LLM_API_KEY environment variable not found.\n"
                "Please set it in your .env file."
            )
        base_url = os.getenv("LLM_BASE_URL", "https://elm.edina.ac.uk/api/v1")
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
