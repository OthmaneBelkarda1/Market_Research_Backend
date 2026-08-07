"""Constants, enumerations and error codes of the ``studies`` domain.

The enumerations below are the single source of truth for the values allowed both by the
Pydantic schemas and by the CHECK constraints of the migration.
"""

from dataclasses import dataclass
from enum import StrEnum


class StudyStatus(StrEnum):
    """Lifecycle of a study. Transitions are performed by ``service.set_study_status``."""

    CREATED = "created"
    COLLECTING = "collecting"
    ANALYZING = "analyzing"
    REPORTING = "reporting"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


# A study in one of these statuses is under way: it holds the (product, region) lock.
# A finished study never blocks a new one -- relaunching is an expected use case.
ACTIVE_STUDY_STATUSES: frozenset[str] = frozenset(
    {
        StudyStatus.CREATED,
        StudyStatus.COLLECTING,
        StudyStatus.ANALYZING,
        StudyStatus.REPORTING,
    }
)

# A study actually being executed by a process: `created` is excluded on purpose, since a
# study just created has no task behind it yet. This is what the restart recovery targets.
ACTIVE_RUNNING_STATUSES: frozenset[str] = frozenset(
    {StudyStatus.COLLECTING, StudyStatus.ANALYZING, StudyStatus.REPORTING}
)


class StudySource(StrEnum):
    """The six collectors of the pipeline -- one row of ``study_source_data`` each."""

    GOOGLE_TRENDS = "google_trends"
    REDDIT = "reddit"
    RECHERCHE_WEB = "recherche_web"
    ALIEXPRESS = "aliexpress"
    AMAZON = "amazon"
    META_ADS = "meta_ads"


