"""Reprise des appels au modèle qui conditionnent une collecte entière.

La reprise ajoutée à l'orchestrateur relance un sous-processus complet — vingt
minutes de collecte possibles pour refaire un appel d'une seconde. Celle-ci est au
plus près de ce qui échoue, dans les trois agents dont un appel perdu abandonne
toute la collecte : `agent_recherche_web`, `agent_amazon`, `agent_meta_ads`.

Chacun est un arbre autonome, avec son propre `config.py` et aucun import croisé —
d'où un helper par agent plutôt qu'un module partagé, et ce test qui les parcourt
tous les trois pour qu'aucun ne dérive des autres.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

RACINE = Path(__file__).resolve().parents[2] / "src" / "agents" / "market_study"
AGENTS = ("agent_recherche_web", "agent_amazon", "agent_meta_ads")


def _config(agent: str) -> Any:
    """Charge le `config.py` d'un agent, qui s'importe par nom nu.

    Args:
        agent: Nom du répertoire de l'agent.

    Returns:
        Le module de configuration.
    """
    chemin = str(RACINE / agent)
    if chemin not in sys.path:
        sys.path.insert(0, chemin)
    for reste in ("config",):
        sys.modules.pop(reste, None)
    return importlib.import_module("config")


class _SaturationError(Exception):
    """Une erreur passagère, reconnue par son nom comme le fait le code."""


_SaturationError.__name__ = "RateLimitError"


@pytest.fixture(autouse=True)
def _sans_attente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _secondes: None)


@pytest.mark.parametrize("agent", AGENTS)
def test_un_appel_perdu_puis_repris_finit_par_aboutir(agent: str) -> None:
    """Le scénario de l'étude 7a93b99d, avec la reprise qui lui a manqué."""
    config = _config(agent)
    essais: list[int] = []

    def appel() -> str:
        essais.append(1)
        if len(essais) == 1:
            raise _SaturationError("429")
        return "plan"

    assert config.invoquer_avec_reprises(appel, "plan") == "plan"
    assert len(essais) == 2


@pytest.mark.parametrize("agent", AGENTS)
def test_une_saturation_persistante_epuise_les_tentatives(agent: str) -> None:
    """Trois essais au total, puis `None` — la collecte se déclare vide, comme avant."""
    config = _config(agent)
    essais: list[int] = []

    def appel() -> str:
        essais.append(1)
        raise _SaturationError("429")

    assert config.invoquer_avec_reprises(appel, "plan") is None
    assert len(essais) == config.NB_TENTATIVES_MAX + 1


@pytest.mark.parametrize("agent", AGENTS)
def test_une_erreur_deterministe_n_est_jamais_rejouee(agent: str) -> None:
    """Un gabarit cassé redonnera la même erreur : la rejouer, c'est la payer deux fois.

    C'est ce qui est arrivé au run 8609db9e — huit invocations facturées pour huit
    fois le même `KeyError`.
    """
    config = _config(agent)
    essais: list[int] = []

    def appel() -> str:
        essais.append(1)
        raise KeyError("variable_absente")

    assert config.invoquer_avec_reprises(appel, "plan") is None
    assert essais == [1]


@pytest.mark.parametrize("agent", AGENTS)
def test_un_appel_qui_aboutit_n_est_pas_rejoue(agent: str) -> None:
    config = _config(agent)
    essais: list[int] = []

    def appel() -> str:
        essais.append(1)
        return "plan"

    assert config.invoquer_avec_reprises(appel, "plan") == "plan"
    assert essais == [1]


@pytest.mark.parametrize("agent", AGENTS)
def test_les_appels_bloquants_passent_tous_par_la_reprise(agent: str) -> None:
    """Aucun appel décidant d'une collecte ne doit garder son essai unique.

    Le contrôle qualité, lui, garde le sien : il est explicitement non bloquant et
    sa perte ne fait que dégrader un rapport de qualité, pas annuler une collecte.
    """
    fichier = "queries.py" if agent == "agent_recherche_web" else "strategy.py"
    source = (RACINE / agent / fichier).read_text(encoding="utf-8")

    assert "converti en absence de plan" not in source
    assert "converti en région non résolue" not in source
    assert "le contrôle qualité ne bloque pas" in source, (
        "le contrôle qualité reste volontairement sans reprise"
    )
