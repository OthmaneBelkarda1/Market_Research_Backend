"""Accès à la recherche web via l'actor Apify `apify/rag-web-browser`.

Une requête = un run. Le champ `query` de l'actor est une chaîne UNIQUE — une
recherche Google ou une URL directe — et jamais une concaténation de requêtes :
l'actor traiterait l'ensemble comme une seule recherche et le ciblage serait
perdu.

Limitation régionale documentée de l'actor, constatée à l'exploration : la SERP
est interrogée depuis les États-Unis, en anglais, sans aucun paramètre de pays
ni de langue de recherche. `serpProxyGroup` sélectionne un groupe de proxies
SERP (`GOOGLE_SERP` ou `SHADER`), pas un pays. Aucun contournement par proxy
n'est tenté ici : le ciblage régional repose uniquement sur la construction des
requêtes (`site:.<tld>` et mots-clés géographiques).

La fonction exposée ne propage jamais d'exception : toute erreur devient un
`StatutCollecte(succes=False, ...)`.
"""

from __future__ import annotations

import json
import time
from datetime import timedelta
from typing import Any

from apify_client import ApifyClient

from config import (
    ACTOR_RAG_WEB_BROWSER,
    APIFY_TOKEN,
    BACKOFF_TENTATIVES_SECS,
    CIBLAGE_OUVERT,
    CIBLAGE_TLD,
    MARGE_ATTENTE_RUN_SECS,
    MAX_RESULTS_PAR_REQUETE,
    NB_TENTATIVES_MAX,
    REQUEST_TIMEOUT_SECS,
    SCRAPING_TOOL,
    TIMEOUT_RUN_SECS,
    obtenir_logger,
)
from schemas import RequetePlanifiee, StatutCollecte

_LOG = obtenir_logger(__name__)

_STATUT_SUCCES = "SUCCEEDED"
_FORMAT_SORTIE = "markdown"

