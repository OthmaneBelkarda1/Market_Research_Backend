"""Accès aux annonces via l'actor Apify `apify/facebook-ads-scraper`.

Une recherche = un run. L'actor ouvre l'URL qu'on lui donne — ici toujours une
URL de la bibliothèque publicitaire construite par `strategy`, ou une URL de
Page Facebook fournie par l'appelant — et remonte les annonces correspondantes.

Choix structurant du module : **tous les filtres passent par l'URL**, aucun par
le payload. Le pays, le statut de diffusion et le mode d'appariement sont des
paramètres de la bibliothèque publicitaire elle-même ; les pousser dans l'URL
garantit que l'actor voit exactement la page qu'un humain verrait, et évite de
dépendre d'un champ d'entrée dont le nom peut changer d'une version d'actor à
l'autre. Seul le plafond d'annonces (`resultsLimit`) est transmis en payload —
c'est le poste de coût, l'actor étant facturé À L'ANNONCE.

Les fonctions exposées ne propagent jamais d'exception : toute erreur devient un
`StatutCollecte(succes=False, ...)`.
"""

from __future__ import annotations

import json
import time
from datetime import timedelta
from typing import Any

from apify_client import ApifyClient

from config import (
    ACTOR_META_ADS,
    APIFY_TOKEN,
    BACKOFF_TENTATIVES_SECS,
    CLE_ENVELOPPE_RESULTATS,
    CLE_ENVELOPPE_TOTAL,
    MARGE_ATTENTE_RUN_SECS,
    MAX_ANNONCES_PAR_RECHERCHE,
    NB_TENTATIVES_MAX,
    TIMEOUT_RUN_SECS,
    obtenir_logger,
)
from schemas import RecherchePlanifiee, StatutCollecte

_LOG = obtenir_logger(__name__)

_STATUT_SUCCES = "SUCCEEDED"

