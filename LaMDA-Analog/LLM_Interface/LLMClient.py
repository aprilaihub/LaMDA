"""
Core LLM Interface classes for different model providers.
Handles the low-level interaction with OpenAI and Google Gemini APIs.
"""

import os


class LLMClient:
    """Unified interface for different LLM providers."""
    
    def __init__(self, model_name):
        self.model_name = model_name
        self.client = None
        self._setup_client()
    
    def _setup_client(self):
        """Initialize the appropriate client based on model name."""
        if self.model_name.startswith("gpt-") or self.model_name.startswith("o-"):
            self._setup_openai_client()
        elif self.model_name.startswith("gemini-"):
            self._setup_gemini_client()
        else:
            raise ValueError(f"Unsupported model: {self.model_name}")
    
    def _setup_openai_client(self):
        """Setup OpenAI client."""
        from openai import OpenAI
        
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if openai_api_key is None:
            raise ValueError("API key not found. Please set the OPENAI_API_KEY environment variable.")
        self.client = OpenAI(api_key=openai_api_key)
    
    def _setup_gemini_client(self):
        """Setup Gemini client."""
        from google import genai
        from google.genai import types
        
        gemini_api_key = os.getenv("GEMINI_API_KEY")
        if gemini_api_key is None:
            raise ValueError("API key not found. Please set the GEMINI_API_KEY environment variable.")
        self.client = genai.Client()
        self.genai_types = types
    
    def generate_content(self, prompt, system_prompt, max_tokens=1024, temperature=0.7, top_p=1.0):
        """Generate content using the appropriate model."""
        if self.model_name.startswith("gpt-") or self.model_name.startswith("o-"):
            return self._generate_openai_content(prompt, system_prompt, max_tokens, temperature, top_p)
        elif self.model_name.startswith("gemini-"):
            return self._generate_gemini_content(prompt, system_prompt, max_tokens, temperature, top_p)
    
    def _generate_openai_content(self, prompt, system_prompt, max_tokens, temperature, top_p):
        """Generate content using OpenAI models."""
        stream = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            stream=True,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )
        
        content_parts = []
        token_count = 0
        
        for chunk in stream:
            content = chunk.choices[0].delta.content or ""
            token_count += len(content.split())
            content_parts.append(content)
        
        return ''.join(content_parts), token_count
    
    def _generate_gemini_content(self, prompt, system_prompt, max_tokens, temperature, top_p):
        """Generate content using Gemini models."""
        config = self.genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p
        )
        
        stream = self.client.models.generate_content_stream(
            model=self.model_name,
            contents=prompt,
            config=config,
        )
        
        content_parts = []
        
        for chunk in stream:
            content = chunk.text
            if content:
                content_parts.append(content)
        
        full_content = ''.join(content_parts)
        
        # Get accurate token count for Gemini
        token_info = self.client.models.count_tokens(
            model=self.model_name, 
            contents=full_content
        )
        token_count = token_info.total_tokens
        
        return full_content, token_count