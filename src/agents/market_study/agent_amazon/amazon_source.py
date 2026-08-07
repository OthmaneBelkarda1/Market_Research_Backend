"""Accès aux données Amazon via deux actors Apify.

- `junglee/Amazon-crawler` : une recherche = un run. L'actor crawle l'URL de
  listing qu'on lui donne — ici toujours une page de résultats construite par
  `strategy` — et remonte les fiches produits.
- `junglee/amazon-reviews-scraper` : un produit = un run. Grouper plusieurs
  produits dans un même run serait plus économique en apparence, mais l'actor
  plafonne un run à une dizaine d'avis : toute l'allocation partirait sur le
  premier produit de la liste.

Choix structurant du module, valable pour les deux actors : **aucune adresse de
livraison n'est transmise** (ni `countryCode`, ni `zipCode`). Renseigner une
adresse ferait masquer par Amazon tout ce qu'il ne peut pas y expédier et
convertirait les prix. Le corpus livré est le catalogue complet de la
marketplace, dans sa propre devise. `proxyCountry` est laissé sur son défaut
(`AUTO_SELECT`) : l'actor apparie lui-même le proxy au domaine interrogé.

Les fonctions exposées ne propagent jamais d'exception : toute erreur devient un
`StatutCollecte(succes=False, ...)`.
"""

from __future__ import annotations

import json
import math
import time
from datetime import timedelta
from typing import Any

from apify_client import ApifyClient

from config import (
    ACTOR_AMAZON_AVIS,
    ACTOR_AMAZON_CRAWLER,
    ANCIENNETE_MAX_AVIS,
    APIFY_TOKEN,
    BACKOFF_TENTATIVES_SECS,
    CLE_ERREUR,
    FILTRE_NOTES_AVIS,
    INCLURE_DONNEES_PERSONNELLES,
    MARGE_ATTENTE_RUN_SECS,
    MAX_PRODUITS_PAR_RECHERCHE,
    MIN_PAGES_SERP,
    NB_AVIS_PAR_PRODUIT,
    NB_TENTATIVES_MAX,
    PRODUITS_PAR_PAGE_SERP,
    TIMEOUT_RUN_AVIS_SECS,
    TIMEOUT_RUN_SECS,
    TRI_AVIS,
    UTILISER_SOLVEUR_CAPTCHA_SUR,
    obtenir_logger,
)
from schemas import Marketplace, RecherchePlanifiee, StatutCollecte

_LOG = obtenir_logger(__name__)

_STATUT_SUCCES = "SUCCEEDED"

TYPE_RUN_PRODUITS: str = "produits"
TYPE_RUN_AVIS: str = "avis"

_MESSAGE_PAGE_VIDE = (
    "Run SUCCEEDED sans aucun produit exploitable. Amazon a servi une page de "
    "résultats vide ou en erreur : c'est le plus souvent une protection "
    "anti-bot, plus rarement une recherche réellement sans résultat."
)


def _en_dict(objet: Any) -> dict:
    """Normalise une réponse du client Apify en dictionnaire.

    Args:
        objet: Modèle Pydantic ou dictionnaire renvoyé par `apify-client`.

    Returns:
        Le dictionnaire correspondant, vide si l'objet est nul.
    """
    if objet is None:
        return {}
    if isinstance(objet, dict):
        return objet
    if hasattr(objet, "model_dump"):
        return objet.model_dump(mode="json")
    return dict(objet)


def _executer_run(
    client: ApifyClient, actor: str, payload: dict, timeout_secs: int
) -> tuple[list[dict] | None, str | None]:
    """Lance un run d'actor et récupère l'intégralité de son dataset.

    Un dataset vide n'est pas traité comme une erreur ici : son interprétation
    dépend de l'actor appelé et relève des fonctions publiques du module.

    Args:
        client: Client Apify authentifié.
        actor: Identifiant de l'actor à exécuter.
        payload: Payload d'appel de l'actor.
        timeout_secs: Durée maximale du run.

    Returns:
        Un couple `(items, message_erreur)`. `items` vaut `None` en cas
        d'échec ; `message_erreur` est `None` en cas de succès.
    """
    run = _en_dict(
        client.actor(actor).call(
            run_input=payload,
            run_timeout=timedelta(seconds=timeout_secs),
            wait_duration=timedelta(seconds=timeout_secs + MARGE_ATTENTE_RUN_SECS),
            logger=None,
        )
    )
    if not run:
        return None, "Aucun run retourné par l'API Apify."

    statut = run.get("status")
    if statut != _STATUT_SUCCES:
        message = run.get("status_message") or run.get("statusMessage") or ""
        return None, f"Run Apify en statut {statut}. {message}".strip()

    dataset_id = run.get("default_dataset_id") or run.get("defaultDatasetId")
    if not dataset_id:
        return None, "Run terminé sans dataset associé."

    page = client.dataset(dataset_id).list_items()
    items = getattr(page, "items", None)
    if items is None:
        items = _en_dict(page).get("items") or []
    return [_en_dict(item) for item in items], None


