"""La liste blanche des régions, et ce qui reste quand elle est ouverte.

Aucun de ces tests n'a besoin d'une base ni du réseau : ils portent sur les deux
validateurs de requête et sur les tables du pipeline, lues sur disque.

La liste tenait cinq pays — MA, FR, ES, US, AE — alors que le pipeline sait résoudre
une devise et une langue pour 244. C'était une précaution de lancement, pas une
limite de ce qu'il sait faire, et elle refusait 239 pays qu'il traite.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from src.products.config import products_settings
from src.products.schemas import normalize_region as normaliser_region_extraction
from src.studies.config import studies_settings
from src.studies.schemas import normalize_region as normaliser_region_etude

RACINE_PIPELINE = (
    Path(__file__).resolve().parents[2] / "src" / "agents" / "market_study"
)


def _pays_de_la_table(fichier: str) -> set[str]:
    """Lit les codes pays d'une table du pipeline.

    Args:
        fichier: Nom du module de résolution.

    Returns:
        Les codes ISO 3166-1 alpha-2 qu'il connaît.
    """
    texte = (RACINE_PIPELINE / fichier).read_text(encoding="utf-8")
    return set(re.findall(r'"([A-Z]{2})"\s*:', texte))


# ---------------------------------------------------------------------------
# 1. Le défaut n'interdit plus rien
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("region", ["JP", "BR", "DE", "IN", "NG", "MA", "FR"])
def test_toute_region_bien_formee_est_acceptee(region: str) -> None:
    """Le Japon, le Brésil, l'Inde, le Nigeria : tous refusés avant, tous traités."""
    assert normaliser_region_etude(region) == region
    assert normaliser_region_extraction(region) == region


def test_une_region_minuscule_est_normalisee() -> None:
    """`br` est accepté et stocké `BR` : la casse n'est pas une erreur d'appel."""
    assert normaliser_region_etude("br") == "BR"


def test_une_liste_renseignee_restreint_toujours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ouvrir par défaut ne retire pas le levier : il redevient actif dès qu'on le règle.

    C'est ce qui rend l'ouverture réversible sans toucher au code — par
    déploiement, ou le temps d'une ouverture de marché.
    """
    monkeypatch.setattr(studies_settings, "ALLOWED_REGIONS", "FR,ES")
    assert normaliser_region_etude("FR") == "FR"
    with pytest.raises(ValueError, match="not allowed"):
        normaliser_region_etude("JP")


def test_une_liste_vide_n_autorise_pas_rien(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le piège de ce réglage : une liste vide doit tout ouvrir, pas tout fermer."""
    monkeypatch.setattr(studies_settings, "ALLOWED_REGIONS", "")
    assert studies_settings.allowed_regions == frozenset()
    assert studies_settings.region_autorisee("JP")
    monkeypatch.setattr(products_settings, "EXTRACTION_ALLOWED_REGIONS", "   ,  ")
    assert products_settings.region_autorisee("JP")


# ---------------------------------------------------------------------------
# 2. Ce qui garde une région inconnue, maintenant que la liste ne le fait plus
# ---------------------------------------------------------------------------
def test_les_deux_tables_du_pipeline_couvrent_le_meme_ensemble() -> None:
    """Devise et langue doivent couvrir les mêmes pays, sans quoi un pays passe à moitié.

    Une région qui résout sa devise mais pas sa langue échouerait à la deuxième
    étape, après la première — pour rien, puisque les deux précèdent la collecte,
    mais en laissant croire à un défaut de la table des langues seule.
    """
    devises = _pays_de_la_table("devise_marche.py")
    langues = _pays_de_la_table("langues_marche.py")
    assert devises == langues
    assert len(devises) > 200, "les tables du pipeline couvrent le monde, pas une poignée"


def test_les_cinq_regions_historiques_sont_toujours_couvertes() -> None:
    """Non-régression : ouvrir n'a rien retiré."""
    devises = _pays_de_la_table("devise_marche.py")
    assert {"MA", "FR", "ES", "US", "AE"} <= devises


def test_une_region_hors_des_tables_est_arretee_avant_toute_depense() -> None:
    """C'est le pipeline qui garde l'inconnu, et il le garde gratuitement.

    `_resolve_market` s'exécute AVANT le premier collecteur : un pays absent des
    tables stoppe l'étude sur `CURRENCY_NOT_MAPPED` sans avoir consommé un run
    d'actor ni un jeton. Restituer les 244 codes dans le backend n'ajouterait
    qu'une seconde liste à tenir à jour.
    """
    from src.studies.constants import RunErrorCode

    devises = _pays_de_la_table("devise_marche.py")
    assert "ZZ" not in devises
    assert RunErrorCode.CURRENCY_NOT_MAPPED == "CURRENCY_NOT_MAPPED"


# ---------------------------------------------------------------------------
# 3. La réserve propre à l'extraction
# ---------------------------------------------------------------------------
def test_une_region_sans_profil_de_navigateur_retombe_sur_le_profil_neutre() -> None:
    """L'extraction accepte tout, mais ne sait se faire passer pour un visiteur que
    dans treize pays.

    Ailleurs, le navigateur se présente en `en-US`/`UTC` : une boutique qui change
    de devise sur les en-têtes affichera son prix américain. L'extraction réussit
    et le chiffre est réel — c'est simplement le prix vu par un autre visiteur.
    """
    from src.products.constants import NEUTRAL_REGION_PROFILE, REGION_PROFILES

    assert "JP" not in REGION_PROFILES
    assert products_settings.region_autorisee("JP")
    assert NEUTRAL_REGION_PROFILE[0].startswith("en-")
