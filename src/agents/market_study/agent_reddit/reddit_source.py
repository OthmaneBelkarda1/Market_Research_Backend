"""Accès à Reddit via l'actor Apify `harshmaur/reddit-scraper`.

Deux modes d'appel sont exposés, correspondant aux deux phases de collecte :

1. `rechercher_posts` — prospection par mots-clés, **sans commentaires**,
   éventuellement restreinte à un subreddit unique ;
2. `collecter_commentaires` — approfondissement d'une liste d'URLs de posts.

Aucune des deux fonctions ne propage d'exception : toute erreur est convertie
en `StatutCollecte(succes=False, ...)`.

Contraintes documentées de l'actor, respectées ici :

- `withinCommunity` n'accepte qu'**un seul** subreddit par run — la couverture
  multi-subreddits se fait par plusieurs runs, jamais par concaténation ;
- les plans Apify gratuits ne traitent que les 40 premiers mots-clés d'un run ;
- `postedAfter` forcerait le tri `new` et ignorerait `searchTime` : nous
  retenons `searchSort=relevance` + `searchTime` ;
- Reddit plafonne toute liste de résultats à environ 1 000 posts ;
- le mode rapide, actif d'office sur les recherches par mots-clés, peut manquer
  des posts.

Aucun champ `mcp*` du schéma d'entrée n'est renseigné : les connecteurs de
livraison sont hors périmètre.
"""

from __future__ import annotations

import json
import time
from datetime import timedelta
from typing import Any

from apify_client import ApifyClient

from config import (
    ACTOR_REDDIT,
    APIFY_TOKEN,
    BACKOFF_TENTATIVES_SECS,
    FENETRE_RECHERCHE,
    GROUPES_PROXY,
    NB_MIN_POSTS_PAR_REQUETE,
    NB_TENTATIVES_MAX,
    PHASE_COMMENTAIRES,
    PHASE_PROSPECTION_GLOBALE,
    PHASE_PROSPECTION_SUBREDDIT,
    TIMEOUT_RUN_SECS,
    TRI_RECHERCHE,
    obtenir_logger,
)
from schemas import StatutCollecte

_LOG = obtenir_logger(__name__)

_STATUT_SUCCES = "SUCCEEDED"
_MARGE_ATTENTE_SECS = 60
_MOTS_CLES_PROXY = ("proxy", "residential")
_SEPARATEUR_REQUETES = " | "

