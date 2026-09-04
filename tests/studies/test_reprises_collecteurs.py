"""Reprise d'un collecteur qui n'a rien rapporté.

Sans base ni réseau : `_run_module` est remplacé, et ce qui est vérifié ici est la
politique de reprise, pas le lancement d'un sous-processus.

L'incident qui la motive — étude 7a93b99d, 04/09/2026. Trois collecteurs sur six
sont revenus vides en quinze secondes chacun, avec le même message : « Génération du
plan de requêtes impossible ». Ils n'avaient pas échoué à collecter, ils n'avaient
jamais collecté : l'appel au modèle qui construit leur plan de recherche avait
échoué, et chacun de ces agents l'avale en rendant un plan vide, sans jamais
retenter. Trois échecs au même instant sur un produit parfaitement valide — une
saturation passagère de l'API, contre laquelle une seconde tentative suffit.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from src.studies.config import studies_settings
from src.studies.constants import COLLECTORS, EXIT_REGION_NOT_COVERED, EXIT_UNUSABLE_INPUT
from src.studies.runner import ModuleRun, _collecter_avec_reprises, _merite_une_reprise

AMAZON = next(spec for spec in COLLECTORS if spec.source == "amazon")
ETUDE = uuid.UUID("7a93b99d-b71c-4318-b594-afcab8519c54")


def _vide() -> ModuleRun:
    """Le cas de l'incident : code 0, aucune récolte."""
    return ModuleRun(
        exit_code=0,
        duration_seconds=15.0,
        payload={"donnees_disponibles": False, "produits": [], "limites": ["…"]},
    )


def _plein() -> ModuleRun:
    return ModuleRun(
        exit_code=0,
        duration_seconds=60.0,
        payload={"donnees_disponibles": True, "produits": [{"asin": "B0"}]},
    )


@pytest.fixture(autouse=True)
def _sans_attente(monkeypatch: pytest.MonkeyPatch) -> None:
    """Les tests ne patientent pas les vingt secondes de production."""
    monkeypatch.setattr(studies_settings, "RETRY_BACKOFF_SECONDS", 0.0)


def _brancher(monkeypatch: pytest.MonkeyPatch, resultats: list[ModuleRun]) -> list[int]:
    """Fait rendre à `_run_module` la suite de résultats donnée.

    Args:
        monkeypatch: Fixture pytest.
        resultats: Résultats successifs, le dernier étant répété si besoin.

    Returns:
        Une liste qui s'allonge d'un élément à chaque lancement.
    """
    lancements: list[int] = []

    async def faux_run_module(*_args: Any, **_kwargs: Any) -> ModuleRun:
        lancements.append(1)
        return resultats[min(len(lancements) - 1, len(resultats) - 1)]

    monkeypatch.setattr("src.studies.runner._run_module", faux_run_module)
    return lancements


# ---------------------------------------------------------------------------
# 1. Ce qui est rejoué
# ---------------------------------------------------------------------------
async def test_une_collecte_vide_est_rejouee_et_finit_par_aboutir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le scénario de l'incident, avec la reprise que le run n'avait pas."""
    lancements = _brancher(monkeypatch, [_vide(), _plein()])
    run, tentatives = await _collecter_avec_reprises(AMAZON, [], Path("."), ETUDE)

    assert len(lancements) == 2, "la seconde tentative doit avoir lieu"
    assert tentatives == 2
    assert run.payload["produits"]


async def test_les_reprises_s_arretent_au_plafond(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un essai et deux reprises : trois lancements, pas davantage."""
    lancements = _brancher(monkeypatch, [_vide()])
    run, tentatives = await _collecter_avec_reprises(AMAZON, [], Path("."), ETUDE)

    assert len(lancements) == studies_settings.COLLECTOR_ATTEMPTS == 3
    assert tentatives == 3
    assert not run.payload["produits"], "le dernier résultat est conservé"


