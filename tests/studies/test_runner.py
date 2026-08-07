"""F8.2 -- the orchestrator, against the fake pipeline.

No test here touches the network, an LLM, or the real modules: the runner is pointed at
`tests/studies/fake_pipeline`, whose scripts hold the same CLI contract. What is tested is
therefore the wiring itself -- arguments, exit codes, stdout parsing, files, persistence.
"""

import asyncio
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.products.models import Product
from src.studies import runner, service
from src.studies.config import studies_settings
from src.studies.constants import (
    RunErrorCode,
    StudyAgent,
    StudyAnalysisStatus,
    StudySource,
    StudySourceStatus,
    StudyStatus,
)
from src.studies.models import Study, StudyAnalysis, StudyReport, StudySourceData
from src.studies.schemas import StudyCreate

from tests.studies.conftest import ProductFactory


async def _new_study(db: AsyncSession, product: Product, region: str = "MA") -> Study:
    """A study in ``created``, not yet executed (``launch_study`` is neutralized)."""
    return await service.create_study(db, StudyCreate(product_id=product.id, region=region))


async def _execute(db: AsyncSession, study: Study) -> Study:
    """Run the pipeline to the end, then re-read the row the runner wrote."""
    # The identifier is read before `expire_all`, which would otherwise turn the next
    # `study.id` into a lazy load in synchronous code.
    study_id = study.id
    await runner._run_study(study_id)
    db.expire_all()
    refreshed = await db.get(Study, study_id)
    assert refreshed is not None
    return refreshed


async def _sources(db: AsyncSession, study: Study) -> dict[str, StudySourceData]:
    rows = await db.scalars(select(StudySourceData).where(StudySourceData.study_id == study.id))
    return {row.source: row for row in rows}


async def _analyses(db: AsyncSession, study: Study) -> dict[str, StudyAnalysis]:
    rows = await db.scalars(select(StudyAnalysis).where(StudyAnalysis.study_id == study.id))
    return {row.agent: row for row in rows}


# ---------------------------------------------------------------------------
# 1. Nominal run
# ---------------------------------------------------------------------------
async def test_nominal_run(db_session: AsyncSession, product: Product, pipeline: Path) -> None:
    study = await _new_study(db_session, product)

    study = await _execute(db_session, study)

    assert study.status == StudyStatus.COMPLETED
    assert study.started_at is not None and study.finished_at is not None
    assert study.error is None
    # Language and currency come from the pipeline's own tables, never from the backend.
    assert study.langue == "fr"
    assert study.devise == "MAD"

    sources = await _sources(db_session, study)
    assert set(sources) == {source.value for source in StudySource}
    assert all(row.status == StudySourceStatus.SUCCEEDED for row in sources.values())
    # The payload is the module's JSON, stored as received.
    assert sources[StudySource.AMAZON].payload == {
        "module": "agent_amazon",
        "produits": [{"asin": "B0TEST", "prix": 199}],
    }
    assert sources[StudySource.AMAZON].exit_code == 0

    analyses = await _analyses(db_session, study)
    assert set(analyses) == {agent.value for agent in StudyAgent}
    assert all(row.status == StudyAnalysisStatus.SUCCEEDED for row in analyses.values())
    assert analyses[StudyAgent.F5_VERDICT].payload["verdict_potentiel"]["verdict"] == "positif"

    report = await db_session.scalar(select(StudyReport).where(StudyReport.study_id == study.id))
    assert report is not None
    assert report.rapport_markdown.startswith("# Rapport d'etude simule")
    assert report.resume_markdown is not None

    # progress carries every module plus the market resolution, with its reserve.
    assert study.progress["marche"]["langue"] == "fr"
    assert study.progress["marche"]["reserve"]
    assert study.progress[StudySource.REDDIT]["status"] == StudySourceStatus.SUCCEEDED
    assert study.progress[StudyAgent.F6_PLC]["status"] == StudyAnalysisStatus.SUCCEEDED
    assert study.progress["f7_rapport"]["status"] == "succeeded"


async def test_the_workdir_holds_what_the_pipeline_produced(
    db_session: AsyncSession, product: Product, pipeline: Path
) -> None:
    """The files are the pipeline's own: the analysis agents read them, and they settle
    any doubt about a sentence of the report."""
    study = await _execute(db_session, await _new_study(db_session, product))

    workdir = pipeline / str(study.id)
    produced = {path.name for path in workdir.iterdir()}
    assert {
        "tendances.json",
        "reddit.json",
        "aliexpress.json",  # written by the runner: these three only talk on stdout
        "recherche_web.json",
        "amazon.json",
        "meta_ads.json",  # written by the modules themselves through --sortie
        "insights.json",
        "concurrence.json",
        "recommandations.json",
        "plc.json",
        "rapport_etude.md",
        "resume_executif.md",
        "restitution.json",
    } <= produced
    assert json.loads((workdir / "reddit.json").read_text(encoding="utf-8"))["module"]