_MESSAGE_RECHERCHE_VIDE = (
    "Run SUCCEEDED sans aucune annonce. Sur la bibliothèque publicitaire, c'est "
    "le plus souvent un constat réel — personne ne diffuse d'annonce sur ces "
    "mots dans ce pays — mais cela peut aussi venir d'une formulation trop "
    "étroite, d'un filtre de statut trop restrictif, ou d'un blocage de Meta."
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


def _est_enveloppe(item: dict) -> bool:
    """Indique si un item est une enveloppe de résultats plutôt qu'une annonce.

    Args:
        item: Item brut du dataset.

    Returns:
        Vrai s'il s'agit d'une enveloppe.
    """
    return CLE_ENVELOPPE_TOTAL in item or CLE_ENVELOPPE_RESULTATS in item


def deballer(items: list[dict]) -> list[dict]:
    """Ramène le dataset d'un run à une liste plate d'annonces.

    Une recherche sans résultat ne produit PAS un dataset vide : l'actor y écrit
    une enveloppe `{inputUrl, results: [], totalCount: 0, …}`. Prise pour une
    annonce, elle ferait croire à un item collecté et empêcherait de détecter la
    recherche vide — donc de l'élargir.

    Les enveloppes portant des résultats sont dépliées plutôt que jetées :
    `results` a été observé vide, mais rien ne garantit qu'il le soit toujours,
    et perdre des annonces déjà facturées serait le pire des deux risques.

    Args:
        items: Items bruts du dataset, dans l'ordre.

    Returns:
        Les seules annonces, dans l'ordre.
    """
    annonces: list[dict] = []

    for item in items:
        if not _est_enveloppe(item):
            annonces.append(item)
            continue

        resultats = item.get(CLE_ENVELOPPE_RESULTATS) or []
        if not isinstance(resultats, list):
            continue
        # `results` a été servi comme liste d'annonces ; une liste de listes
        # (une par collation) reste possible côté API Meta.
        for resultat in resultats:
            if isinstance(resultat, dict):
                annonces.append(resultat)
            elif isinstance(resultat, list):
                annonces.extend(sous for sous in resultat if isinstance(sous, dict))

    if len(annonces) != len(items):
        _LOG.info(
            "Déballage du dataset : %s item(s) bruts → %s annonce(s).",
            len(items),
            len(annonces),
        )
    return annonces


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


def _executer_run(
    client: ApifyClient, payload: dict, timeout_secs: int
) -> tuple[list[dict] | None, str | None]:
    """Lance un run d'actor et récupère l'intégralité de son dataset.

    Un dataset vide n'est pas traité comme une erreur ici : son interprétation
    relève des fonctions publiques du module.

    Args:
        client: Client Apify authentifié.
        payload: Payload d'appel de l'actor.
        timeout_secs: Durée maximale du run.

    Returns:
        Un couple `(items, message_erreur)`. `items` vaut `None` en cas
        d'échec ; `message_erreur` est `None` en cas de succès.
    """
    run = _en_dict(
        client.actor(ACTOR_META_ADS).call(
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


def _avec_tentatives(
    payload: dict, timeout_secs: int, libelle: str
) -> tuple[list[dict] | None, str, int]:
    """Exécute un run avec nouvelles tentatives et attente croissante.

    L'attente entre deux tentatives est délibérément longue : un échec vient le
    plus souvent d'un blocage de Meta, et réessayer immédiatement réutiliserait
    la session proxy qui vient d'être refusée.

    Args:
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
            ACTOR_META_ADS,
            libelle,
            tentative,
            NB_TENTATIVES_MAX,
            json.dumps(payload, ensure_ascii=False),
        )

        try:
            items, erreur = _executer_run(client, payload, timeout_secs)
        except Exception as exception:  # noqa: BLE001 — aucune exception ne remonte
            items, erreur = None, f"{type(exception).__name__}: {exception}"

        if items is not None:
            return items, "", tentative

        derniere_erreur = erreur or "Échec inconnu."

    return None, derniere_erreur, NB_TENTATIVES_MAX


def _payload(url: str, max_annonces: int) -> dict:
    """Construit le payload de l'actor.

    Args:
        url: URL de recherche ou de Page à ouvrir.
        max_annonces: Plafond d'annonces à rapporter.

    Returns:
        Le payload prêt à être envoyé à l'actor.
    """
    return {
        "startUrls": [{"url": url}],
        # Seul rempart contre une facturation à l'annonce sur une recherche
        # large : une catégorie grand public compte des milliers d'annonces
        # actives sur un grand pays.
        "resultsLimit": max_annonces,
    }


def collecter_annonces(
    recherche: RecherchePlanifiee, max_annonces: int = MAX_ANNONCES_PAR_RECHERCHE
) -> tuple[list[dict], StatutCollecte]:
    """Exécute une recherche et récupère les annonces brutes.

    Un run techniquement réussi mais sans annonce n'est pas compté comme un
    échec : le statut est `succes=True` avec `nb_items=0` et un message de
    diagnostic. Sur cette source, une recherche vide est un résultat en soi —
    c'est à l'appelant de décider d'un élargissement.

    Args:
        recherche: Recherche planifiée à exécuter, une seule par run.
        max_annonces: Plafond d'annonces du run.

    Returns:
        Un couple `(items, statut)`. Les items sont déballés de leur éventuelle
        enveloppe (voir `deballer`) mais non normalisés — c'est le rôle de
        `normalize`.
    """
    items, erreur, tentatives = _avec_tentatives(
        _payload(recherche.url, max_annonces), TIMEOUT_RUN_SECS, recherche.mots_cles
    )

    if items is None:
        _LOG.error("Recherche « %s » en échec : %s", recherche.mots_cles, erreur)
        return [], StatutCollecte(
            recherche=recherche.mots_cles,
            url=recherche.url,
            succes=False,
            message_erreur=erreur,
            nb_items=0,
            nb_tentatives=tentatives,
            plafond_atteint=False,
        )

    # Le déballage passe AVANT tout décompte : une enveloppe de recherche vide
    # compterait sinon pour une annonce collectée.
    items = deballer(items)

    if not items:
        _LOG.warning(
            "Recherche « %s » sans annonce : %s", recherche.mots_cles, _MESSAGE_RECHERCHE_VIDE
        )
        return [], StatutCollecte(
            recherche=recherche.mots_cles,
            url=recherche.url,
            succes=True,
            message_erreur=_MESSAGE_RECHERCHE_VIDE,
            nb_items=0,
            nb_tentatives=tentatives,
            plafond_atteint=False,
        )

    # Le plafond atteint signale un corpus TRONQUÉ : la bibliothèque en
    # contenait davantage, dans un ordre que Meta ne documente pas.
    plafond_atteint = len(items) >= max_annonces
    _LOG.info(
        "Recherche « %s » → %s annonce(s) brute(s)%s.",
        recherche.mots_cles,
        len(items),
        " (plafond atteint)" if plafond_atteint else "",
    )
    return items, StatutCollecte(
        recherche=recherche.mots_cles,
        url=recherche.url,
        succes=True,
        message_erreur=None,
        nb_items=len(items),
        nb_tentatives=tentatives,
        plafond_atteint=plafond_atteint,
    )
