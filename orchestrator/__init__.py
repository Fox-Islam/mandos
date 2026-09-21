"""Mandos - cross-harness multi-model deliberation MCP server.

The deliberation engine: fan a prompt out to a configurable panel of
OpenAI-compatible providers, put named questions about their answers to Jev, and
return the calibrated analysis plus all raw panel answers. The harness's own native
model authors the final answer. See docs/architecture/ for the design and current
contracts.
"""

__all__ = ["__version__"]

__version__ = "0.0.0"
