"""Accès à Google Trends via l'actor Apify `data_xplorer/google-trends-fast-scraper`.

Le module expose une unique fonction synchrone, `collecter_tendances`, qui ne
propage jamais d'exception : toute erreur est convertie en
`StatutCollecte(succes=False, ...)`.
"""

from __future__ import annotations

import json
import time
from datetime import timedelta
from typing import Any

from apify_client import ApifyClient

from config import (
    ACTOR_TENDANCES,
    APIFY_TOKEN,
    BACKOFF_TENTATIVES_SECS,
    CLE_TIMELINE_PARTIELLE,
    GROUPES_PROXY,
    MODE_ACTOR,
    NB_TENTATIVES_MAX,
    TIMEFRAME_12M,
    TIMEOUT_RUN_SECS,
    obtenir_logger,
)
from schemas import StatutCollecte

_LOG = obtenir_logger(__name__)

_STATUT_SUCCES = "SUCCEEDED"
_MARGE_ATTENTE_SECS = 60
_MOTS_CLES_PROXY = ("proxy", "residential")


def _horizon_depuis_timeframe(timeframe: str) -> str:
    """Traduit un `predefinedTimeframe` en libellé d'horizon.

    Args:
        timeframe: Valeur envoyée à l'actor, ex. « today 12-m ».

    Returns:
        « 12m » ou « 5y ».
    """
    return "12m" if timeframe == TIMEFRAME_12M else "5y"


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


def _compter_points(item: dict) -> int:
    """Compte les points de la série temporelle d'un item.

    Le décompte porte sur les points bruts renvoyés par la source, dernière
    période partielle incluse — celle-ci est en revanche écartée du calcul des
    indicateurs.

    Args:
        item: Item brut du dataset.

    Returns:
        Le nombre de points datés, `0` si la série est absente ou vide.
    """
    timeline = item.get("timeline_data")
    if not isinstance(timeline, dict):
        return 0
    for cle, valeur in timeline.items():
        if cle != CLE_TIMELINE_PARTIELLE and isinstance(valeur, dict):
            return len(valeur)
    return 0


def _construire_payload(
    terme: str, geo: str, timeframe: str, fetch_regional: bool
) -> dict:
    """Construit le payload d'appel de l'actor.

    Args:
        terme: Mot-clé unique à interroger.
        geo: Code pays ISO-2.
        timeframe: Période prédéfinie de l'actor.
        fetch_regional: Demande la ventilation régionale.

    Returns:
        Le payload prêt à être envoyé.
    """
    return {
        "mode": MODE_ACTOR,
        "keyword": terme,
        "predefinedTimeframe": timeframe,
        "geo": geo,
        "fetchRegionalData": fetch_regional,
        "proxyConfiguration": {
            "useApifyProxy": True,
            "apifyProxyGroups": GROUPES_PROXY,
        },
    }


