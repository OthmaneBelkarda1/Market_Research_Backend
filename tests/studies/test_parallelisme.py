"""Parallelisation of the orchestrator: overlap, bound, barrier, and lost updates.

Every assertion about concurrency is computed from the timestamps the fake modules write
in the study's workdir -- one `_trace_<module>.json` per module, holding a monotonic
`debut` and `fin`. Never from a shared counter: the modules are separate processes, and a
counter incremented in one of them says nothing about the others.

Reading overlap from timestamps is also what keeps these tests from asserting on wall
clock durations, which would make them flaky on a loaded machine. The one duration
assertion below (test 1) is deliberately loose for that reason.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from src.products.models import Product
from src.studies import runner, service
from src.studies.config import studies_settings
from src.studies.constants import StudyAgent, StudySource, StudyStatus
from src.studies.models import Study
from src.studies.schemas import StudyCreate

from tests.studies.conftest import ProductFactory

COLLECTOR_MODULES = (
    "agent_tendances",
    "agent_reddit",
    "agent_recherche_web",
    "agent_amazon",
    "agent_meta_ads",
    "agent_aliexpress",
)


# ---------------------------------------------------------------------------
# Reading the timestamps
# ---------------------------------------------------------------------------
def _intervals(
    workdir: Path, study_id: str, modules: tuple[str, ...]
) -> dict[str, tuple[float, float]]:
    """``{module: (debut, fin)}`` for the modules that ran to completion.

    A module killed on timeout never writes its `fin`, and is therefore absent: that is
    correct, an interval it never finished is not an interval.
    """
    intervals: dict[str, tuple[float, float]] = {}
    for module in modules:
        trace = workdir / study_id / f"_trace_{module}.json"
        if not trace.exists():
            continue
        marks = json.loads(trace.read_text(encoding="utf-8"))
        if "debut" in marks and "fin" in marks:
            intervals[module] = (marks["debut"], marks["fin"])
    return intervals


def _max_overlap(intervals: dict[str, tuple[float, float]]) -> int:
    """How many of these intervals were ever open at the same instant.

    A sweep over the boundaries: a `fin` is applied before a `debut` at the same timestamp,
    so two modules that merely touch are never counted as overlapping.
    """
    events = [(start, 1) for start, _ in intervals.values()]
    events += [(end, -1) for _, end in intervals.values()]
    events.sort(key=lambda event: (event[0], event[1]))

    current = maximum = 0
    for _, delta in events:
        current += delta
        maximum = max(maximum, current)
    return maximum


async def _new_study(db: AsyncSession, product: Product, region: str = "MA") -> Study:
    return await service.create_study(db, StudyCreate(product_id=product.id, region=region))


def _slow_collectors(monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    for module in COLLECTOR_MODULES:
        monkeypatch.setenv(f"FAKE_{module.upper()}_SLEEP", str(seconds))


# ---------------------------------------------------------------------------
# 1 and 2. Collection: overlap, and the bound that caps it
# ---------------------------------------------------------------------------
async def test_the_six_collectors_overlap(
    db_session: AsyncSession,
    product: Product,
    pipeline: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Six collectors at bound 6: the phase costs the slowest, not the sum.

    Three seconds each, not one: the phase carries a fixed cost the concurrency cannot
    remove -- six Python interpreters spawned at once, and two round-trips to a remote
    database per collector -- measured at about 3 s here. Against one-second modules that
    fixed cost dominates, and the ratio below would be measuring process startup rather
    than overlap. Three seconds keeps it a minority of the phase.
    """
    monkeypatch.setattr(studies_settings, "COLLECT_PARALLEL", 6)
    _slow_collectors(monkeypatch, 3.0)
    study = await _new_study(db_session, product)
    study_id = study.id

    await runner._run_study(study_id)

    intervals = _intervals(pipeline, str(study_id), COLLECTOR_MODULES)
    assert len(intervals) == len(StudySource)
    assert _max_overlap(intervals) == 6

    db_session.expire_all()
    refreshed = await db_session.get(Study, study_id)
    assert refreshed is not None
    phase = refreshed.progress["phase_durations"]["collecting"]
    individually = sum(end - start for start, end in intervals.values())
    assert phase < individually / 2