async def test_un_echec_franc_est_rejoue(monkeypatch: pytest.MonkeyPatch) -> None:
    echec = ModuleRun(exit_code=1, duration_seconds=2.0, error="boom")
    lancements = _brancher(monkeypatch, [echec, _plein()])
    _run, tentatives = await _collecter_avec_reprises(AMAZON, [], Path("."), ETUDE)

    assert len(lancements) == 2
    assert tentatives == 2


async def test_un_succes_du_premier_coup_ne_relance_rien(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une reprise inutile coûte un run d'actor et des jetons : elle n'a pas lieu."""
    lancements = _brancher(monkeypatch, [_plein()])
    _run, tentatives = await _collecter_avec_reprises(AMAZON, [], Path("."), ETUDE)

    assert len(lancements) == 1
    assert tentatives == 1


# ---------------------------------------------------------------------------
# 2. Ce qui n'est jamais rejoué
# ---------------------------------------------------------------------------
async def test_une_region_non_couverte_n_est_pas_rejouee(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Amazon n'a pas de site marocain, et le relancer ne lui en donnera pas un."""
    hors_zone = ModuleRun(exit_code=EXIT_REGION_NOT_COVERED, duration_seconds=1.0)
    lancements = _brancher(monkeypatch, [hors_zone])
    _run, tentatives = await _collecter_avec_reprises(AMAZON, [], Path("."), ETUDE)

    assert len(lancements) == 1
    assert tentatives == 1


async def test_une_entree_inexploitable_n_est_pas_rejouee(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La leçon du run 8609db9e : rejouer un défaut de câblage, c'est le payer deux fois.

    Le module recevra exactement la même entrée à la reprise, et rendra exactement
    la même erreur.
    """
    invalide = ModuleRun(
        exit_code=EXIT_UNUSABLE_INPUT, duration_seconds=1.0, error="entrée illisible"
    )
    lancements = _brancher(monkeypatch, [invalide])
    _run, tentatives = await _collecter_avec_reprises(AMAZON, [], Path("."), ETUDE)

    assert len(lancements) == 1
    assert tentatives == 1


@pytest.mark.parametrize(
    ("run", "attendu"),
    [
        (ModuleRun(exit_code=EXIT_REGION_NOT_COVERED, duration_seconds=1.0), False),
        (ModuleRun(exit_code=EXIT_UNUSABLE_INPUT, duration_seconds=1.0), False),
        (ModuleRun(exit_code=1, duration_seconds=1.0, error="boom"), True),
    ],
)
def test_la_politique_de_reprise_est_lisible_seule(run: ModuleRun, attendu: bool) -> None:
    assert _merite_une_reprise(AMAZON, run) is attendu


# ---------------------------------------------------------------------------
# 3. Les analyses suivent la même règle
# ---------------------------------------------------------------------------
async def test_une_analyse_en_echec_est_rejouee(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Les analyses appellent le même modèle que l'étape de planification des
    collecteurs : elles rencontrent les mêmes pannes passagères."""
    from src.studies.constants import ANALYSES
    from src.studies.runner import _run_analysis

    spec = ANALYSES[0]
    monkeypatch.setattr("src.studies.runner._input_args", lambda *_: ["--x", "y"])
    lancements = _brancher(
        monkeypatch,
        [
            ModuleRun(exit_code=1, duration_seconds=2.0, error="saturation"),
            ModuleRun(exit_code=0, duration_seconds=90.0, payload={"insights": [1]}),
        ],
    )
    run = await _run_analysis(spec, tmp_path)

    assert len(lancements) == 2
    assert run.succeeded


async def test_une_analyse_sans_entree_amont_n_est_jamais_lancee(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Le fichier manquera tout autant à la seconde tentative."""
    from src.studies.constants import ANALYSES
    from src.studies.runner import _run_analysis

    monkeypatch.setattr("src.studies.runner._input_args", lambda *_: None)
    lancements = _brancher(monkeypatch, [ModuleRun(exit_code=0, duration_seconds=1.0)])
    run = await _run_analysis(ANALYSES[0], tmp_path)

    assert lancements == [], "aucun sous-processus ne doit être lancé"
    assert run.exit_code == EXIT_UNUSABLE_INPUT
