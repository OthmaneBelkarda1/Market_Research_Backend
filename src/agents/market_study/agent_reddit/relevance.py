"""Dédoublonnage et filtrage de pertinence du corpus de posts.

Le filtrage se fait en deux temps :

1. un filtre **déterministe**, sans LLM : dédoublonnage par identifiant de post
   entre les runs — les recherches globale et restreintes se recouvrent — et
   élimination des posts dont ni le titre ni le texte ne sont exploitables ;
2. un **scoring LLM par lots**, qui note la proximité de chaque post avec la
   catégorie de besoin couverte par le produit.

Dégradation gracieuse : un lot dont le scoring échoue n'est jamais écarté. Ses
posts sont conservés avec `pertinence=None`, sans avoir été confrontés au
seuil. Une défaillance du LLM ne doit jamais vider le corpus.
"""

from __future__ import annotations

import time

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate

from config import (
    ANTHROPIC_API_KEY,
    BACKOFF_TENTATIVES_SECS,
    LONGUEUR_EXTRAIT_PERTINENCE,
    LONGUEUR_MIN_TITRE_EXPLOITABLE,
    MAX_TOKENS_LLM,
    MODELE_CLAUDE,
    NB_TENTATIVES_MAX,
    SEUIL_PERTINENCE,
    TAILLE_LOT_PERTINENCE,
    TEMPERATURE_LLM,
    obtenir_logger,
)
from schemas import FicheProduit, LotScoresPertinence, PostReddit

_LOG = obtenir_logger(__name__)

_SCORE_MIN = 0.0
_SCORE_MAX = 1.0
_SUFFIXE_TRONCATURE = "…"

_PROMPT_PERTINENCE = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Tu es analyste d'études de marché. Tu évalues si des discussions "
            "Reddit relèvent de la CATÉGORIE DE BESOIN couverte par un produit.\n\n"
            "RÈGLES :\n"
            "1. Note chaque post de 0 à 1. La question est : « ce post parle-t-il "
            "de la catégorie de produit ou du besoin d'usage couvert par la fiche ? »\n"
            "2. Repères de notation :\n"
            "   - 0.9–1.0 : le post porte directement sur ce type de produit "
            "(demande d'avis, comparaison, retour d'usage, problème rencontré).\n"
            "   - 0.6–0.8 : le post porte sur la catégorie élargie ou sur le besoin "
            "d'usage, sans viser exactement ce type de produit.\n"
            "   - 0.3–0.5 : lien indirect ou incertain.\n"
            "   - 0.0–0.2 : hors sujet. La recherche Reddit remonte beaucoup de "
            "bruit : un post sans rapport doit être noté sévèrement.\n"
            "3. Un post rédigé dans une autre langue que celle de la fiche n'est "
            "PAS pénalisé : seul le sujet compte.\n"
            "4. La marque exacte du produit n'est PAS requise : un post sur une "
            "marque concurrente de la même catégorie est pertinent.\n"
            "5. Renvoie EXACTEMENT un score par post soumis, en reprenant l'index "
            "indiqué. N'invente aucun index, n'en omets aucun.",
        ),
        (
            "human",
            "FICHE PRODUIT\n"
            "Nom : {nom}\n"
            "Catégorie : {categorie}\n"
            "Description : {description}\n\n"
            "POSTS À NOTER\n{posts}",
        ),
    ]
)