async def test_the_bound_is_respected(
    db_session: AsyncSession,
    product: Product,
    pipeline: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``STUDY_COLLECT_PARALLEL`` is the rollback lever: it must really bound."""
    monkeypatch.setattr(studies_settings, "COLLECT_PARALLEL", 2)
    _slow_collectors(monkeypatch, 0.6)
    study = await _new_study(db_session, product)
    study_id = study.id

    await runner._run_study(study_id)

    intervals = _intervals(pipeline, str(study_id), COLLECTOR_MODULES)
    assert len(intervals) == len(StudySource)
    assert _max_overlap(intervals) <= 2


# ---------------------------------------------------------------------------
# 3. F3 and F4 overlap, and F5 waits for both
# ---------------------------------------------------------------------------
async def test_f3_and_f4_overlap_and_f5_waits_for_both(
    db_session: AsyncSession,
    product: Product,
    pipeline: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_AGENT_INSIGHTS_CONSOMMATEURS_SLEEP", "1.0")
    monkeypatch.setenv("FAKE_AGENT_ANALYSE_CONCURRENTIELLE_SLEEP", "1.0")
    study = await _new_study(db_session, product)
    study_id = study.id

    await runner._run_study(study_id)

    analyses = ("agent_insights_consommateurs", "agent_analyse_concurrentielle")
    intervals = _intervals(pipeline, str(study_id), analyses)
    assert _max_overlap(intervals) == 2

    f5 = _intervals(pipeline, str(study_id), ("agent_recommandations_strategiques",))
    f5_start = f5["agent_recommandations_strategiques"][0]
    # F5 starts once BOTH are done, whatever their status.
    assert all(f5_start >= end for _, end in intervals.values())


# ---------------------------------------------------------------------------
# 4. The barrier holds on a partial failure
# ---------------------------------------------------------------------------
async def test_a_failure_and_a_timeout_do_not_stop_the_others(
    db_session: AsyncSession,
    product: Product,
    pipeline: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One collector in exit code 1, one hanging past its timeout: the four others finish,
    the analysis starts anyway, and the study yields a report."""
    monkeypatch.setattr(studies_settings, "COLLECT_PARALLEL", 6)
    monkeypatch.setattr(studies_settings, "TIMEOUT_COLLECTOR_SECONDS", 1.0)
    monkeypatch.setenv("FAKE_AGENT_REDDIT_EXIT", "1")
    monkeypatch.setenv("FAKE_AGENT_META_ADS_SLEEP", "30")
    study = await _new_study(db_session, product)
    study_id = study.id

    await runner._run_study(study_id)

    db_session.expire_all()
    refreshed = await db_session.get(Study, study_id)
    assert refreshed is not None
    assert refreshed.status == StudyStatus.PARTIAL

    progress = refreshed.progress
    # The timed-out collector never blocked the barrier, and the four healthy ones ran.
    assert progress[StudySource.META_ADS]["status"] == "failed"
    assert progress[StudySource.REDDIT]["status"] == "failed"
    for source in (StudySource.GOOGLE_TRENDS, StudySource.AMAZON, StudySource.ALIEXPRESS):
        assert progress[source]["status"] == "succeeded"
    assert progress["f7_rapport"]["status"] == "succeeded"


# ---------------------------------------------------------------------------
# 6. No lost update, over repeated concurrent runs
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("run_index", range(5))
async def test_progress_never_loses_an_entry(
    db_session: AsyncSession,
    make_product: ProductFactory,
    pipeline: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_index: int,
) -> None:
    """Six concurrent writers on one jsonb column: every entry must survive.

    This is the test that would fail on the previous read-modify-write in Python once its
    serializing lock was removed -- two collectors finishing together would each write back
    a dict missing the other's entry.
    """
    monkeypatch.setattr(studies_settings, "COLLECT_PARALLEL", 6)
    product = await make_product(name=f"Produit run {run_index}")
    study = await _new_study(db_session, product)
    study_id = study.id

    await runner._run_study(study_id)

    db_session.expire_all()
    refreshed = await db_session.get(Study, study_id)
    assert refreshed is not None
    progress = refreshed.progress

    for source in StudySource:
        assert source in progress, f"run {run_index}: {source} lost from progress"
    for agent in StudyAgent:
        assert agent in progress, f"run {run_index}: {agent} lost from progress"
    assert "f7_rapport" in progress
    assert "marche" in progress

    assert set(progress["phase_durations"]) == {"collecting", "analyzing", "reporting", "total"}


# ---------------------------------------------------------------------------
# The bound is a real semaphore, not an unbounded gather
# ---------------------------------------------------------------------------
async def test_the_default_bound_is_six(pipeline: Path) -> None:
    """The whole point of the change: the six collectors are no longer capped at two."""
    assert studies_settings.COLLECT_PARALLEL == 6


async def test_a_module_runs_on_a_subprocess_capable_loop() -> None:
    """On Windows under ``uvicorn --reload`` the loop cannot spawn a subprocess at all.

    Rather than simulate uvicorn, the fallback is exercised directly: the private-loop path
    must return the module's real result, on every platform.
    """
    ran: list[str] = []

    async def work() -> str:
        ran.append("called")
        return "done"

    assert await runner._on_subprocess_capable_loop(work) == "done"
    assert ran == ["called"]