class StudySourceStatus(StrEnum):
    """``skipped_region`` is a normal outcome (exit code 3), not a failure: a collector
    may simply not cover the region (Amazon has no Moroccan site, for instance)."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED_REGION = "skipped_region"


class StudyAgent(StrEnum):
    """The four analysis agents -- one row of ``study_analysis`` each. F7 (the report)
    has its own table because it produces text, not an analysis payload."""

    F3_INSIGHTS = "f3_insights"
    F4_CONCURRENCE = "f4_concurrence"
    F5_VERDICT = "f5_verdict"
    F6_PLC = "f6_plc"


class StudyAnalysisStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StudyTrigger(StrEnum):
    """What caused the study to be created."""

    PRODUCTS = "products"
    EXTRACTIONS = "extractions"
    MANUAL = "manual"


# ISO 3166-1 alpha-2, uppercase (region) and ISO 639-1, lowercase (language). The region
# whitelist itself is configuration, hence a validator rather than a Literal type.
REGION_PATTERN = r"^[A-Z]{2}$"
LANGUE_PATTERN = r"^[a-z]{2}$"

# Aligned on the products domain (EXTRACTION_ALLOWED_REGIONS): the same countries are
# extractable and studiable today. The two settings stay distinct so they may diverge.
DEFAULT_ALLOWED_REGIONS = "MA,FR,ES,US,AE"

# Pagination of GET /studies. The upper bound is what keeps a sidebar query cheap.
DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 100


# ---------------------------------------------------------------------------
# Wiring of the pipeline modules -- see docs/pipeline_contrats.md
# ---------------------------------------------------------------------------
# Only `runner.py` consumes what follows. The paths are relative to the pipeline
# root (STUDY_PIPELINE_ROOT), which the tests repoint at a fake pipeline sharing
# the same layout: that indirection is the whole substitution mechanism.

LANGUE_RESOLVER_SCRIPT = "langues_marche.py"
DEVISE_RESOLVER_SCRIPT = "devise_marche.py"


@dataclass(frozen=True, slots=True)
class CollectorSpec:
    """A collector: how to call it, and where its JSON must land."""

    source: StudySource
    script: str
    output_file: str
    # True: the module only ever writes to stdout, so the runner writes the workdir
    # file itself. False: it takes `--sortie`, and needs `--stdout` to talk as well.
    stdout_only: bool


COLLECTORS: tuple[CollectorSpec, ...] = (
    CollectorSpec(StudySource.GOOGLE_TRENDS, "agent_tendances/main.py", "tendances.json", True),
    CollectorSpec(StudySource.REDDIT, "agent_reddit/main.py", "reddit.json", True),
    CollectorSpec(
        StudySource.RECHERCHE_WEB, "agent_recherche_web/main.py", "recherche_web.json", False
    ),
    CollectorSpec(StudySource.AMAZON, "agent_amazon/main.py", "amazon.json", False),
    CollectorSpec(StudySource.META_ADS, "agent_meta_ads/main.py", "meta_ads.json", False),
    CollectorSpec(StudySource.ALIEXPRESS, "agent_aliexpress/main.py", "aliexpress.json", True),
)


@dataclass(frozen=True, slots=True)
class AnalysisSpec:
    """An analysis agent, and the upstream files it consumes.

    ``inputs`` maps a command-line flag to the workdir file that feeds it. A flag is
    only passed when its file exists: a missing input degrades the analysis, which the
    module reports in its own ``limites`` -- it never fabricates the missing part.
    ``required`` names the flags without which the module cannot run at all.
    """

    agent: StudyAgent
    script: str
    output_file: str
    inputs: tuple[tuple[str, str], ...]
    required: frozenset[str] = frozenset()


ANALYSES: tuple[AnalysisSpec, ...] = (
    AnalysisSpec(
        StudyAgent.F3_INSIGHTS,
        "agent_insights_consommateurs/main.py",
        "insights.json",
        (
            ("--reddit", "reddit.json"),
            ("--amazon", "amazon.json"),
            ("--recherche-web", "recherche_web.json"),
        ),
    ),
    AnalysisSpec(
        StudyAgent.F4_CONCURRENCE,
        "agent_analyse_concurrentielle/main.py",
        "concurrence.json",
        (
            ("--aliexpress", "aliexpress.json"),
            ("--amazon", "amazon.json"),
            ("--meta-ads", "meta_ads.json"),
            ("--recherche-web", "recherche_web.json"),
        ),
    ),
    AnalysisSpec(
        StudyAgent.F5_VERDICT,
        "agent_recommandations_strategiques/main.py",
        "recommandations.json",
        (
            ("--insights", "insights.json"),
            ("--concurrence", "concurrence.json"),
            ("--tendances", "tendances.json"),
        ),
    ),
    AnalysisSpec(
        StudyAgent.F6_PLC,
        "agent_plc/main.py",
        "plc.json",
        (
            ("--recommandations", "recommandations.json"),
            ("--insights", "insights.json"),
            ("--concurrence", "concurrence.json"),
        ),
        required=frozenset({"--recommandations"}),
    ),
)

REPORT_SPEC = AnalysisSpec(
    StudyAgent.F5_VERDICT,  # unused: F7 has its own table, not a study_analysis row
    "agent_restitution/main.py",
    "restitution.json",
    (
        ("--recommandations", "recommandations.json"),
        ("--insights", "insights.json"),
        ("--concurrence", "concurrence.json"),
        ("--plc", "plc.json"),
    ),
    required=frozenset({"--recommandations"}),
)
REPORT_MARKDOWN_FILE = "rapport_etude.md"
REPORT_SUMMARY_FILE = "resume_executif.md"

# Exit codes, shared by every module of the pipeline.
EXIT_SUCCESS = 0
EXIT_RUNTIME_ERROR = 1
EXIT_UNUSABLE_INPUT = 2
EXIT_REGION_NOT_COVERED = 3

# Credentials the pipeline needs. Checked at startup: a missing one is a warning per
# key, never a boot failure -- the API serves everything else perfectly well without.
REQUIRED_PIPELINE_CREDENTIALS: tuple[tuple[str, ...], ...] = (
    ("ANTHROPIC_API_KEY",),
    ("APIFY_TOKEN", "APIFY_API_TOKEN"),  # either one; the pipeline falls back on the second
    ("SEL_ANONYMISATION",),
    ("ALIEXPRESS_APP_KEY",),
    ("ALIEXPRESS_APP_SECRET",),
    ("ALIEXPRESS_ACCESS_TOKEN",),
    ("ALIEXPRESS_REFRESH_TOKEN",),
)


class ErrorCode(StrEnum):
    STUDY_NOT_FOUND = "No study found for this identifier."
    STUDY_SOURCE_NOT_FOUND = (
        "This study holds no data for this collector: it has not run yet, or the study "
        "never reached the collection stage."
    )
    PRODUCT_NOT_FOUND = "No product sheet found for this identifier."
    REGION_REQUIRED = (
        "No region for this study: none was sent and the product sheet does not carry a "
        "usable ISO 3166-1 alpha-2 region. A region is never inferred."
    )
    STUDY_ALREADY_RUNNING = (
        "A study is already running for this product and region. Wait for it to finish "
        "before starting another one."
    )


class RunErrorCode(StrEnum):
    """``study.error["code"]`` -- why a study could not be run to completion.

    Persisted, so they are part of the API contract. Messages carry no credential and
    no stderr fragment that could hold one.
    """

    CURRENCY_NOT_MAPPED = "CURRENCY_NOT_MAPPED"
    LANGUAGE_NOT_RESOLVED = "LANGUAGE_NOT_RESOLVED"
    PRODUCT_NOT_FOUND = "PRODUCT_NOT_FOUND"
    ALL_COLLECTORS_FAILED = "ALL_COLLECTORS_FAILED"
    REPORT_FAILED = "REPORT_FAILED"
    INTERRUPTED_BY_RESTART = "INTERRUPTED_BY_RESTART"
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"
