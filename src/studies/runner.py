"""Execution of the market study pipeline -- the only module of the backend that knows
the pipeline exists.

Everything specific to `src/agents/market_study` lives here or in the specs of
`constants.py`: paths, command lines, exit codes, file names. The rest of the domain only
ever calls ``launch_study``.

Three principles, none of them negotiable:

* **The pipeline is never modified, and never imported.** Its eleven modules are
  standalone executables whose contract is JSON on stdout and an exit code. They are
  invoked as subprocesses, with explicit arguments -- never through a shell.
* **Everything is persisted as it happens.** Each module's output is written to the
  database the moment it is received, and ``study.progress`` is updated at every
  transition. A crash mid-study leaves in place everything already collected.
* **A degraded result is a result.** A failed collector, a region without an Amazon site,
  a negative verdict: the chain carries on and says so. Only a failed report, or a study
  that cannot start at all, is a failure.

See `docs/pipeline_contrats.md` for the module-by-module contract this file wires.
"""

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import SessionFactory
from src.products.models import Product
from src.studies import service
from src.studies.config import studies_settings
from src.studies.constants import (
    ANALYSES,
    COLLECTORS,
    DEVISE_RESOLVER_SCRIPT,
    EXIT_REDACTION_IMPOSSIBLE,
    EXIT_REGION_NOT_COVERED,
    EXIT_SUCCESS,
    EXIT_UNUSABLE_INPUT,
    LANGUE_RESOLVER_SCRIPT,
    MODULE_LOG_DIR,
    REPORT_MARKDOWN_FILE,
    REPORT_SPEC,
    REPORT_SUMMARY_FILE,
    REQUIRED_PIPELINE_CREDENTIALS,
    SOURCES_STATE_FILE,
    AnalysisSpec,
    CollectorSpec,
    RunErrorCode,
    StudyAgent,
    StudyAnalysisStatus,
    StudySourceStatus,
    StudyStatus,
)
from src.studies.models import Study, StudyAnalysis, StudyReport, StudySourceData

logger = logging.getLogger(__name__)

# Studies run in-process, so the semaphore and the task set only make sense within one
# uvicorn worker. That single-worker constraint is documented in the README.
_semaphore: asyncio.Semaphore | None = None
_running: set[asyncio.Task[None]] = set()


