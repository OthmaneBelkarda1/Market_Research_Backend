"""Routage des plateformes et épinglage du pays, sans réseau.

Trois défauts couverts ici, tous constatés en production le 04/09/2026.
"""

from __future__ import annotations

import pytest
from src.agents.product_extraction import ACTOR_ADAPTERS, PlatformUnsupportedError, detect_route
from src.products.extraction import _apply_region


# ---------------------------------------------------------------------------
# 1. Une plateforme sans scrapeur est refusée avant toute dépense
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "https://www.temu.com/ma-en/smart-door-lock-g-601101619167649.html",
        "https://www.temu.com/us-en/x-g-1.html?_x_ads_channel=google",
        "https://temu.com/fr-fr/y-g-2.html",
    ],
)
def test_temu_est_refuse_des_le_routage(url: str) -> None:
    """Sans ce refus, l'URL coûtait deux runs Apify et des jetons LLM.

    Le chemin complet était : actor dédié (échec), actor de repli (échec), étapes
    du modèle par-dessus — une quarantaine de secondes et de l'argent réel pour
    atteindre un échec dont on connaissait déjà l'issue.
    """
    with pytest.raises(PlatformUnsupportedError, match="Temu"):
        detect_route(url)


def test_le_refus_dit_ce_qui_a_ete_verifie_et_quand() -> None:
    """Une table de plateformes bloquées se re-teste ; elle ne se croit pas sur parole."""
    from src.agents.product_extraction.routing import PLATEFORMES_SANS_SCRAPEUR

    raison = PLATEFORMES_SANS_SCRAPEUR["temu.com"]
    assert "2026-09-04" in raison
    assert "actors" in raison


@pytest.mark.parametrize(
    "url",
    [
        "https://www.aliexpress.com/item/1005012323403801.html",
        "https://www.amazon.fr/dp/B0TEST",
        "https://boutique-quelconque.example/produit/1",
    ],
)
def test_les_autres_domaines_routent_normalement(url: str) -> None:
    """Le court-circuit ne doit toucher que ce qu'il nomme."""
    assert detect_route(url).url


# ---------------------------------------------------------------------------
# 2. Le pays demandé atteint enfin l'actor Temu
# ---------------------------------------------------------------------------
def test_l_adaptateur_temu_transmet_le_pays() -> None:
    """La note de l'adaptateur affirmait pendant des mois que l'actor n'avait pas
    d'entrée pays. Son schéma publié en a une : `region`, dont le défaut est `us`.

    Le paramètre n'étant jamais envoyé, toute extraction repartait avec des prix
    américains, quelle que soit la région demandée, et sans que rien ne le dise.
    """
    entree = ACTOR_ADAPTERS["temu"].build_input("https://www.temu.com/x-g-1.html")
    assert "region" in entree

    from src.products.constants import ACTOR_COUNTRY_KEYS

    assert "region" in ACTOR_COUNTRY_KEYS, (
        "sans cette clé, `_regional_build_input` n'a rien à réécrire"
    )


@pytest.mark.parametrize("region", ["MA", "FR", "US"])
def test_le_clone_regional_reecrit_le_pays_de_temu(region: str) -> None:
    url = "https://www.temu.com/ma-en/x-g-1.html"
    # `detect_route` refuse Temu, mais l'épinglage du pays reste vérifiable sur
    # l'adaptateur lui-même : c'est lui qui portera le correctif le jour où un
    # scrapeur Temu refonctionnera.
    cle = f"temu@{region}"
    from src.products.extraction import _cloner_adaptateur

    assert _cloner_adaptateur("temu", region) == cle
    assert ACTOR_ADAPTERS[cle].build_input(url)["region"] == region.lower()


def test_amazon_garde_ses_propres_cles_de_pays() -> None:
    """Non-régression : ajouter `region` ne doit pas déranger les autres actors."""
    entree = ACTOR_ADAPTERS["amazon"].build_input("https://www.amazon.fr/dp/B0TEST")
    assert entree["proxyCountry"] and entree["countryCode"]
    assert "region" not in entree


# ---------------------------------------------------------------------------
# 3. Les clones régionaux existent pour TOUTE région, pas pour une liste
# ---------------------------------------------------------------------------
def test_un_clone_est_cree_pour_une_region_hors_de_toute_liste() -> None:
    """La régression introduite en ouvrant les régions.

    Les clones étaient pré-enregistrés en parcourant la liste blanche. Rendue vide
    — ce qui veut dire « aucune restriction » — la boucle n'avait plus rien à
    parcourir et enregistrait zéro clone : chaque extraction Apify repartait sur le
    pays par défaut de l'agent, en journalisant un « should not happen ».
    """
    options = _apply_region("JP", "https://www.aliexpress.com/item/1005012323403801.html")
    assert options["force_actor"] == "aliexpress@JP"
    assert "aliexpress@JP" in ACTOR_ADAPTERS


def test_une_url_hors_apify_ne_recoit_aucun_actor() -> None:
    """Une page rendue par le navigateur n'a pas d'actor à épingler."""
    options = _apply_region("FR", "https://boutique-quelconque.example/produit/1")
    assert "force_actor" not in options
    assert options["locale"] == "fr-FR"