# ---------------------------------------------------------------------------
# 2 to 4. Degraded collection
# ---------------------------------------------------------------------------
async def test_region_not_covered_is_not_a_failure(
    db_session: AsyncSession, product: Product, pipeline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit code 3 -- Amazon has no Moroccan site. That is the market, not a defect."""
    monkeypatch.setenv("FAKE_AGENT_AMAZON_EXIT", "3")

    study = await _execute(db_session, await _new_study(db_session, product))

    sources = await _sources(db_session, study)
    assert sources[StudySource.AMAZON].status == StudySourceStatus.SKIPPED_REGION
    assert sources[StudySource.AMAZON].exit_code == 3
    assert study.status == StudyStatus.COMPLETED


async def test_failed_collector_yields_a_partial_study(
    db_session: AsyncSession, product: Product, pipeline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_AGENT_REDDIT_EXIT", "1")

    study = await _execute(db_session, await _new_study(db_session, product))

    sources = await _sources(db_session, study)
    assert sources[StudySource.REDDIT].status == StudySourceStatus.FAILED
    assert sources[StudySource.REDDIT].payload is None
    assert "echec simule" in sources[StudySource.REDDIT].error
    # The chain carried on: the analyses ran and a report exists.
    assert study.status == StudyStatus.PARTIAL
    assert await db_session.scalar(select(StudyReport).where(StudyReport.study_id == study.id))


async def test_a_collector_that_hangs_is_killed(
    db_session: AsyncSession, product: Product, pipeline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_AGENT_META_ADS_SLEEP", "30")
    monkeypatch.setattr(studies_settings, "TIMEOUT_COLLECTOR_SECONDS", 1.0)

    study = await _execute(db_session, await _new_study(db_session, product))

    sources = await _sources(db_session, study)
    assert sources[StudySource.META_ADS].status == StudySourceStatus.FAILED
    assert "killed after 1 s" in sources[StudySource.META_ADS].error
    assert sources[StudySource.META_ADS].duration_seconds < 30
    assert study.status == StudyStatus.PARTIAL


async def test_exit_zero_with_unparsable_stdout_is_a_failure(
    db_session: AsyncSession, product: Product, pipeline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken contract is reported as such, never stored as a payload."""
    monkeypatch.setenv("FAKE_AGENT_TENDANCES_STDOUT", "invalide")

    study = await _execute(db_session, await _new_study(db_session, product))

    sources = await _sources(db_session, study)
    assert sources[StudySource.GOOGLE_TRENDS].status == StudySourceStatus.FAILED
    assert "not JSON" in sources[StudySource.GOOGLE_TRENDS].error
    assert sources[StudySource.GOOGLE_TRENDS].payload is None


async def test_every_collector_failing_stops_the_study(
    db_session: AsyncSession, product: Product, pipeline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for source in StudySource:
        monkeypatch.setenv(f"FAKE_AGENT_{_module_of(source).upper()}_EXIT", "1")

    study = await _execute(db_session, await _new_study(db_session, product))

    assert study.status == StudyStatus.FAILED
    assert study.error["code"] == RunErrorCode.ALL_COLLECTORS_FAILED
    # No analysis was launched: there was nothing to analyse.
    assert not await _analyses(db_session, study)


def _module_of(source: StudySource) -> str:
    """Directory name of the collector behind a source."""
    return {
        StudySource.GOOGLE_TRENDS: "tendances",
        StudySource.REDDIT: "reddit",
        StudySource.RECHERCHE_WEB: "recherche_web",
        StudySource.AMAZON: "amazon",
        StudySource.META_ADS: "meta_ads",
        StudySource.ALIEXPRESS: "aliexpress",
    }[source]


# ---------------------------------------------------------------------------
# 5 and 6. Analysis and report
# ---------------------------------------------------------------------------
async def test_a_failed_report_fails_the_study(
    db_session: AsyncSession, product: Product, pipeline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No report, no deliverable: this is the one failure that is fatal."""
    monkeypatch.setenv("FAKE_AGENT_RESTITUTION_EXIT", "1")

    study = await _execute(db_session, await _new_study(db_session, product))

    assert study.status == StudyStatus.FAILED
    assert study.error["code"] == RunErrorCode.REPORT_FAILED
    assert not await db_session.scalar(select(StudyReport).where(StudyReport.study_id == study.id))
    # Everything collected before is kept.
    assert len(await _sources(db_session, study)) == len(StudySource)


async def test_plc_not_triggered_is_a_success(
    db_session: AsyncSession, product: Product, pipeline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-positive verdict makes F6 emit a short output in exit code 0. That is a
    result, not a failure -- and F7 renders it."""
    monkeypatch.setenv("FAKE_PLC_NON_DECLENCHE", "1")

    study = await _execute(db_session, await _new_study(db_session, product))

    analyses = await _analyses(db_session, study)
    assert analyses[StudyAgent.F6_PLC].status == StudyAnalysisStatus.SUCCEEDED
    assert analyses[StudyAgent.F6_PLC].payload["declenchement"]["mode"] == "non_declenche"
    assert study.status == StudyStatus.COMPLETED


async def test_a_failed_analysis_keeps_the_chain_going(
    db_session: AsyncSession, product: Product, pipeline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_AGENT_INSIGHTS_CONSOMMATEURS_EXIT", "2")

    study = await _execute(db_session, await _new_study(db_session, product))

    analyses = await _analyses(db_session, study)
    assert analyses[StudyAgent.F3_INSIGHTS].status == StudyAnalysisStatus.FAILED
    assert analyses[StudyAgent.F3_INSIGHTS].exit_code == 2
    assert analyses[StudyAgent.F5_VERDICT].status == StudyAnalysisStatus.SUCCEEDED
    assert study.status == StudyStatus.PARTIAL
    assert await db_session.scalar(select(StudyReport).where(StudyReport.study_id == study.id))


# ---------------------------------------------------------------------------
# 7. Currency
# ---------------------------------------------------------------------------
async def test_unmapped_currency_stops_before_any_module(
    db_session: AsyncSession, product: Product, pipeline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A guessed currency would skew the whole price benchmark, coherently enough that no
    check downstream could catch it. Nothing is launched, nothing is billed."""
    monkeypatch.setenv("FAKE_DEVISE_MARCHE_EXIT", "1")

    study = await _execute(db_session, await _new_study(db_session, product))

    assert study.status == StudyStatus.FAILED
    assert study.error["code"] == RunErrorCode.CURRENCY_NOT_MAPPED
    assert not await _sources(db_session, study)
    assert study.devise is None


async def test_an_explicit_language_skips_the_table(
    db_session: AsyncSession, product: Product, pipeline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A language given by the caller is used as-is -- the resolver is not even called."""
    monkeypatch.setenv("FAKE_LANGUES_MARCHE_EXIT", "1")
    study = await service.create_study(
        db_session, StudyCreate(product_id=product.id, region="MA", langue="es")
    )

    study = await _execute(db_session, study)

    assert study.langue == "es"
    assert study.status == StudyStatus.COMPLETED


# ---------------------------------------------------------------------------
# 8 and 9. Concurrency and restart
# ---------------------------------------------------------------------------
async def test_two_studies_are_serialized(
    db_session: AsyncSession,
    make_product: ProductFactory,
    pipeline: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One study at a time: a study costs ~2,3 $ of LLM credits and holds Apify quota."""
    trace = tmp_path / "trace.jsonl"
    monkeypatch.setenv("FAKE_PIPELINE_TRACE", str(trace))
    first = await _new_study(db_session, await make_product(name="Produit A"))
    second = await _new_study(db_session, await make_product(name="Produit B"))

    await asyncio.gather(runner._run_study(first.id), runner._run_study(second.id))

    events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    order = [event["cwd"] for event in events]
    # One contiguous block of events per study: the two runs never interleave.
    blocks = [cwd for index, cwd in enumerate(order) if index == 0 or order[index - 1] != cwd]
    assert blocks == [str(first.id), str(second.id)] or blocks == [str(second.id), str(first.id)]


async def test_a_restart_closes_the_studies_it_interrupted(
    db_session: AsyncSession, product: Product
) -> None:
    """Studies live in the memory of one worker: a restart kills them whatever the
    database says. Leaving one `collecting` would show a bar that can never move."""
    study = await _new_study(db_session, product)
    study_id = study.id
    await service.set_study_status(db_session, study, StudyStatus.COLLECTING)

    recovered = await service.recover_interrupted_studies(db_session)

    assert recovered == 1
    db_session.expire_all()
    study = await db_session.get(Study, study_id)
    assert study.status == StudyStatus.FAILED
    assert study.error["code"] == RunErrorCode.INTERRUPTED_BY_RESTART
    assert study.finished_at is not None


async def test_a_created_study_is_not_touched_by_the_restart(
    db_session: AsyncSession, product: Product
) -> None:
    """A study just created has no task behind it yet: it is not an interrupted run."""
    study_id = (await _new_study(db_session, product)).id

    assert await service.recover_interrupted_studies(db_session) == 0

    db_session.expire_all()
    assert (await db_session.get(Study, study_id)).status == StudyStatus.CREATED


# ---------------------------------------------------------------------------
# Wiring details worth pinning
# ---------------------------------------------------------------------------
async def test_a_vanished_study_is_not_an_exception(pipeline: Path) -> None:
    """The orchestrator survives a study deleted between creation and execution."""
    await runner._run_study(uuid.uuid4())


async def test_missing_credentials_are_warnings_not_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for names in ("ANTHROPIC_API_KEY", "APIFY_TOKEN", "APIFY_API_TOKEN", "SEL_ANONYMISATION"):
        monkeypatch.delenv(names, raising=False)

    missing = runner.check_pipeline_credentials()

    assert "ANTHROPIC_API_KEY" in missing
    assert "APIFY_TOKEN" in missing
