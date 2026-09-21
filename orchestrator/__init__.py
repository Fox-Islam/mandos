"""Mandos — cross-harness multi-model deliberation (Fusion) MCP server.

The deliberation engine: fan a prompt out to a configurable panel of
OpenAI-compatible providers, run an API-side analysis judge for structured
analysis, return all raw panel answers, and let the harness's own native model
author the final answer. See docs/architecture/ for the design and current contracts.
"""

__all__ = ["__version__"]

__version__ = "0.0.0"