def _executer_run(client: ApifyClient, payload: dict) -> tuple[dict | None, str | None]:
    """Lance un run de l'actor et récupère le premier item du dataset.

    Args:
        client: Client Apify authentifié.
        payload: Payload d'appel de l'actor.

    Returns:
        Un couple `(item, message_erreur)`. `item` vaut `None` en cas d'échec ou
        de dataset vide ; `message_erreur` est `None` en cas de succès.
    """
    run = _en_dict(
        client.actor(ACTOR_TENDANCES).call(
            run_input=payload,
            run_timeout=timedelta(seconds=TIMEOUT_RUN_SECS),
            wait_duration=timedelta(seconds=TIMEOUT_RUN_SECS + _MARGE_ATTENTE_SECS),
            logger=None,
        )
    )
    if not run:
        return None, "Aucun run retourné par l'API Apify."

    statut = run.get("status")
    if statut != _STATUT_SUCCES:
        message = run.get("status_message") or run.get("statusMessage") or ""
        detail = f"Run Apify en statut {statut}. {message}".strip()
        if any(mot in message.lower() for mot in _MOTS_CLES_PROXY):
            detail = (
                "Proxies résidentiels indisponibles ou refusés sur ce compte Apify "
                f"— {detail}"
            )
        return None, detail

    dataset_id = run.get("default_dataset_id") or run.get("defaultDatasetId")
    if not dataset_id:
        return None, "Run terminé sans dataset associé."

    page = client.dataset(dataset_id).list_items()
    items = getattr(page, "items", None)
    if items is None:
        items = _en_dict(page).get("items") or []
    items = [_en_dict(item) for item in items]

    # Succès silencieux : l'actor peut se terminer en SUCCEEDED tout en renvoyant
    # un dataset vide ou un item aux listes vides (blocage reCAPTCHA côté Google).
    # Ce cas doit être remonté comme un échec, jamais comme une série nulle :
    # « aucune tendance mesurée » et « tendance nulle mesurée » conduisent à des
    # décisions opposées. Une série présente et intégralement à zéro reste, elle,
    # un succès — c'est une mesure, pas une absence de mesure.
    if not items:
        return None, (
            "Run SUCCEEDED mais dataset vide — blocage probable côté Google "
            "(reCAPTCHA) ou terme non reconnu."
        )

    item = items[0]
    if _compter_points(item) == 0:
        return None, (
            "Run SUCCEEDED mais série temporelle vide dans l'item renvoyé — "
            "blocage probable côté Google (reCAPTCHA)."
        )
    return item, None


def collecter_tendances(
    terme: str,
    geo: str,
    timeframe: str,
    fetch_regional: bool = False,
) -> tuple[dict | None, StatutCollecte]:
    """Interroge Google Trends pour un mot-clé unique.

    L'actor n'accepte qu'un seul mot-clé par run (`keyword` est une chaîne) :
    aucune comparaison multi-termes n'est possible, les indices de deux runs
    distincts étant normalisés séparément.

    Args:
        terme: Mot-clé unique à interroger.
        geo: Code pays ISO-2 en majuscules.
        timeframe: « today 12-m » ou « today 5-y ».
        fetch_regional: Demande la ventilation régionale (nécessite un `geo`).

    Returns:
        Un couple `(item, statut)` : l'item brut du dataset — `None` en cas
        d'échec — et le compte rendu de la collecte.
    """
    horizon = _horizon_depuis_timeframe(timeframe)

    if not APIFY_TOKEN:
        return None, StatutCollecte(
            horizon=horizon,
            terme_interroge=terme,
            succes=False,
            message_erreur="APIFY_TOKEN absent de l'environnement.",
            nb_points=0,
            nb_tentatives=0,
        )

    payload = _construire_payload(terme, geo, timeframe, fetch_regional)
    client = ApifyClient(APIFY_TOKEN)
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
        # vérifier qu'aucune corruption d'encodage n'affecte le mot-clé.
        _LOG.info(
            "Appel Apify (tentative %s/%s) — payload=%s",
            tentative,
            NB_TENTATIVES_MAX,
            json.dumps(payload, ensure_ascii=False),
        )

        try:
            item, erreur = _executer_run(client, payload)
        except Exception as exception:  # noqa: BLE001 — aucune exception ne remonte
            item, erreur = None, f"{type(exception).__name__}: {exception}"

        if item is not None:
            nb_points = _compter_points(item)
            _LOG.info(
                "Collecte %s réussie pour « %s » — %s points", horizon, terme, nb_points
            )
            return item, StatutCollecte(
                horizon=horizon,
                terme_interroge=terme,
                succes=True,
                message_erreur=None,
                nb_points=nb_points,
                nb_tentatives=tentative,
            )

        derniere_erreur = erreur or "Échec inconnu."

    _LOG.error("Collecte %s échouée pour « %s » : %s", horizon, terme, derniere_erreur)
    return None, StatutCollecte(
        horizon=horizon,
        terme_interroge=terme,
        succes=False,
        message_erreur=derniere_erreur,
        nb_points=0,
        nb_tentatives=NB_TENTATIVES_MAX,
    )