def _study_semaphore() -> asyncio.Semaphore:
    """The global concurrency gate, created on the loop that first needs it."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(studies_settings.MAX_CONCURRENCY)
    return _semaphore


def check_pipeline_credentials() -> list[str]:
    """Report the credentials the pipeline needs and does not have.

    Called at startup: a missing key is a warning, never a boot failure. Everything the
    API does outside a study keeps working, and the module that needs the key will say so
    itself, in its own words, when it runs.
    """
    missing = [
        names[0] for names in REQUIRED_PIPELINE_CREDENTIALS if not any(os.getenv(n) for n in names)
    ]
    for name in missing:
        logger.warning(
            "Pipeline credential %s is not set: the modules that need it will fail "
            "(the rest of the study still runs)",
            name,
        )
    return missing


# ---------------------------------------------------------------------------
# Subprocess plumbing
# ---------------------------------------------------------------------------
# Windows + `uvicorn --reload` (or `--workers > 1`) forces a SelectorEventLoop, which
# cannot create subprocesses at all -- `create_subprocess_exec` raises NotImplementedError
# on it. Every one of the eleven pipeline modules is a subprocess, so on that loop a study
# fails on its very first module and never starts.
#
# `src/products/extraction.py` hits the same wall with the Playwright driver and solves it
# the same way. The fallback is per module rather than per study: the database engine and
# its connection pool are bound to the main loop, and moving the whole study onto a private
# loop would drag every session with it.
_SELECTOR_LOOP_NOTICE = (
    "This event loop cannot spawn subprocesses (Windows + uvicorn --reload/--workers>1 "
    "forces a SelectorEventLoop). Running the pipeline module on a dedicated "
    "ProactorEventLoop thread instead."
)


def _loop_can_spawn_subprocesses() -> bool:
    """Whether the running loop can start a pipeline module."""
    if sys.platform != "win32":
        return True
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return True  # no loop yet: nothing to rule out
    return isinstance(loop, asyncio.ProactorEventLoop)


async def _on_subprocess_capable_loop(make_coroutine: Callable[[], Awaitable[Any]]) -> Any:
    """Await ``make_coroutine()`` on a loop that can actually spawn a subprocess.

    Normally -- Linux, macOS, and Windows without ``--reload`` -- this is a plain ``await``
    on the caller's own loop, and nothing at all is added.

    ``make_coroutine`` is a factory rather than a coroutine so the coroutine object is
    created inside the target loop.

    Caveat, identical to the extraction one: the module's own ``asyncio.wait_for`` timeout
    still applies inside the private loop, but the thread itself cannot be killed. The
    collector's semaphore slot is only freed once that thread really returns.
    """
    if _loop_can_spawn_subprocesses():
        return await make_coroutine()

    logger.warning(_SELECTOR_LOOP_NOTICE)

    def run_on_private_loop() -> Any:
        loop = asyncio.ProactorEventLoop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(make_coroutine())
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    return await asyncio.to_thread(run_on_private_loop)


@dataclass(slots=True)
class ModuleRun:
    """What one module did: its exit code, its JSON, and why it failed if it did."""

    exit_code: int
    duration_seconds: float
    payload: dict[str, Any] | None = None
    error: str | None = None
    timed_out: bool = False
    # Where the module's stdout was captured. The collectors that only talk on stdout
    # promote this file to their output file, instead of the runner re-serializing the
    # parsed payload -- one full copy of the JSON that never has to exist.
    stdout_path: Path | None = None

    @property
    def succeeded(self) -> bool:
        return self.exit_code == EXIT_SUCCESS and self.payload is not None

    @property
    def skipped_region(self) -> bool:
        return self.exit_code == EXIT_REGION_NOT_COVERED


async def _run_module(
    script: str,
    args: list[str],
    *,
    workdir: Path,
    timeout_seconds: float,
    parse_stdout: bool = True,
) -> ModuleRun:
    """Run one pipeline module and bring back what it said.

    The single entry point for every module launch, so the event-loop fallback above is
    applied uniformly and cannot be forgotten at one call site.
    """
    return await _on_subprocess_capable_loop(
        lambda: _spawn_module(
            script,
            args,
            workdir=workdir,
            timeout_seconds=timeout_seconds,
            parse_stdout=parse_stdout,
        )
    )


async def _spawn_module(
    script: str,
    args: list[str],
    *,
    workdir: Path,
    timeout_seconds: float,
    parse_stdout: bool,
) -> ModuleRun:
    """Spawn the module, wait for it, and read what it produced.

    Both streams are redirected to files in ``workdir/logs`` rather than to pipes.
    That is a memory decision, not a tidiness one: with pipes, ``communicate()``
    accumulates everything a module writes until it exits, and the runner then held
    three representations of the same payload at once -- the raw bytes, the decoded
    string, and the parsed dict -- times ``COLLECT_PARALLEL`` collectors finishing
    together. A collector emits the whole Amazon review set on stdout, and every
    module talks a lot on stderr with ``--verbose``. That peak is what pushed the
    service past its memory limit on Render. Written to disk, stdout is read back
    once and stderr is never fully loaded at all (see ``_tail_file``).

    It also retires the pipe-buffer deadlock the previous implementation had to
    dodge: with no pipe to drain, ``process.wait()`` is safe, and reading the two
    streams concurrently is no longer a correctness requirement.

    The working directory is the study's, because three modules write ``output.json``
    relative to it when no ``--sortie`` is given.
    """
    executable = studies_settings.python_executable
    script_path = studies_settings.PIPELINE_ROOT.resolve() / script
    started = datetime.now(UTC)

    # `os.environ` carries the credentials; the two UTF-8 flags stop Windows from
    # decoding the modules' French output through the ANSI code page.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

    slug = script.replace("/", "_").replace("\\", "_").removesuffix(".py")
    log_dir = workdir / MODULE_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{slug}.stdout.json"
    stderr_path = log_dir / f"{slug}.stderr.log"

    timed_out = False
    # The handles are the subprocess's stdout and stderr: they must stay open for as
    # long as it runs, and be closed before anything reads what it wrote.
    with stdout_path.open("wb") as out_handle, stderr_path.open("wb") as err_handle:
        process = await asyncio.create_subprocess_exec(
            executable,
            str(script_path),
            *args,
            cwd=str(workdir),
            env=env,
            stdout=out_handle,
            stderr=err_handle,
        )

        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        except TimeoutError:
            timed_out = True
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.kill()
                await process.wait()

    duration = (datetime.now(UTC) - started).total_seconds()
    exit_code = process.returncode if process.returncode is not None else 1

    if timed_out:
        return ModuleRun(
            exit_code=exit_code,
            duration_seconds=duration,
            error=f"Module killed after {timeout_seconds:.0f} s (STUDY_TIMEOUT_*_SECONDS).",
            timed_out=True,
        )

    stderr_tail = _tail_file(stderr_path)
    if exit_code != EXIT_SUCCESS:
        return ModuleRun(exit_code=exit_code, duration_seconds=duration, error=stderr_tail)

    if not parse_stdout:
        return ModuleRun(exit_code=exit_code, duration_seconds=duration, payload={})

    try:
        payload = json.loads(stdout_path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exception:
        # Exit code 0 with unparsable stdout is a contract breach, not a module failure:
        # say so plainly instead of storing something the pipeline never produced.
        return ModuleRun(
            exit_code=exit_code,
            duration_seconds=duration,
            error=f"Exit code 0 but stdout is not JSON ({exception}). stderr: {stderr_tail}",
        )
    if not isinstance(payload, dict):
        return ModuleRun(
            exit_code=exit_code,
            duration_seconds=duration,
            error=f"Exit code 0 but stdout is a {type(payload).__name__}, not a JSON object.",
        )
    return ModuleRun(
        exit_code=exit_code,
        duration_seconds=duration,
        payload=payload,
        stdout_path=stdout_path,
    )


def _tail_file(path: Path, *, max_bytes: int = 64 * 1024) -> str:
    """The last lines of a module's stderr log, read from the end of the file.

    Only the tail is loaded. Reading the file whole would put back exactly what
    moving off pipes removed: a verbose module writes megabytes of progress log, and
    none of it past the last few lines is ever used.
    """
    try:
        size = path.stat().st_size
    except OSError:  # the module died before writing anything
        return ""
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
            handle.readline()  # drop the partial line the seek landed in the middle of
        chunk = handle.read()
    return _tail(chunk.decode("utf-8", errors="replace"))


def _tail(text: str) -> str:
    """The last lines of stderr, which is where a module explains itself."""
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-studies_settings.STDERR_TAIL_LINES :])


# ---------------------------------------------------------------------------
# Language and currency -- two deterministic tables of the pipeline
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class MarketResolution:
    """Language and currency of a market, as the pipeline's own tables give them."""

    langue: str
    devise: str
    langue_payload: dict[str, Any] = field(default_factory=dict)
    devise_payload: dict[str, Any] = field(default_factory=dict)


