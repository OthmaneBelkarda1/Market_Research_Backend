"""Settings scoped to the ``studies`` domain (``STUDY_`` prefix)."""

import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.studies.constants import DEFAULT_ALLOWED_REGIONS


class StudyConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="STUDY_", extra="ignore")

    # False: products are still stored, but no study is ever created automatically.
    # Manual creation through POST /studies keeps working.
    AUTO_START: bool = True

    # --- Execution of the pipeline (see docs/pipeline_contrats.md) ------------
    # Where the pipeline lives. The test suite repoints this at a fake pipeline
    # sharing the same layout: this is the substitution mechanism, and the reason
    # no module path is ever hard-coded outside `constants.py`.
    PIPELINE_ROOT: Path = Path("src/agents/market_study")

    # Interpreter running the modules. Empty: the one running the app, as required.
    # An escape hatch: the pipeline was validated on Python 3.14, this app runs on
    # 3.12 with the same package versions.
    PYTHON_EXECUTABLE: str = ""

    # One study at a time. A study costs ~2,3 $ of LLM credits, Apify quota, and
    # holds several hundred MB: this bounds spend and memory, not just load.
    MAX_CONCURRENCY: int = Field(default=1, ge=1)
    # How many collectors run at once. They are independent by design (disjoint
    # sources, disjoint output files, no module reads another's), so the phase costs
    # the slowest of a batch rather than the sum of all six.
    #
    # Six was the default until an instance on Render was killed for exceeding its
    # memory limit. RAM is the binding constraint, and it was the one ceiling the
    # original note listed without measuring: a collector is not a coroutine but a
    # whole Python subprocess (`runner._spawn_module`), each re-importing the LLM and
    # scraping stack from scratch, sharing nothing with the API process. Six of those
    # plus the API is the peak that did not fit. Two does.
    #
    # The API cost is unchanged either way -- same calls, same tokens, spread over
    # more wall-clock. Nothing degrades: all six collectors still run, in batches.
    #
    # This stays the adjustment lever, with no redeployment. Raise it on an instance
    # with the memory to back it, and watch the peak before trusting the new value;
    # the other ceilings it has to clear are concurrent Apify actor runs (five of the
    # six go through Apify, capped by the account plan) and Anthropic rate limits.
    COLLECT_PARALLEL: int = Field(default=2, ge=1)

    # Measured on the reference run: 13 min for the six collectors, and 666 s for the
    # slowest analysis module (F4). Both defaults leave a wide margin.
    TIMEOUT_COLLECTOR_SECONDS: float = Field(default=1200.0, gt=0)
    TIMEOUT_ANALYSIS_SECONDS: float = Field(default=1800.0, gt=0)
    # Language and currency are table lookups: no network, no LLM, milliseconds.
    TIMEOUT_RESOLVER_SECONDS: float = Field(default=60.0, gt=0)

    # The two cost levers of the collection phase: Amazon bills one actor run per
    # enriched product, Meta bills per ad. Lower them for a cheap validation run.
    AMAZON_AVIS: int = Field(default=5, ge=0)
    META_ANNONCES: int = Field(default=30, ge=1)

    # One directory per study, holding the pipeline's own files. Kept by default:
    # the JSON files are what settle any doubt about a sentence of the report.
    WORKDIR: Path = Path("var/studies")
    KEEP_WORKDIR: bool = True

    # Tail of stderr persisted when a module fails. Enough to diagnose, short enough
    # to stay readable in an API response.
    STDERR_TAIL_LINES: int = Field(default=50, ge=1)

    # Comma-separated on purpose: pydantic-settings parses a `set[str]`/`list[str]` field
    # as JSON, which would reject the natural `STUDY_ALLOWED_REGIONS=MA,FR`.
    ALLOWED_REGIONS: str = DEFAULT_ALLOWED_REGIONS

    @property
    def allowed_regions(self) -> frozenset[str]:
        """The region whitelist, normalized. Empty entries are ignored."""
        return frozenset(
            part.strip().upper() for part in self.ALLOWED_REGIONS.split(",") if part.strip()
        )

    @property
    def sorted_allowed_regions(self) -> list[str]:
        """Stable order, so error messages and OpenAPI examples stay reproducible."""
        return sorted(self.allowed_regions)

    @property
    def python_executable(self) -> str:
        """The interpreter the pipeline modules are launched with."""
        return self.PYTHON_EXECUTABLE or sys.executable


studies_settings = StudyConfig()