def _client() -> ApifyClient:
    """Instancie le client Apify.

    Returns:
        Le client authentifié.

    Raises:
        RuntimeError: Si aucun jeton n'est présent dans l'environnement.
    """
    if not APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN absent de l'environnement.")
    return ApifyClient(APIFY_TOKEN)


def _avec_tentatives(
    actor: str,
    payload: dict,
    timeout_secs: int,
    libelle: str,
) -> tuple[list[dict] | None, str, int]:
    """Exécute un run avec nouvelles tentatives et attente croissante.

    L'attente entre deux tentatives est délibérément longue : un échec vient le
    plus souvent d'un blocage anti-bot d'Amazon, et réessayer immédiatement
    réutiliserait la session proxy qui vient d'être refusée.

    Args:
        actor: Identifiant de l'actor à exécuter.
        payload: Payload d'appel de l'actor.
        timeout_secs: Durée maximale d'un run.
        libelle: Libellé de l'appel, pour les traces.

    Returns:
        Un triplet `(items, derniere_erreur, nb_tentatives)`. `items` vaut
        `None` si toutes les tentatives ont échoué.
    """
    try:
        client = _client()
    except RuntimeError as exception:
        return None, str(exception), 0

    derniere_erreur = "Échec inconnu."

    for tentative in range(1, NB_TENTATIVES_MAX + 1):
        if tentative > 1:
            attente = BACKOFF_TENTATIVES_SECS[
                min(tentative - 2, len(BACKOFF_TENTATIVES_SECS) - 1)
            ]
            _LOG.warning(
                "Nouvelle tentative %s/%s dans %s s (%s)",
                tentative,
                NB_TENTATIVES_MAX,
                attente,
                derniere_erreur,
            )
            time.sleep(attente)

        # Le payload est logué tel qu'il part, en UTF-8 : c'est le seul moyen de
        # vérifier qu'aucune corruption d'encodage n'affecte l'URL de recherche.
        _LOG.info(
            "Appel Apify %s [%s] (tentative %s/%s) — payload=%s",
            actor,
            libelle,
            tentative,
            NB_TENTATIVES_MAX,
            json.dumps(payload, ensure_ascii=False),
        )

        try:
            items, erreur = _executer_run(client, actor, payload, timeout_secs)
        except Exception as exception:  # noqa: BLE001 — aucune exception ne remonte
            items, erreur = None, f"{type(exception).__name__}: {exception}"

        if items is not None:
            return items, "", tentative

        derniere_erreur = erreur or "Échec inconnu."

    return None, derniere_erreur, NB_TENTATIVES_MAX


# --------------------------------------------------------------------------- #
# Produits
# --------------------------------------------------------------------------- #


def _payload_produits(recherche: RecherchePlanifiee, marketplace: Marketplace) -> dict:
    """Construit le payload de l'actor de collecte de produits.

    Args:
        recherche: Recherche planifiée, dont l'URL est déjà filtrée.
        marketplace: Marketplace interrogée.

    Returns:
        Le payload prêt à être envoyé à l'actor.
    """
    payload: dict[str, Any] = {
        "categoryOrProductUrls": [{"url": recherche.url}],
        "maxItemsPerStartUrl": MAX_PRODUITS_PAR_RECHERCHE,
        # Sans ce plafond, l'actor pagine dans la traîne longue quand une
        # recherche étroite compte moins de produits que le quota demandé.
        "maxSearchPagesPerStartUrl": max(
            MIN_PAGES_SERP,
            math.ceil(MAX_PRODUITS_PAR_RECHERCHE / PRODUITS_PAR_PAGE_SERP),
        ),
        # Visite de chaque fiche produit : sans elle, on n'obtient que les
        # données maigres de la vignette de résultats — ni ASIN, ni rangs Best
        # Sellers, ni détail de la notation.
        "scrapeProductDetails": True,
        # Ajoute le profil du vendeur (note globale, historique) au nom du
        # vendeur, déjà présent sans l'option.
        "scrapeSellers": True,
    }
    if marketplace.domaine in UTILISER_SOLVEUR_CAPTCHA_SUR:
        payload["useCaptchaSolver"] = True
    return payload