async def _resolve_market(region: str, langue: str | None, workdir: Path) -> MarketResolution:
    """Ask the pipeline for the language and the currency of ``region``.

    Neither is ever guessed here. A country absent from either table stops the study: a
    currency invented would skew the whole downstream price benchmark, coherently enough
    that no check could catch it.

    The language table holds exactly one language per country. A multilingual market is
    N complete studies, never an average -- but the pipeline never asks for them on its
    own, so neither does this runner.
    """
    devise_run = await _run_module(
        DEVISE_RESOLVER_SCRIPT,
        ["--geo", region],
        workdir=workdir,
        timeout_seconds=studies_settings.TIMEOUT_RESOLVER_SECONDS,
    )
    if not devise_run.succeeded:
        raise StudyNotRunnableError(
            RunErrorCode.CURRENCY_NOT_MAPPED,
            f"No currency for region {region}: it is absent from the pipeline's table.",
        )
    devise = str(devise_run.payload.get("devise", "")).strip().upper() if devise_run.payload else ""
    if not devise:
        raise StudyNotRunnableError(
            RunErrorCode.CURRENCY_NOT_MAPPED,
            f"The currency table returned no currency for region {region}.",
        )

    if langue:
        return MarketResolution(
            langue=langue, devise=devise, devise_payload=devise_run.payload or {}
        )

    langue_run = await _run_module(
        LANGUE_RESOLVER_SCRIPT,
        ["--geo", region],
        workdir=workdir,
        timeout_seconds=studies_settings.TIMEOUT_RESOLVER_SECONDS,
    )
    if not langue_run.succeeded or not langue_run.payload:
        raise StudyNotRunnableError(
            RunErrorCode.LANGUAGE_NOT_RESOLVED,
            f"No language for region {region}: it is absent from the pipeline's table.",
        )
    codes = [str(code).strip().lower() for code in langue_run.payload.get("codes", []) if code]
    if not codes:
        raise StudyNotRunnableError(
            RunErrorCode.LANGUAGE_NOT_RESOLVED,
            f"The language table returned no language for region {region}.",
        )

    # The table flags markets where the mother tongue is not the one typed into a search
    # engine. Ignoring it yields an empty corpus with no module in error: it is logged and
    # persisted with the study, never swallowed.
    if reserve := langue_run.payload.get("reserve"):
        logger.warning("Market to arbitrate for region %s: %s", region, reserve)

    return MarketResolution(
        langue=codes[0],
        devise=devise,
        langue_payload=langue_run.payload,
        devise_payload=devise_run.payload or {},
    )