_MESSAGE_TLD_VIDE = (
    "Run SUCCEEDED sans aucune page : le TLD ciblé est peu doté en contenu sur "
    "cette requête. Information légitime sur le marché, pas un échec de collecte."
)
_MESSAGE_GEO_VIDE = (
    "Run SUCCEEDED sans aucune page : aucun résultat pour cette formulation "
    "géographique. Information légitime, pas un échec de collecte."
)
_MESSAGE_OUVERTE_VIDE = (
    "Run SUCCEEDED mais dataset vide sur une requête SANS ciblage régional — "
    "symptôme d'un problème de requête (encodage, formulation) ou de proxy SERP, "
    "et non d'une absence de contenu."
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


def _payload(requete: RequetePlanifiee) -> dict:
    """Construit le payload d'appel de l'actor pour une requête.

    Les champs correspondent au schéma d'entrée du build `version-1`, relevé le
    01/08/2026 : seuls `query` (requis), `maxResults`, `outputFormats`,
    `requestTimeoutSecs` et `scrapingTool` sont renseignés. Les valeurs par
    défaut de l'actor sont conservées pour le reste, `serpProxyGroup` compris.

    Args:
        requete: Requête planifiée à exécuter.

    Returns:
        Le payload prêt à être envoyé à l'actor.
    """
    return {
        "query": requete.texte,
        "maxResults": MAX_RESULTS_PAR_REQUETE,
        "outputFormats": [_FORMAT_SORTIE],
        "requestTimeoutSecs": REQUEST_TIMEOUT_SECS,
        "scrapingTool": SCRAPING_TOOL,
    }


def _executer_run(client: ApifyClient, payload: dict) -> tuple[list[dict] | None, str | None]:
    """Lance un run de l'actor et récupère l'intégralité de son dataset.

    Un dataset vide n'est pas traité comme une erreur ici : son interprétation
    dépend du mode de ciblage et relève de `rechercher_pages`.

    Args:
        client: Client Apify authentifié.
        payload: Payload d'appel de l'actor.

    Returns:
        Un couple `(items, message_erreur)`. `items` vaut `None` en cas
        d'échec ; `message_erreur` est `None` en cas de succès.
    """
    run = _en_dict(
        client.actor(ACTOR_RAG_WEB_BROWSER).call(
            run_input=payload,
            run_timeout=timedelta(seconds=TIMEOUT_RUN_SECS),
            wait_duration=timedelta(seconds=TIMEOUT_RUN_SECS + MARGE_ATTENTE_RUN_SECS),
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


def rechercher_pages(
    requete: RequetePlanifiee,
) -> tuple[list[dict], StatutCollecte]:
    """Exécute une requête de recherche web et récupère les pages en Markdown.

    Un run réussi mais vide s'interprète selon le mode de ciblage. En ciblage
    `tld` ou `geo_keywords`, zéro page est une information légitime sur le
    marché — un TLD national peu doté en contenu éditorial, par exemple — et
    produit `succes=True` avec `nb_pages=0`. En ciblage `ouverte`, zéro page
    signale un problème de requête ou de proxy et produit `succes=False` : une
    requête sans restriction ne peut normalement pas être stérile.

    Args:
        requete: Requête planifiée à exécuter, une seule par run.

    Returns:
        Un couple `(items_bruts, statut)`. Les items sont renvoyés tels que
        l'actor les a produits ; leur normalisation relève de `normalize`.
    """
    if not requete.texte.strip():
        return [], StatutCollecte(
            requete=requete.texte,
            succes=False,
            message_erreur="Requête vide.",
            nb_pages=0,
            nb_tentatives=0,
        )

    if not APIFY_TOKEN:
        return [], StatutCollecte(
            requete=requete.texte,
            succes=False,
            message_erreur="APIFY_TOKEN absent de l'environnement.",
            nb_pages=0,
            nb_tentatives=0,
        )

    client = ApifyClient(APIFY_TOKEN)
    payload = _payload(requete)
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
        # vérifier qu'aucune corruption d'encodage n'affecte les accents, et que
        # l'opérateur site: est bien présent là où il doit l'être.
        _LOG.info(
            "Appel Apify [%s/%s] (tentative %s/%s) — payload=%s",
            requete.axe,
            requete.ciblage,
            tentative,
            NB_TENTATIVES_MAX,
            json.dumps(payload, ensure_ascii=False),
        )

        try:
            items, erreur = _executer_run(client, payload)
        except Exception as exception:  # noqa: BLE001 — aucune exception ne remonte
            items, erreur = None, f"{type(exception).__name__}: {exception}"

        if items is not None:
            return items, _statut_run_abouti(requete, items, tentative)

        derniere_erreur = erreur or "Échec inconnu."

    _LOG.error("Requête « %s » en échec : %s", requete.texte, derniere_erreur)
    return [], StatutCollecte(
        requete=requete.texte,
        succes=False,
        message_erreur=derniere_erreur,
        nb_pages=0,
        nb_tentatives=NB_TENTATIVES_MAX,
    )


def _statut_run_abouti(
    requete: RequetePlanifiee, items: list[dict], tentative: int
) -> StatutCollecte:
    """Qualifie un run techniquement réussi, dataset vide compris.

    Args:
        requete: Requête exécutée.
        items: Items bruts renvoyés par le run.
        tentative: Numéro de la tentative ayant abouti.

    Returns:
        Le statut de collecte correspondant.
    """
    if items:
        _LOG.info(
            "Requête « %s » → %s page(s) brute(s).", requete.texte, len(items)
        )
        return StatutCollecte(
            requete=requete.texte,
            succes=True,
            message_erreur=None,
            nb_pages=len(items),
            nb_tentatives=tentative,
        )

    if requete.ciblage == CIBLAGE_OUVERT:
        _LOG.error("Requête ouverte stérile : %s", _MESSAGE_OUVERTE_VIDE)
        return StatutCollecte(
            requete=requete.texte,
            succes=False,
            message_erreur=_MESSAGE_OUVERTE_VIDE,
            nb_pages=0,
            nb_tentatives=tentative,
        )

    message = _MESSAGE_TLD_VIDE if requete.ciblage == CIBLAGE_TLD else _MESSAGE_GEO_VIDE
    _LOG.info("Requête « %s » sans résultat : %s", requete.texte, message)
    return StatutCollecte(
        requete=requete.texte,
        succes=True,
        message_erreur=message,
        nb_pages=0,
        nb_tentatives=tentative,
    )