def collecter_produits(
    recherche: RecherchePlanifiee, marketplace: Marketplace
) -> tuple[list[dict], StatutCollecte]:
    """Exécute une recherche Amazon et récupère les fiches produits brutes.

    Un run techniquement réussi mais sans produit exploitable n'est pas compté
    comme un échec : le statut est `succes=True` avec `nb_items=0` et un message
    de diagnostic. C'est à l'appelant de décider d'une relance — l'actor écrit
    d'ailleurs ses propres enregistrements `error` DANS le dataset plutôt que de
    faire échouer le run.

    Args:
        recherche: Recherche planifiée à exécuter, une seule par run.
        marketplace: Marketplace interrogée.

    Returns:
        Un couple `(items_bruts, statut)`. Les items sont renvoyés tels que
        l'actor les a produits, enregistrements `error` compris ; leur
        normalisation relève de `normalize`.
    """
    libelle = f"{recherche.mots_cles} / {recherche.tri}"
    items, erreur, tentatives = _avec_tentatives(
        ACTOR_AMAZON_CRAWLER,
        _payload_produits(recherche, marketplace),
        TIMEOUT_RUN_SECS,
        libelle,
    )

    if items is None:
        _LOG.error("Recherche « %s » en échec : %s", recherche.mots_cles, erreur)
        return [], StatutCollecte(
            recherche=recherche.mots_cles,
            type_run=TYPE_RUN_PRODUITS,
            succes=False,
            message_erreur=erreur,
            nb_items=0,
            nb_tentatives=tentatives,
        )

    produits = [item for item in items if not item.get(CLE_ERREUR)]
    if not produits:
        _LOG.warning("Recherche « %s » sans produit : %s", recherche.mots_cles, _MESSAGE_PAGE_VIDE)
        return items, StatutCollecte(
            recherche=recherche.mots_cles,
            type_run=TYPE_RUN_PRODUITS,
            succes=True,
            message_erreur=_MESSAGE_PAGE_VIDE,
            nb_items=0,
            nb_tentatives=tentatives,
        )

    _LOG.info("Recherche « %s » → %s produit(s) brut(s).", recherche.mots_cles, len(produits))
    return items, StatutCollecte(
        recherche=recherche.mots_cles,
        type_run=TYPE_RUN_PRODUITS,
        succes=True,
        message_erreur=None,
        nb_items=len(produits),
        nb_tentatives=tentatives,
    )


# --------------------------------------------------------------------------- #
# Avis
# --------------------------------------------------------------------------- #


def _payload_avis(url_produit: str) -> dict:
    """Construit le payload de l'actor de collecte d'avis.

    Args:
        url_produit: URL de la fiche produit.

    Returns:
        Le payload prêt à être envoyé à l'actor.
    """
    payload: dict[str, Any] = {
        "productUrls": [{"url": url_produit}],
        "maxReviews": NB_AVIS_PAR_PRODUIT,
        "sort": TRI_AVIS,
        "filterByRatings": FILTRE_NOTES_AVIS,
        # Le nom du relecteur est une donnée personnelle sans usage ici.
        "includeGdprSensitive": INCLURE_DONNEES_PERSONNELLES,
        # Un même produit atteignable sous plusieurs ASIN ne doit pas remonter
        # ses avis en double.
        "deduplicateRedirectedAsins": True,
    }
    if ANCIENNETE_MAX_AVIS:
        payload["reviewsCutoffDate"] = ANCIENNETE_MAX_AVIS
    return payload


def collecter_avis(url_produit: str) -> tuple[list[dict], StatutCollecte]:
    """Collecte les avis d'UN produit.

    Args:
        url_produit: URL de la fiche produit à traiter.

    Returns:
        Un couple `(items_bruts, statut)`. L'échec d'un produit ne compromet
        jamais le reste du corpus.
    """
    if not url_produit:
        return [], StatutCollecte(
            recherche="—",
            type_run=TYPE_RUN_AVIS,
            succes=False,
            message_erreur="Produit sans URL : avis non collectables.",
            nb_items=0,
            nb_tentatives=0,
        )

    items, erreur, tentatives = _avec_tentatives(
        ACTOR_AMAZON_AVIS, _payload_avis(url_produit), TIMEOUT_RUN_AVIS_SECS, url_produit
    )

    if items is None:
        _LOG.warning("Avis indisponibles pour %s : %s", url_produit, erreur)
        return [], StatutCollecte(
            recherche=url_produit,
            type_run=TYPE_RUN_AVIS,
            succes=False,
            message_erreur=erreur,
            nb_items=0,
            nb_tentatives=tentatives,
        )

    _LOG.info("Avis collectés pour %s : %s item(s).", url_produit, len(items))
    return items, StatutCollecte(
        recherche=url_produit,
        type_run=TYPE_RUN_AVIS,
        succes=True,
        message_erreur=None,
        nb_items=len(items),
        nb_tentatives=tentatives,
    )
