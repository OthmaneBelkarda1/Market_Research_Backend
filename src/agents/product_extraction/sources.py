"""
Source contract
===============

Both extraction backends (Playwright and Apify) return the same object, so the
agent and the pipeline can treat them interchangeably.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SourceResult:
    """Raw material for one product, from one backend."""

    strategy: str                       # "playwright" | "apify"
    source: str                         # "playwright" | "apify:<actor-id>"
    url: str                            # requested URL
    final_url: str                      # after redirects (same as url for actors)
    fields: dict[str, Any] = field(default_factory=dict)
    # Keys in `fields` that were inferred (layout heuristics) rather than read
    # from declared markup or an actor's typed output. The LLM may overrule
    # these; everything else in `fields` outranks it.
    soft_fields: set[str] = field(default_factory=set)
    # Free-form evidence for the LLM: cleaned page text, or the actor's record
    # as JSON. This is what lets the model recover fields the parsers missed.
    context: str = ""
    raw: dict[str, Any] | None = None   # untouched actor record, when applicable
    warnings: list[str] = field(default_factory=list)