class StudyNotRunnableError(Exception):
    """The study cannot start at all -- no module is launched, nothing is billed."""

    def __init__(self, code: RunErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
async def launch_study(study_id: uuid.UUID) -> None:
    """Start the pipeline for a study, in the background.

    Returns as soon as the task is scheduled: a study runs for 30 to 60 minutes, which is
    why this is an ``asyncio.Task`` with its state in the database, and not a
    ``BackgroundTask`` with its state in memory.
    """
    task = asyncio.create_task(_run_study(study_id), name=f"study-{study_id}")
    # Hold a reference: the event loop only keeps a weak one, and a garbage-collected
    # task disappears mid-study without a word.
    _running.add(task)
    task.add_done_callback(_running.discard)


async def _run_study(study_id: uuid.UUID) -> None:
    """Run one study end to end, under the global semaphore.

    Its own database session: the request that created the study is long gone by the time
    the first collector answers.
    """
    async with _study_semaphore(), SessionFactory() as db:
        try:
            await _execute(db, study_id)
        except StudyNotRunnableError as exception:
            logger.warning("Study %s cannot run (%s): %s", study_id, exception.code, exception)
            await _fail_study(db, study_id, exception.code, exception.message)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Study %s stopped on an unexpected error", study_id)
            await _fail_study(
                db,
                study_id,
                RunErrorCode.UNEXPECTED_ERROR,
                "The study stopped on an unexpected server error.",
            )


async def _execute(db: AsyncSession, study_id: uuid.UUID) -> None:
    study = await db.get(Study, study_id)
    if study is None:
        logger.error("Study %s vanished before it could run", study_id)
        return
    product = await db.get(Product, study.product_id)
    if product is None:
        raise StudyNotRunnableError(
            RunErrorCode.PRODUCT_NOT_FOUND, "The product sheet of this study no longer exists."
        )

    workdir = (studies_settings.WORKDIR / str(study_id)).resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    await service.set_study_status(db, study, StudyStatus.COLLECTING, started_at=datetime.now(UTC))
    study_started = time.monotonic()

    market = await _resolve_market(study.region, study.langue, workdir)
    study.langue = market.langue
    study.devise = market.devise
    await db.commit()
    await _update_progress(
        db,
        study_id,
        "marche",
        {
            "langue": market.langue,
            "devise": market.devise,
            "reserve": market.langue_payload.get("reserve"),
        },
    )
    logger.info(
        "Study %s: region=%s langue=%s devise=%s",
        study_id,
        study.region,
        study.langue,
        study.devise,
    )

    base_args = [
        "--nom",
        product.name,
        "--description",
        product.description,
        "--categorie",
        product.category,
        "--geo",
        study.region,
        "--langue",
        market.langue,
    ]

    # The barrier: `_collect` returns only once all six tasks are done, whatever their
    # status. Nothing downstream starts on a partial collection.
    phase_started = time.monotonic()
    collected, collect_durations = await _collect(study_id, market.devise, base_args, workdir)
    durations = {"collecting": time.monotonic() - phase_started}
    await _record_phase_durations(db, study_id, durations)

    if not any(status == StudySourceStatus.SUCCEEDED for status in collected.values()):
        raise StudyNotRunnableError(
            RunErrorCode.ALL_COLLECTORS_FAILED,
            "Every collector failed: there is nothing to analyse.",
        )

    await service.set_study_status(db, study, StudyStatus.ANALYZING)
    phase_started = time.monotonic()
    analysed, analysis_durations = await _analyse(db, study_id, workdir)
    durations["analyzing"] = time.monotonic() - phase_started
    await _record_phase_durations(db, study_id, durations)

    await service.set_study_status(db, study, StudyStatus.REPORTING)
    phase_started = time.monotonic()
    report_ok, report_duration, report_error = await _report(db, study_id, workdir)
    durations["reporting"] = time.monotonic() - phase_started
    durations["total"] = time.monotonic() - study_started
    await _record_phase_durations(db, study_id, durations)

    _log_recap(study_id, durations, {**collect_durations, **analysis_durations}, report_duration)

    failures = [s for s in collected.values() if s == StudySourceStatus.FAILED]
    failures += [s for s in analysed.values() if s == StudyAnalysisStatus.FAILED]

    if not report_ok and report_error == RunErrorCode.F7_REDACTION_FAILED:
        # `partial`, et non `failed` : les JSON F3 à F6 sont en base et intacts, seule
        # la mise en récit a échoué. L'étude est réparable par un rejeu de F7 seul --
        # `POST /studies/{id}/report/regenerate` -- sans repayer une collecte.
        await service.set_study_status(
            db,
            study,
            StudyStatus.PARTIAL,
            error={
                "code": RunErrorCode.F7_REDACTION_FAILED,
                "message": (
                    "The write-up did not meet the v2 template: the analyses are "
                    "intact and the report can be replayed on its own."
                ),
            },
            finished_at=datetime.now(UTC),
        )
        logger.warning("Study %s: report refused by F7, analyses kept", study_id)
        return

    if not report_ok:
        await _fail_study(
            db,
            study_id,
            RunErrorCode.REPORT_FAILED,
            "The report could not be produced: the study has no deliverable.",
        )
        return

    # `skipped_region` never counts as a failure: a country without an Amazon site is a
    # normal outcome of the market, not a defect of the study.
    # Une source `empty` rend l'étude partielle : elle a tourné sans rien rapporter, et
    # le rapport porte alors son encart « Étude partielle ». Le statut de l'étude et le
    # texte du rapport disent ainsi la même chose.
    failures += [s for s in collected.values() if s == StudySourceStatus.EMPTY]
    final = StudyStatus.PARTIAL if failures else StudyStatus.COMPLETED
    await service.set_study_status(db, study, final, finished_at=datetime.now(UTC))
    logger.info("Study %s finished with status %s", study_id, final)


async def _collect(
    study_id: uuid.UUID, devise: str, base_args: list[str], workdir: Path
) -> tuple[dict[str, str], dict[str, float]]:
    """Run the six collectors, at most ``COLLECT_PARALLEL`` at a time (6 by default).

    Each task owns its database session: an ``AsyncSession`` is not safe to share between
    concurrent tasks, and the previous single-session-plus-lock arrangement serialized six
    round-trips to a remote pooler for no reason. What made that lock necessary -- the
    read-modify-write on ``progress`` -- is gone, merged in SQL instead (``_update_progress``).

    Returns the status of each collector and its duration, the latter feeding the
    end-of-study recap.
    """
    gate = asyncio.Semaphore(studies_settings.COLLECT_PARALLEL)
    statuses: dict[str, str] = {}
    durations: dict[str, float] = {}
    volumes: dict[str, int] = {}
    raisons: dict[str, str] = {}

    async def run_one(spec: CollectorSpec) -> None:
        args = list(base_args)
        if spec.source == "aliexpress":
            args += ["--devise", devise]
        if spec.source == "amazon":
            args += ["--avis", str(studies_settings.AMAZON_AVIS)]
        if spec.source == "meta_ads":
            args += ["--annonces", str(studies_settings.META_ANNONCES)]
        if not spec.stdout_only:
            args += ["--sortie", spec.output_file, "--stdout"]

        async with gate:
            # Two short sessions rather than one held across the run: a collector takes up
            # to twenty minutes, and keeping six pooled connections idle for that long --
            # against a remote pooler -- buys nothing.
            async with SessionFactory() as db:
                await _update_progress(db, study_id, spec.source, {"status": "running"})
            logger.info("[%s] study %s: started", spec.source, study_id)

            run = await _run_module(
                spec.script,
                args,
                workdir=workdir,
                timeout_seconds=studies_settings.TIMEOUT_COLLECTOR_SECONDS,
            )

            if run.succeeded and spec.stdout_only and run.stdout_path is not None:
                # These three only ever talk on stdout: the file the analysis agents
                # will read is that capture, promoted into place. A rename rather than
                # a re-serialization of `run.payload` -- byte for byte what the module
                # emitted, and one full copy of the JSON that never has to be built.
                # Only on success, so a collector that died mid-write never leaves a
                # truncated file behind for the agents to read as if it were complete.
                run.stdout_path.replace(workdir / spec.output_file)

            status = _collector_status(spec, run)
            statuses[spec.source] = status
            volumes[spec.source] = _nb_items(spec.source, run.payload)
            durations[spec.source] = run.duration_seconds
            if status == StudySourceStatus.FAILED and run.timed_out:
                raisons[spec.source] = "delai_depasse"
            async with SessionFactory() as db:
                await _persist_source(db, study_id, spec, run, status)

    # `gather` without `return_exceptions`: a collector never raises -- `_run_module` turns
    # every failure into a ModuleRun. An exception here would be a defect of the runner
    # itself, and must surface as one rather than be counted as a failed collector.
    await asyncio.gather(*(run_one(spec) for spec in COLLECTORS))
    _ecrire_etat_sources(workdir, statuses, volumes, raisons)
    return statuses, durations


RAISONS_SOURCE_VIDE: dict[str, str] = {
    StudySourceStatus.SKIPPED_REGION: "region_non_couverte",
    StudySourceStatus.FAILED: "echec_collecteur",
    StudySourceStatus.EMPTY: "aucun_resultat",
}


def _ecrire_etat_sources(
    workdir: Path,
    statuses: dict[str, str],
    volumes: dict[str, int],
    raisons: dict[str, str],
) -> None:
    """Write what each collector actually brought back, for F7 to read.

    F7 renders a « Sources analysées » line naming all six sources, an empty one with
    its reason. That reason exists only here: by the time F3 to F6 have run, a source
    that returned nothing is indistinguishable from a source that was never asked.

    Args:
        workdir: The study's working directory.
        statuses: Status per collector.
        volumes: Items harvested per collector.
        raisons: Free-form reason per collector, where one was captured.
    """
    etat = {
        source: {
            "donnees_disponibles": statuses.get(source) == StudySourceStatus.SUCCEEDED,
            "nb_items": volumes.get(source, 0),
            "raison": raisons.get(source)
            or RAISONS_SOURCE_VIDE.get(statuses.get(source, "")),
        }
        for source in (spec.source for spec in COLLECTORS)
    }
    (workdir / SOURCES_STATE_FILE).write_text(
        json.dumps(etat, ensure_ascii=False, indent=1), encoding="utf-8"
    )


# La liste de chaque payload qui porte la récolte, par opposition aux limites, aux
# hypothèses et aux statuts internes -- tous présents et non vides même quand la
# collecte n'a rien donné. Elle ne sert qu'au DÉCOMPTE, et seulement en secours du
# drapeau ci-dessous : `google_trends` n'y figure pas, parce qu'il ne rapporte pas
# une liste d'éléments mais un bloc d'indicateurs.
CLE_RECOLTE: dict[str, str] = {
    "aliexpress": "produits",
    "amazon": "produits",
    "reddit": "commentaires",
    "recherche_web": "pages",
    "meta_ads": "annonces",
}

DRAPEAU_RECOLTE = "donnees_disponibles"
"""Le champ par lequel chaque collecteur dit lui-même s'il a rapporté quelque chose.

Il fait foi. La première version de ce code déduisait la récolte de la longueur
d'une liste choisie par source, ce qui était une devinette sur un contrat qui
existait déjà : `google_trends` s'y trouvait classé vide sur toutes ses collectes,
parce qu'il ne publie pas de liste d'éléments mais des indicateurs, et son
`sujets_associes` est vide même sur un run parfaitement bon.

Le décompte de listes reste, en secours, pour un module qui ne publierait pas le
drapeau."""


def _a_recolte(source: str, payload: dict[str, Any] | None) -> bool:
    """Dit si un collecteur a rapporté quelque chose d'exploitable.

    Args:
        source: Identifiant du collecteur.
        payload: Sortie JSON du module, ou ``None``.

    Returns:
        ``True`` si la collecte a produit des données.
    """
    if not payload:
        return False
    drapeau = payload.get(DRAPEAU_RECOLTE)
    if isinstance(drapeau, bool):
        return drapeau
    return _nb_items(source, payload) > 0


def _nb_items(source: str, payload: dict[str, Any] | None) -> int:
    """Compte les éléments rapportés par un collecteur, quand il en liste.

    Purement informatif : le volume voyage jusqu'à F7 par ``sources_etat.json``, où
    il nourrit la ligne « Sources analysées ». C'est ``_a_recolte`` qui décide du
    statut, jamais ce décompte.

    Args:
        source: Identifiant du collecteur.
        payload: Sortie JSON du module, ou ``None``.

    Returns:
        Le nombre d'éléments récoltés, zéro compris — y compris pour un collecteur
        qui a réussi sans rien lister, comme ``google_trends``.
    """
    if not payload:
        return 0
    valeur = payload.get(CLE_RECOLTE.get(source, ""))
    return len(valeur) if isinstance(valeur, list) else 0


def _collector_status(spec: CollectorSpec, run: ModuleRun) -> str:
    """Classify one collector run: did it work, and did it bring anything back?

    ``succeeded`` now requires both. A collector that exits 0 with an empty harvest is
    ``empty``, which is neither a success nor a failure: nothing broke, and there is
    nothing to analyse either. Study 8609db9e is the reason -- AliExpress came back with
    zero offers, was filed as ``succeeded``, and the report never mentioned that half of
    its price comparison had no data behind it.

    Args:
        spec: The collector's specification.
        run: What the module did.

    Returns:
        The status to persist.
    """
    if run.skipped_region:
        return StudySourceStatus.SKIPPED_REGION
    if not run.succeeded:
        return StudySourceStatus.FAILED
    if not _a_recolte(spec.source, run.payload):
        return StudySourceStatus.EMPTY
    return StudySourceStatus.SUCCEEDED


async def _analyse(
    db: AsyncSession, study_id: uuid.UUID, workdir: Path
) -> tuple[dict[str, str], dict[str, float]]:
    """F3 and F4 in parallel, then F5, then F6 -- which is always chained.

    An analysis with a missing input degrades and says so in its own ``limites``; the
    chain never stops for it. F6 is run whatever the verdict: a non-positive one produces
    a short "not triggered" output, in exit code 0, that F7 renders as a standard notice.

    F5 starts once **both** F3 and F4 are done, whatever their status: a failed upstream is
    a degraded input, and degradation belongs to the modules, never to this orchestrator.
    """
    statuses: dict[str, str] = {}
    durations: dict[str, float] = {}
    by_agent = {spec.agent: spec for spec in ANALYSES}

    # F3 and F4 read disjoint inputs and never read each other: running them together
    # turns 1 076 s of measured wall-clock into 666 s.
    parallel = (StudyAgent.F3_INSIGHTS, StudyAgent.F4_CONCURRENCE)

    async def run_and_persist(agent: StudyAgent) -> None:
        """Its own short sessions, for the same reasons as a collector's."""
        async with SessionFactory() as own_db:
            await _update_progress(own_db, study_id, agent, {"status": "running"})
        logger.info("[%s] study %s: started", agent, study_id)

        run = await _run_analysis(by_agent[agent], workdir)

        durations[agent] = run.duration_seconds
        async with SessionFactory() as own_db:
            statuses[agent] = await _persist_analysis(own_db, study_id, by_agent[agent], run)

    await asyncio.gather(*(run_and_persist(agent) for agent in parallel))

    # F5 then F6 are sequential by nature -- F6 reads what F5 wrote -- so the study's own
    # session serves them, and no second one is opened.
    for agent in (StudyAgent.F5_VERDICT, StudyAgent.F6_PLC):
        logger.info("[%s] study %s: started", agent, study_id)
        await _update_progress(db, study_id, agent, {"status": "running"})
        run = await _run_analysis(by_agent[agent], workdir)
        durations[agent] = run.duration_seconds
        statuses[agent] = await _persist_analysis(db, study_id, by_agent[agent], run)

    return statuses, durations


async def _run_analysis(spec: AnalysisSpec, workdir: Path) -> ModuleRun:
    args = _input_args(spec, workdir)
    if args is None:
        return ModuleRun(
            exit_code=EXIT_UNUSABLE_INPUT,
            duration_seconds=0.0,
            error="A required upstream output is missing: the module was not launched.",
        )
    args += ["--sortie", spec.output_file, "--stdout"]
    return await _run_module(
        spec.script,
        args,
        workdir=workdir,
        timeout_seconds=studies_settings.TIMEOUT_ANALYSIS_SECONDS,
    )


def _input_args(spec: AnalysisSpec, workdir: Path) -> list[str] | None:
    """The ``--flag path`` pairs whose file actually exists, or None if a required one
    is missing. Same rule as the reference script: an absent input is simply not passed."""
    args: list[str] = []
    for flag, filename in spec.inputs:
        path = workdir / filename
        if path.exists():
            args += [flag, filename]
        elif flag in spec.required:
            return None
    return args


async def _report(
    db: AsyncSession, study_id: uuid.UUID, workdir: Path
) -> tuple[bool, float, str | None]:
    """F7: the report and the executive summary.

    Two ways to fail, and they are not the same incident.

    Exit 4 -- ``REDACTION_IMPOSSIBLE`` -- means the analyses are intact and only the
    write-up did not meet the v2 template. That is worth replaying on its own, without
    paying six collectors again, which is what ``POST /studies/{id}/report/regenerate``
    does. Any other failure is an unknown, and the study fails outright.

    Neither writes a report. Study 8609db9e is why: F7 returned 0 with an empty
    narrative, the study was filed ``completed``, and a decision-maker opened a document
    that contained no analysis. A missing report is visible; an empty one is not.

    Args:
        db: Database session.
        study_id: Study identifier.
        workdir: The study's working directory.

    Returns:
        The triple ``(succès, durée, code_erreur_ou_None)``.
    """
    args = _input_args(REPORT_SPEC, workdir)
    if args is None:
        await _update_progress(
            db, study_id, "f7_rapport", {"status": "failed", "error": "missing verdict"}
        )
        return False, 0.0, RunErrorCode.REPORT_FAILED

    args += [
        "--rapport",
        REPORT_MARKDOWN_FILE,
        "--resume",
        REPORT_SUMMARY_FILE,
        "--sortie",
        REPORT_SPEC.output_file,
        "--stdout",
    ]
    # L'état des collecteurs, s'il a été écrit : il porte la raison d'une collecte
    # vide, que ni F3 ni F4 ne conservent.
    if (workdir / SOURCES_STATE_FILE).exists():
        args += ["--sources-etat", SOURCES_STATE_FILE]
    await _update_progress(db, study_id, "f7_rapport", {"status": "running"})
    logger.info("[f7_rapport] study %s: started", study_id)
    run = await _run_module(
        REPORT_SPEC.script,
        args,
        workdir=workdir,
        timeout_seconds=studies_settings.TIMEOUT_ANALYSIS_SECONDS,
    )

    rapport = workdir / REPORT_MARKDOWN_FILE
    if not run.succeeded or not rapport.exists():
        code = (
            RunErrorCode.F7_REDACTION_FAILED
            if run.exit_code == EXIT_REDACTION_IMPOSSIBLE
            else RunErrorCode.REPORT_FAILED
        )
        await _update_progress(
            db,
            study_id,
            "f7_rapport",
            {
                "status": "failed",
                "exit_code": run.exit_code,
                "error": run.error,
                "code": code,
            },
        )
        return False, run.duration_seconds, code

    resume = workdir / REPORT_SUMMARY_FILE
    db.add(
        StudyReport(
            study_id=study_id,
            rapport_markdown=rapport.read_text(encoding="utf-8"),
            resume_markdown=resume.read_text(encoding="utf-8") if resume.exists() else None,
            payload=run.payload,
        )
    )
    await db.commit()
    await _update_progress(
        db,
        study_id,
        "f7_rapport",
        {"status": "succeeded", "duration_seconds": round(run.duration_seconds, 1)},
    )
    return True, run.duration_seconds, None


# ---------------------------------------------------------------------------
# Replaying the report alone
# ---------------------------------------------------------------------------
ENTREES_REJEU_REQUISES: tuple[StudyAgent, ...] = (
    StudyAgent.F3_INSIGHTS,
    StudyAgent.F4_CONCURRENCE,
    StudyAgent.F5_VERDICT,
)
"""Analyses that must be on file before the report can be replayed.

F6 is deliberately absent: it only runs when the verdict is positive, and a study
that concluded No-go has no life-cycle payload by design, not by accident."""


async def rebuild_report(study_id: uuid.UUID) -> None:
    """Re-run F7 alone, from the analysis payloads already in the database.

    Nothing is collected and nothing is analysed: the six collectors and the four
    analysis agents are the expensive half of a study, they succeeded, and their output
    is on file. Only the write-up is replayed.

    The working directory is REBUILT from the database rather than reused. A study's
    directory is kept by default, but it can have been purged, and rebuilding is both
    cheap and the only way to be sure F7 reads exactly what was persisted -- not a file
    left behind by a partial run.

    Args:
        study_id: Study whose report is to be rebuilt.
    """
    async with _study_semaphore(), SessionFactory() as db:
        try:
            await _rebuild_report(db, study_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Study %s: report replay stopped on an error", study_id)
            await _fail_study(
                db,
                study_id,
                RunErrorCode.UNEXPECTED_ERROR,
                "The report replay stopped on an unexpected server error.",
            )


async def _rebuild_report(db: AsyncSession, study_id: uuid.UUID) -> None:
    """Do the replay: restore the inputs, run F7, store the result.

    Args:
        db: Database session.
        study_id: Study whose report is to be rebuilt.
    """
    study = await db.get(Study, study_id)
    if study is None:
        logger.error("Study %s vanished before its report could be replayed", study_id)
        return

    workdir = (studies_settings.WORKDIR / str(study_id)).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    await _restaurer_entrees(db, study_id, workdir)

    await service.set_study_status(db, study, StudyStatus.REPORTING)
    started = time.monotonic()
    report_ok, _duration, code = await _report(db, study_id, workdir)
    logger.info(
        "Study %s: report replay %s in %.1f s",
        study_id,
        "succeeded" if report_ok else f"failed ({code})",
        time.monotonic() - started,
    )

    if not report_ok:
        await service.set_study_status(
            db,
            study,
            StudyStatus.PARTIAL,
            error={
                "code": code or RunErrorCode.REPORT_FAILED,
                "message": (
                    "The report still could not be produced: the analyses remain on "
                    "file and the replay can be attempted again."
                ),
            },
            finished_at=datetime.now(UTC),
        )
        return

    # Le statut retrouve sa règle d'origine : partiel si une source a échoué ou n'a rien
    # rapporté, complet sinon. Il est recalculé sur la base, et non hérité de l'échec
    # précédent, qui ne disait plus rien de l'étude une fois le rapport écrit.
    sources = await db.scalars(
        select(StudySourceData.status).where(StudySourceData.study_id == study_id)
    )
    degradees = [
        statut
        for statut in sources
        if statut in (StudySourceStatus.FAILED, StudySourceStatus.EMPTY)
    ]
    final = StudyStatus.PARTIAL if degradees else StudyStatus.COMPLETED
    await service.set_study_status(db, study, final, finished_at=datetime.now(UTC))
    logger.info("Study %s: report replayed, status %s", study_id, final)


async def _restaurer_entrees(
    db: AsyncSession, study_id: uuid.UUID, workdir: Path
) -> None:
    """Write the persisted analysis payloads and source state back to the workdir.

    Args:
        db: Database session.
        study_id: Study identifier.
        workdir: Directory F7 will read.
    """
    fichiers = {spec.agent: spec.output_file for spec in ANALYSES}
    analyses = await db.scalars(
        select(StudyAnalysis).where(StudyAnalysis.study_id == study_id)
    )
    for analyse in analyses:
        fichier = fichiers.get(analyse.agent)
        if fichier and analyse.payload:
            (workdir / fichier).write_text(
                json.dumps(analyse.payload, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )

    lignes = await db.scalars(
        select(StudySourceData).where(StudySourceData.study_id == study_id)
    )
    etat = {
        ligne.source: {
            "donnees_disponibles": ligne.status == StudySourceStatus.SUCCEEDED,
            "nb_items": _nb_items(ligne.source, ligne.payload),
            "raison": RAISONS_SOURCE_VIDE.get(ligne.status),
        }
        for ligne in lignes
    }
    if etat:
        (workdir / SOURCES_STATE_FILE).write_text(
            json.dumps(etat, ensure_ascii=False, indent=1), encoding="utf-8"
        )


async def analyses_manquantes(db: AsyncSession, study_id: uuid.UUID) -> list[str]:
    """Report which of the analyses required for a replay are not on file.

    Args:
        db: Database session.
        study_id: Study identifier.

    Returns:
        The missing agents, empty when the report can be replayed.
    """
    presentes = {
        agent
        for agent, payload in (
            await db.execute(
                select(StudyAnalysis.agent, StudyAnalysis.payload).where(
                    StudyAnalysis.study_id == study_id
                )
            )
        ).all()
        if payload
    }
    return [agent for agent in ENTREES_REJEU_REQUISES if agent not in presentes]


async def launch_report_rebuild(study_id: uuid.UUID) -> None:
    """Schedule a report replay in the background.

    Args:
        study_id: Study whose report is to be rebuilt.
    """
    task = asyncio.create_task(rebuild_report(study_id), name=f"report-{study_id}")
    _running.add(task)
    task.add_done_callback(_running.discard)


# ---------------------------------------------------------------------------
# Persistence -- written as it happens, never at the end
# ---------------------------------------------------------------------------
async def _persist_source(
    db: AsyncSession, study_id: uuid.UUID, spec: CollectorSpec, run: ModuleRun, status: str
) -> None:
    """Write one collector's row, from that collector's own session.

    An upsert on ``UNIQUE (study_id, source)`` rather than a SELECT-then-INSERT: with six
    tasks holding six sessions, the read and the write of a read-modify-write are no longer
    in the same transaction, and the pair would be a race. Postgres arbitrates instead.

    ``updated_at`` is set explicitly because the ORM's ``onupdate`` does not fire on a Core
    ``ON CONFLICT DO UPDATE``.
    """
    values = {
        "study_id": study_id,
        "source": spec.source,
        "status": status,
        "payload": run.payload if run.succeeded else None,
        "error": run.error,
        "exit_code": run.exit_code,
        "duration_seconds": round(run.duration_seconds, 3),
    }
    statement = insert(StudySourceData).values(**values)
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[StudySourceData.study_id, StudySourceData.source],
            set_={
                "status": statement.excluded.status,
                "payload": statement.excluded.payload,
                "error": statement.excluded.error,
                "exit_code": statement.excluded.exit_code,
                "duration_seconds": statement.excluded.duration_seconds,
                "updated_at": func.now(),
            },
        )
    )
    await db.commit()
    await _update_progress(
        db,
        study_id,
        spec.source,
        {
            "status": status,
            "exit_code": run.exit_code,
            "duration_seconds": round(run.duration_seconds, 1),
        },
    )
    logger.info(
        "[%s] study %s: %s (exit=%s, %.1fs)",
        spec.source,
        study_id,
        status,
        run.exit_code,
        run.duration_seconds,
    )


async def _persist_analysis(
    db: AsyncSession, study_id: uuid.UUID, spec: AnalysisSpec, run: ModuleRun
) -> str:
    """Same shape, same reasoning, for an analysis agent (F3 and F4 also run together)."""
    status = StudyAnalysisStatus.SUCCEEDED if run.succeeded else StudyAnalysisStatus.FAILED
    values = {
        "study_id": study_id,
        "agent": spec.agent,
        "status": status,
        "payload": run.payload if run.succeeded else None,
        "error": run.error,
        "exit_code": run.exit_code,
        "duration_seconds": round(run.duration_seconds, 3),
    }
    statement = insert(StudyAnalysis).values(**values)
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[StudyAnalysis.study_id, StudyAnalysis.agent],
            set_={
                "status": statement.excluded.status,
                "payload": statement.excluded.payload,
                "error": statement.excluded.error,
                "exit_code": statement.excluded.exit_code,
                "duration_seconds": statement.excluded.duration_seconds,
                "updated_at": func.now(),
            },
        )
    )
    await db.commit()
    await _update_progress(
        db,
        study_id,
        spec.agent,
        {
            "status": status,
            "exit_code": run.exit_code,
            "duration_seconds": round(run.duration_seconds, 1),
        },
    )
    if run.exit_code == EXIT_UNUSABLE_INPUT:
        logger.warning(
            "[%s] study %s: exited on unusable input (probable wiring defect): %s",
            spec.agent,
            study_id,
            run.error,
        )
    logger.info("[%s] study %s: %s (exit=%s)", spec.agent, study_id, status, run.exit_code)
    return status


