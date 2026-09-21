"""Mandos - cross-harness multi-model deliberation MCP server.

The deliberation engine: fan a prompt out to a configurable panel of
OpenAI-compatible providers, put named questions about their answers to Jev, and
return the calibrated analysis plus all raw panel answers. The harness's own native
model authors the final answer. See docs/architecture/ for the design and current
contracts.
"""

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    # One source of truth, the installed distribution. A hardcoded copy here read
    # 0.0.0 while pyproject declared something else.
    __version__ = version("mandos")
except PackageNotFoundError:  # running from a checkout that was never installed
    __version__ = "0.0.0"
