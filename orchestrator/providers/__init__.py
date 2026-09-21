"""Chat provider implementations.

Every provider is OpenAI-compatible (POST /v1/chat/completions): OpenRouter,
MiniMax, DeepSeek, local vLLM, OpenAI, Groq, Together, Fireworks, Ollama, LM
Studio, … There is a single concrete kind, ``OpenAiCompatibleProvider``; the
``openrouter`` kind is a documented alias of ``openai``. There is no other kind:
the harness's own native model is the final author, reached through the harness,
not configured as a panellist here.
"""