async def _update_progress(
    db: AsyncSession, study_id: uuid.UUID, key: str, value: dict[str, Any]
) -> None:
    """Merge one entry into ``study.progress``, **in SQL**.

    ``progress = progress || :patch`` is what makes concurrent writers safe. The previous
    read-modify-write in Python was only safe because a single lock serialized it; with one
    session per task that lock is gone, and two collectors finishing together would each
    write back a dict missing the other's entry -- a textbook lost update.

    Postgres merges shallowly, so one patch touches exactly one top-level key. Each key has
    a single writer (one collector, one agent, or the orchestrator for ``phase_durations``),
    which is what makes a shallow merge sufficient.

    Deliberately bypasses the ORM: no ``Study`` instance is loaded, so nothing here can
    clobber ``progress`` from a stale identity map.
    """
    patch = {key: {**value, "at": datetime.now(UTC).isoformat()}}
    await db.execute(
        update(Study)
        .where(Study.id == study_id)
        .values(progress=Study.progress.op("||", return_type=JSONB)(_as_jsonb(patch)))
    )
    await db.commit()


def _as_jsonb(value: dict[str, Any]) -> Any:
    """A bind parameter Postgres reads as ``jsonb``, not as text."""
    return bindparam("patch", value=value, type_=JSONB, unique=True)