_MESSAGE_PROSPECTION_VIDE = (
    "Run SUCCEEDED mais dataset vide — aucun résultat : requêtes potentiellement "
    "trop spécifiques ou corrompues (encodage), ou blocage côté Reddit."
)
_MESSAGE_SUBREDDIT_VIDE = (
    "Run SUCCEEDED sans aucun post : le subreddit est inexistant, inactif, ou "
    "ne contient aucune discussion correspondant aux requêtes. Information "
    "légitime, pas un échec de collecte."
)
_MESSAGE_COMMENTAIRES_VIDE = (
    "Run SUCCEEDED mais aucun commentaire renvoyé — fils supprimés, verrouillés "
    "ou vides sur l'ensemble des posts ciblés."
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


def _configuration_proxy() -> dict:
    """Construit la configuration de proxy commune aux deux modes.

    Returns:
        Le sous-objet `proxy` du payload.
    """
    return {"useApifyProxy": True, "apifyProxyGroups": GROUPES_PROXY}


def _payload_recherche(
    requetes: list[str], within_community: str | None, max_posts: int
) -> dict:
    """Construit le payload de prospection par mots-clés.

    ⚠️ `maxPostsCount` est documenté comme un plafond global (« across all
    search results ») mais l'actor l'applique comme un QUOTA PAR MOT-CLÉ :
    mesure de contrôle, 4 requêtes à `maxPostsCount=10` → exactement 10 items
    par requête, 40 au total. Le plafond demandé est donc réparti entre les
    requêtes avant l'envoi, pour que `max_posts` conserve sa sémantique de coût
    total du run.

    Args:
        requetes: Requêtes consommateur à soumettre.
        within_community: Subreddit unique auquel restreindre la recherche, ou
            `None` pour une recherche sur tout Reddit.
        max_posts: Plafond de posts sauvegardés, tous mots-clés confondus.

    Returns:
        Le payload prêt à être envoyé à l'actor.
    """
    plafond_par_requete = max(NB_MIN_POSTS_PAR_REQUETE, max_posts // len(requetes))
    _LOG.info(
        "Plafond réparti : %s post(s) budgété(s) sur %s requête(s) → "
        "maxPostsCount=%s par requête.",
        max_posts,
        len(requetes),
        plafond_par_requete,
    )
    return {
        "searchTerms": requetes,
        "searchPosts": True,
        "searchComments": False,
        "searchCommunities": False,
        "withinCommunity": within_community or "",
        "searchSort": TRI_RECHERCHE,
        "searchTime": FENETRE_RECHERCHE,
        "crawlCommentsPerPost": False,
        "includeNSFW": False,
        "maxPostsCount": plafond_par_requete,
        "proxy": _configuration_proxy(),
    }


def _payload_commentaires(urls_posts: list[str], max_commentaires_par_post: int) -> dict:
    """Construit le payload d'approfondissement d'une liste de posts.

    Args:
        urls_posts: URLs des posts dont les fils sont à collecter.
        max_commentaires_par_post: Plafond de commentaires par post.

    Returns:
        Le payload prêt à être envoyé à l'actor.
    """
    return {
        "startUrls": [{"url": url} for url in urls_posts],
        "crawlCommentsPerPost": True,
        "maxCommentsPerPost": max_commentaires_par_post,
        # `maxPostsCount` plafonne aussi les posts re-sauvegardés par le run :
        # il doit couvrir la totalité des URLs, sans quoi les fils excédentaires
        # sont silencieusement ignorés.
        "maxPostsCount": len(urls_posts),
        "includeNSFW": False,
        "proxy": _configuration_proxy(),
    }


def _executer_run(client: ApifyClient, payload: dict) -> tuple[list[dict] | None, str | None]:
    """Lance un run de l'actor et récupère l'intégralité de son dataset.

    Un dataset vide n'est pas traité ici comme une erreur : son interprétation
    dépend de la phase et relève des fonctions appelantes.

    Args:
        client: Client Apify authentifié.
        payload: Payload d'appel de l'actor.

    Returns:
        Un couple `(items, message_erreur)`. `items` vaut `None` en cas
        d'échec ; `message_erreur` est `None` en cas de succès.
    """
    run = _en_dict(
        client.actor(ACTOR_REDDIT).call(
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
    return [_en_dict(item) for item in items], None


def _appeler_actor(
    payload: dict, phase: str, cible: str
) -> tuple[list[dict], StatutCollecte, bool]:
    """Exécute un run avec retries et convertit toute erreur en statut.

    Args:
        payload: Payload d'appel de l'actor.
        phase: Phase de collecte, pour le statut retourné.
        cible: Libellé de la cible interrogée, pour le statut retourné.

    Returns:
        Un triplet `(items, statut, run_abouti)`. `run_abouti` distingue un run
        techniquement réussi mais vide d'un run en échec : l'interprétation du
        vide appartient à la fonction appelante.
    """
    if not APIFY_TOKEN:
        return (
            [],
            StatutCollecte(
                phase=phase,
                cible=cible,
                succes=False,
                message_erreur="APIFY_TOKEN absent de l'environnement.",
                nb_items=0,
                nb_tentatives=0,
            ),
            False,
        )

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
        # vérifier qu'aucune corruption d'encodage n'affecte les requêtes.
        _LOG.info(
            "Appel Apify %s → %s (tentative %s/%s) — payload=%s",
            phase,
            cible,
            tentative,
            NB_TENTATIVES_MAX,
            json.dumps(payload, ensure_ascii=False),
        )

        try:
            items, erreur = _executer_run(client, payload)
        except Exception as exception:  # noqa: BLE001 — aucune exception ne remonte
            items, erreur = None, f"{type(exception).__name__}: {exception}"

        if items is not None:
            return (
                items,
                StatutCollecte(
                    phase=phase,
                    cible=cible,
                    succes=True,
                    message_erreur=None,
                    nb_items=len(items),
                    nb_tentatives=tentative,
                ),
                True,
            )

        derniere_erreur = erreur or "Échec inconnu."

    _LOG.error("Collecte %s échouée sur « %s » : %s", phase, cible, derniere_erreur)
    return (
        [],
        StatutCollecte(
            phase=phase,
            cible=cible,
            succes=False,
            message_erreur=derniere_erreur,
            nb_items=0,
            nb_tentatives=NB_TENTATIVES_MAX,
        ),
        False,
    )


def rechercher_posts(
    requetes: list[str],
    within_community: str | None,
    max_posts: int,
) -> tuple[list[dict], StatutCollecte]:
    """Recherche des posts Reddit par mots-clés, sans collecter les commentaires.

    Le traitement d'un run réussi mais vide dépend de la portée de la recherche.
    En prospection globale, zéro post signale un problème (requêtes trop
    spécifiques ou corrompues) et produit `succes=False`. En recherche
    restreinte à un subreddit, zéro post est une information légitime — le
    subreddit peut être inexistant, inactif ou hors sujet — et produit
    `succes=True` avec `nb_items=0`. La distinction est déterminante pour
    l'interprétation en aval.

    Args:
        requetes: Requêtes consommateur à soumettre, dans l'ordre de priorité.
        within_community: Subreddit unique auquel restreindre la recherche, ou
            `None` pour interroger tout Reddit.
        max_posts: Plafond de posts sauvegardés, toutes requêtes confondues.

    Returns:
        Un couple `(items_bruts, statut)`. Les items sont renvoyés tels que
        l'actor les a produits ; leur normalisation relève de `normalize`.
    """
    phase = PHASE_PROSPECTION_GLOBALE if within_community is None else PHASE_PROSPECTION_SUBREDDIT
    cible = within_community or _SEPARATEUR_REQUETES.join(requetes)

    if not requetes:
        return [], StatutCollecte(
            phase=phase,
            cible=cible,
            succes=False,
            message_erreur="Aucune requête à soumettre.",
            nb_items=0,
            nb_tentatives=0,
        )

    payload = _payload_recherche(requetes, within_community, max_posts)
    items, statut, run_abouti = _appeler_actor(payload, phase, cible)

    if run_abouti and not items:
        if within_community is None:
            statut.succes = False
            statut.message_erreur = _MESSAGE_PROSPECTION_VIDE
            _LOG.error("Prospection globale sans résultat : %s", _MESSAGE_PROSPECTION_VIDE)
        else:
            statut.message_erreur = _MESSAGE_SUBREDDIT_VIDE
            _LOG.info("Subreddit « %s » sans résultat exploitable.", within_community)
    elif items:
        _LOG.info("Collecte %s → %s : %s item(s).", phase, cible, len(items))

    return items, statut


def collecter_commentaires(
    urls_posts: list[str],
    max_commentaires_par_post: int,
) -> tuple[list[dict], StatutCollecte]:
    """Collecte les fils de commentaires d'une liste de posts, en un seul run.

    Args:
        urls_posts: URLs des posts retenus à l'issue du filtrage de pertinence.
        max_commentaires_par_post: Plafond de commentaires par post.

    Returns:
        Un couple `(items_bruts, statut)`. Le dataset contient à la fois les
        posts ciblés et leurs commentaires : le tri relève de `normalize`.
    """
    cible = f"{len(urls_posts)} URLs"

    if not urls_posts:
        return [], StatutCollecte(
            phase=PHASE_COMMENTAIRES,
            cible=cible,
            succes=False,
            message_erreur="Aucune URL de post à approfondir.",
            nb_items=0,
            nb_tentatives=0,
        )

    payload = _payload_commentaires(urls_posts, max_commentaires_par_post)
    items, statut, run_abouti = _appeler_actor(payload, PHASE_COMMENTAIRES, cible)

    if run_abouti and not items:
        statut.message_erreur = _MESSAGE_COMMENTAIRES_VIDE
        _LOG.warning(_MESSAGE_COMMENTAIRES_VIDE)
    elif items:
        _LOG.info("Collecte des commentaires : %s item(s) bruts.", len(items))

    return items, statut