def _modele() -> ChatAnthropic:
    """Instancie le modèle Claude utilisé par le scoring.

    Returns:
        Le client `ChatAnthropic` configuré.

    Raises:
        RuntimeError: Si `ANTHROPIC_API_KEY` est absente de l'environnement.
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY absente de l'environnement.")
    return ChatAnthropic(
        model=MODELE_CLAUDE,
        temperature=TEMPERATURE_LLM,
        max_tokens=MAX_TOKENS_LLM,
        api_key=ANTHROPIC_API_KEY,
    )


def dedoublonner(posts: list[PostReddit]) -> list[PostReddit]:
    """Applique le filtre déterministe : doublons et posts inexploitables.

    Les recherches globale et restreintes par subreddit se recouvrent : un même
    post peut être remonté par plusieurs runs. La première occurrence est
    conservée, ce qui privilégie l'origine `recherche_globale` traitée en
    premier par l'agent.

    Args:
        posts: Posts normalisés issus de l'ensemble des runs de prospection.

    Returns:
        Les posts uniques et exploitables, dans l'ordre de première apparition.
    """
    vus: set[str] = set()
    retenus: list[PostReddit] = []
    doublons = 0
    inexploitables = 0

    for post in posts:
        if post.id in vus:
            doublons += 1
            continue
        titre = post.titre.strip()
        texte = (post.texte or "").strip()
        if len(titre) < LONGUEUR_MIN_TITRE_EXPLOITABLE and not texte:
            inexploitables += 1
            continue
        vus.add(post.id)
        retenus.append(post)

    _LOG.info(
        "Filtre déterministe : %s post(s) en entrée → %s retenu(s) "
        "(%s doublon(s), %s inexploitable(s)).",
        len(posts),
        len(retenus),
        doublons,
        inexploitables,
    )
    return retenus


def _extrait(post: PostReddit) -> str:
    """Produit l'extrait de texte transmis au scoring.

    Args:
        post: Post à résumer.

    Returns:
        Les `LONGUEUR_EXTRAIT_PERTINENCE` premiers caractères du corps, ou une
        chaîne vide si le post n'a pas de corps.
    """
    texte = (post.texte or "").strip().replace("\n", " ")
    if len(texte) <= LONGUEUR_EXTRAIT_PERTINENCE:
        return texte
    return texte[:LONGUEUR_EXTRAIT_PERTINENCE] + _SUFFIXE_TRONCATURE


def _formater_lot(lot: list[PostReddit]) -> str:
    """Met en forme un lot de posts pour le prompt de scoring.

    Args:
        lot: Posts du lot.

    Returns:
        Le bloc texte numéroté soumis au modèle.
    """
    lignes = []
    for index, post in enumerate(lot):
        lignes.append(
            f"[{index}] subreddit={post.subreddit}\n"
            f"    titre : {post.titre}\n"
            f"    extrait : {_extrait(post) or '(aucun texte)'}"
        )
    return "\n".join(lignes)


def _scorer_lot(lot: list[PostReddit], produit: FicheProduit) -> dict[int, float] | None:
    """Score un lot de posts, avec retries.

    Args:
        lot: Posts du lot.
        produit: Fiche produit servant de référence de pertinence.

    Returns:
        Les scores indexés sur la position dans le lot, ou `None` si le scoring
        a échoué après épuisement des tentatives.
    """
    chaine = _PROMPT_PERTINENCE | _modele().with_structured_output(LotScoresPertinence)
    entree = {
        "nom": produit.nom,
        "categorie": produit.categorie,
        "description": produit.description,
        "posts": _formater_lot(lot),
    }
    derniere_erreur = "Échec inconnu."

    for tentative in range(1, NB_TENTATIVES_MAX + 1):
        if tentative > 1:
            attente = BACKOFF_TENTATIVES_SECS[
                min(tentative - 2, len(BACKOFF_TENTATIVES_SECS) - 1)
            ]
            _LOG.warning(
                "Scoring : nouvelle tentative %s/%s dans %s s (%s)",
                tentative,
                NB_TENTATIVES_MAX,
                attente,
                derniere_erreur,
            )
            time.sleep(attente)
        try:
            resultat: LotScoresPertinence = chaine.invoke(entree)
        except Exception as exception:  # noqa: BLE001 — converti en dégradation gracieuse
            derniere_erreur = f"{type(exception).__name__}: {exception}"
            continue

        return {
            score.index: min(max(score.score, _SCORE_MIN), _SCORE_MAX)
            for score in resultat.scores
            if 0 <= score.index < len(lot)
        }

    _LOG.error("Scoring d'un lot de %s post(s) abandonné : %s", len(lot), derniere_erreur)
    return None


def filtrer_par_pertinence(
    posts: list[PostReddit], produit: FicheProduit
) -> tuple[list[PostReddit], int]:
    """Note les posts par lots et écarte ceux sous le seuil de pertinence.

    Un post non scoré — lot en échec, ou index absent de la réponse du modèle —
    est conservé avec `pertinence=None` : le filtrage ne doit jamais détruire
    de corpus faute de notation.

    Args:
        posts: Posts dédoublonnés à qualifier.
        produit: Fiche produit servant de référence de pertinence.

    Returns:
        Un couple `(posts_retenus, nb_posts_non_scores)`. Les posts retenus sont
        triés par pertinence décroissante, les posts non scorés en fin de liste.
    """
    if not posts:
        return [], 0

    retenus: list[PostReddit] = []
    nb_non_scores = 0
    nb_ecartes = 0

    for debut in range(0, len(posts), TAILLE_LOT_PERTINENCE):
        lot = posts[debut : debut + TAILLE_LOT_PERTINENCE]
        scores = _scorer_lot(lot, produit)

        for index, post in enumerate(lot):
            score = None if scores is None else scores.get(index)
            if score is None:
                nb_non_scores += 1
                retenus.append(post)
                continue
            post.pertinence = score
            if score >= SEUIL_PERTINENCE:
                retenus.append(post)
            else:
                nb_ecartes += 1

    retenus.sort(
        key=lambda post: (post.pertinence is not None, post.pertinence or _SCORE_MIN),
        reverse=True,
    )
    _LOG.info(
        "Filtrage de pertinence : %s post(s) → %s retenu(s), %s écarté(s) sous "
        "le seuil %s, %s non scoré(s).",
        len(posts),
        len(retenus),
        nb_ecartes,
        SEUIL_PERTINENCE,
        nb_non_scores,
    )
    return retenus, nb_non_scores