# ---------------------------------------------------------------------------
# Instrumentation -- strictly additive, and only ever written by the orchestrator
# ---------------------------------------------------------------------------
async def _record_phase_durations(
    db: AsyncSession, study_id: uuid.UUID, durations: dict[str, float]
) -> None:
    """Write ``progress.phase_durations`` at the close of each phase.

    Written whole each time rather than key by key: the orchestrator is the single writer
    of this key and always holds the full picture, so the shallow ``||`` merge replacing it
    outright is exactly what is wanted. It lands in ``progress``, which is already jsonb --
    no migration, and no existing field touched.
    """
    patch = {name: round(seconds, 1) for name, seconds in durations.items()}
    await db.execute(
        update(Study)
        .where(Study.id == study_id)
        .values(
            progress=Study.progress.op("||", return_type=JSONB)(
                _as_jsonb({"phase_durations": patch})
            )
        )
    )
    await db.commit()


def _log_recap(
    study_id: uuid.UUID,
    phases: dict[str, float],
    modules: dict[str, float],
    report_duration: float,
) -> None:
    """The end-of-study recap: where the time went, and what concurrency actually bought.

    "Gain vs sequential" is the sum of the modules' own durations minus the measured total.
    It is what a run would have cost had every module waited for the previous one, so it
    stays comparable across runs of very different absolute lengths.
    """
    per_module = {**modules, "f7_rapport": report_duration}
    sequential = sum(per_module.values())
    total = phases.get("total", 0.0)
    logger.info(
        "Study %s recap -- modules: %s | phases: %s | total: %.1fs | "
        "sequential would have been %.1fs (gain %.1fs)",
        study_id,
        ", ".join(f"{name}={seconds:.1f}s" for name, seconds in sorted(per_module.items())),
        ", ".join(f"{name}={seconds:.1f}s" for name, seconds in phases.items()),
        total,
        sequential,
        sequential - total,
    )


async def _fail_study(
    db: AsyncSession, study_id: uuid.UUID, code: RunErrorCode, message: str
) -> None:
    """Mark a study failed, from a session that may be in any state."""
    try:
        await db.rollback()
        study = await db.get(Study, study_id)
        if study is None:
            return
        await service.set_study_status(
            db,
            study,
            StudyStatus.FAILED,
            error={"code": str(code), "message": message},
            finished_at=datetime.now(UTC),
        )
    except Exception:
        logger.exception("Could not record the failure of study %s", study_id)
