"""Normalisation, anonymisation RGPD et statistiques descriptives du corpus.

Aucun appel LLM, aucun effet de bord : toutes les fonctions de ce module sont
pures.

Le mapping des items bruts vers les modèles de sortie repose exclusivement sur
le schéma **constaté** lors des runs d'exploration de l'actor (30/07/2026),
relevé dans le README. Les noms de champs sont centralisés dans `config`.

Anonymisation : le pseudonyme d'auteur est remplacé par les
`LONGUEUR_HASH_PSEUDO` premiers caractères de `sha256(pseudo + SEL_ANONYMISATION)`.
Les modèles de sortie étant fermés, aucun champ de profil utilisateur (karma,
avatar, URL de profil, ancienneté, flair, statut premium) ne peut subsister
dans le résultat. Le texte des posts et des commentaires est conservé tel quel.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timezone

from config import (
    CLE_COMMENTAIRE_AUTEUR,
    CLE_COMMENTAIRE_DATE,
    CLE_COMMENTAIRE_ID,
    CLE_COMMENTAIRE_ID_POST,
    CLE_COMMENTAIRE_PROFONDEUR,
    CLE_COMMENTAIRE_SCORE,
    CLE_COMMENTAIRE_TEXTE,
    CLE_POST_AUTEUR,
    CLE_POST_DATE,
    CLE_POST_ID,
    CLE_POST_NB_COMMENTAIRES,
    CLE_POST_REQUETE,
    CLE_POST_SCORE,
    CLE_POST_SUBREDDIT,
    CLE_POST_TEXTE,
    CLE_POST_TITRE,
    CLE_POST_URL,
    CLE_TYPE_ITEM,
    LANGUE_ANGLAISE,
    LONGUEUR_HASH_PSEUDO,
    PORTEE_GLOBALE,
    PORTEE_REGIONALE,
    PREFIXE_SUBREDDIT,
    PSEUDO_ANONYME,
    PSEUDOS_NON_NOMINATIFS,
    SEL_ANONYMISATION,
    SEL_ANONYMISATION_FOURNI,
    TYPE_ITEM_COMMENTAIRE,
    TYPE_ITEM_POST,
    obtenir_logger,
)
from schemas import CommentaireReddit, PostReddit, StatsCorpus

_LOG = obtenir_logger(__name__)

_SUFFIXE_UTC = "Z"
_FORMAT_DATE_UTC = "%Y-%m-%dT%H:%M:%SZ"
_DATE_INCONNUE = ""

if not SEL_ANONYMISATION_FOURNI:
    _LOG.warning(
        "SEL_ANONYMISATION absent de l'environnement : le sel de repli est "
        "public, les empreintes d'auteur sont réversibles par force brute."
    )


def pseudonymiser(pseudo: str | None) -> str:
    """Remplace un pseudonyme Reddit par une empreinte non réversible.

    Les marqueurs de compte supprimé (« [deleted] », « [removed] ») ne
    désignent aucune personne : les hacher créerait autant de faux auteurs
    distincts qu'il y a de marqueurs. Ils sont donc regroupés sous une valeur
    unique.

    Args:
        pseudo: Pseudonyme brut renvoyé par l'actor, éventuellement nul.

    Returns:
        Les `LONGUEUR_HASH_PSEUDO` premiers caractères hexadécimaux du sha256
        salé, ou la valeur de regroupement des auteurs non nominatifs.
    """
    pseudo_nettoye = (pseudo or "").strip()
    if pseudo_nettoye.casefold() in PSEUDOS_NON_NOMINATIFS:
        return PSEUDO_ANONYME

    empreinte = hashlib.sha256(
        f"{pseudo_nettoye}{SEL_ANONYMISATION}".encode("utf-8")
    ).hexdigest()
    return empreinte[:LONGUEUR_HASH_PSEUDO]


def _normaliser_date(valeur: object) -> str:
    """Ramène une date brute au format ISO 8601 UTC canonique.

    Args:
        valeur: Date telle que renvoyée par l'actor, ex. « 2026-06-04T07:56:49.000Z ».

    Returns:
        La date au format `YYYY-MM-DDTHH:MM:SSZ`, ou une chaîne vide si la
        valeur est absente ou illisible.
    """
    if not isinstance(valeur, str) or not valeur.strip():
        return _DATE_INCONNUE
    texte = valeur.strip()
    try:
        horodatage = datetime.fromisoformat(texte.replace(_SUFFIXE_UTC, "+00:00"))
    except ValueError:
        _LOG.warning("Date illisible ignorée : %r", texte)
        return _DATE_INCONNUE
    if horodatage.tzinfo is None:
        horodatage = horodatage.replace(tzinfo=timezone.utc)
    return horodatage.astimezone(timezone.utc).strftime(_FORMAT_DATE_UTC)


def _normaliser_subreddit(valeur: object) -> str:
    """Ramène un nom de subreddit à la forme préfixée « r/nom ».

    Args:
        valeur: Nom brut, avec ou sans préfixe.

    Returns:
        Le nom préfixé, ou une chaîne vide si la valeur est inexploitable.
    """
    if not isinstance(valeur, str) or not valeur.strip():
        return ""
    nom = valeur.strip()
    if nom.casefold().startswith(PREFIXE_SUBREDDIT):
        return f"{PREFIXE_SUBREDDIT}{nom[len(PREFIXE_SUBREDDIT):]}"
    return f"{PREFIXE_SUBREDDIT}{nom}"


def _cle_subreddit(valeur: str) -> str:
    """Produit la forme comparable d'un nom de subreddit.

    Args:
        valeur: Nom de subreddit, préfixé ou non.

    Returns:
        Le nom en minuscules, sans préfixe ni espaces.
    """
    nom = valeur.strip().casefold()
    if nom.startswith(PREFIXE_SUBREDDIT):
        nom = nom[len(PREFIXE_SUBREDDIT) :]
    return nom.strip("/ ")


def _entier_ou_none(valeur: object) -> int | None:
    """Convertit une valeur brute en entier lorsque c'est possible.

    Args:
        valeur: Valeur brute issue du dataset.

    Returns:
        L'entier correspondant, ou `None` si la conversion est impossible.
    """
    if isinstance(valeur, bool) or valeur is None:
        return None
    if isinstance(valeur, int):
        return valeur
    if isinstance(valeur, float):
        return int(valeur)
    return None


def _texte_ou_none(valeur: object) -> str | None:
    """Nettoie un champ texte optionnel.

    Args:
        valeur: Valeur brute issue du dataset.

    Returns:
        Le texte débarrassé de ses espaces de bord, ou `None` s'il est vide.
    """
    if not isinstance(valeur, str):
        return None
    texte = valeur.strip()
    return texte or None


def separer_par_type(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Sépare les items bruts en posts et commentaires.

    Le dataset de l'actor est hétérogène : posts et commentaires y cohabitent,
    distingués par le champ `dataType` (« post » ou « comment »).

    Args:
        items: Items bruts d'un run.

    Returns:
        Un couple `(posts, commentaires)`.
    """
    posts = [item for item in items if item.get(CLE_TYPE_ITEM) == TYPE_ITEM_POST]
    commentaires = [
        item for item in items if item.get(CLE_TYPE_ITEM) == TYPE_ITEM_COMMENTAIRE
    ]
    ignores = len(items) - len(posts) - len(commentaires)
    if ignores:
        _LOG.warning("%s item(s) de type inattendu ignoré(s).", ignores)
    return posts, commentaires


def _attribuer_portee(
    subreddit: str,
    requete_source: str | None,
    subreddits_regionaux: set[str],
    requetes_marche: set[str],
    langue: str,
) -> str:
    """Attribue la portée d'un post.

    Règle d'approximation, documentée comme telle : un post est « regionale »
    s'il provient d'un subreddit régional ciblé, ou s'il a été remonté par une
    requête rédigée dans la langue du marché lorsque celle-ci n'est pas
    l'anglais. Reddit n'expose aucune information de géolocalisation.

    Args:
        subreddit: Subreddit du post, préfixé.
        requete_source: Requête ayant fait remonter le post, si connue.
        subreddits_regionaux: Subreddits régionaux ciblés, en forme comparable.
        requetes_marche: Requêtes en langue du marché, en minuscules.
        langue: Code langue du marché.

    Returns:
        `PORTEE_REGIONALE` ou `PORTEE_GLOBALE`.
    """
    if _cle_subreddit(subreddit) in subreddits_regionaux:
        return PORTEE_REGIONALE
    if (
        langue.casefold() != LANGUE_ANGLAISE
        and requete_source
        and requete_source.strip().casefold() in requetes_marche
    ):
        return PORTEE_REGIONALE
    return PORTEE_GLOBALE


def normaliser_posts(
    items: list[dict],
    origine: str,
    subreddits_regionaux: list[str],
    requetes_marche: list[str],
    langue: str,
) -> list[PostReddit]:
    """Normalise et anonymise les posts bruts d'un run de prospection.

    Args:
        items: Items bruts du run, posts et commentaires mêlés.
        origine: `ORIGINE_RECHERCHE_GLOBALE` ou `ORIGINE_SUBREDDIT_CIBLE`.
        subreddits_regionaux: Subreddits régionaux de la stratégie.
        requetes_marche: Requêtes rédigées dans la langue du marché.
        langue: Code langue du marché.

    Returns:
        Les posts normalisés, sans score de pertinence à ce stade.
    """
    bruts, _ = separer_par_type(items)
    cles_regionales = {_cle_subreddit(nom) for nom in subreddits_regionaux}
    cles_requetes = {requete.strip().casefold() for requete in requetes_marche}

    posts: list[PostReddit] = []
    for brut in bruts:
        identifiant = _texte_ou_none(brut.get(CLE_POST_ID))
        url = _texte_ou_none(brut.get(CLE_POST_URL))
        if not identifiant or not url:
            _LOG.warning("Post sans identifiant ou sans URL ignoré.")
            continue

        subreddit = _normaliser_subreddit(brut.get(CLE_POST_SUBREDDIT))
        requete_source = _texte_ou_none(brut.get(CLE_POST_REQUETE))
        posts.append(
            PostReddit(
                id=identifiant,
                titre=(_texte_ou_none(brut.get(CLE_POST_TITRE)) or ""),
                texte=_texte_ou_none(brut.get(CLE_POST_TEXTE)),
                subreddit=subreddit,
                url=url,
                date_creation=_normaliser_date(brut.get(CLE_POST_DATE)),
                score=_entier_ou_none(brut.get(CLE_POST_SCORE)),
                nb_commentaires=_entier_ou_none(brut.get(CLE_POST_NB_COMMENTAIRES)),
                portee=_attribuer_portee(
                    subreddit, requete_source, cles_regionales, cles_requetes, langue
                ),
                origine=origine,
                pertinence=None,
                auteur_pseudonymise=pseudonymiser(brut.get(CLE_POST_AUTEUR)),
                requete_source=requete_source,
            )
        )
    return posts


def normaliser_commentaires(
    items: list[dict], ids_posts_autorises: set[str]
) -> list[CommentaireReddit]:
    """Normalise et anonymise les commentaires bruts d'un run d'approfondissement.

    Le rattachement au post parent se fait par le champ `postId` du
    commentaire, qui reprend l'identifiant complet du post (« t3_… »).

    Args:
        items: Items bruts du run, posts et commentaires mêlés.
        ids_posts_autorises: Identifiants des posts du corpus retenu ; les
            commentaires rattachés à un autre post sont écartés.

    Returns:
        Les commentaires normalisés.
    """
    _, bruts = separer_par_type(items)

    commentaires: list[CommentaireReddit] = []
    orphelins = 0
    for brut in bruts:
        identifiant = _texte_ou_none(brut.get(CLE_COMMENTAIRE_ID))
        id_post = _texte_ou_none(brut.get(CLE_COMMENTAIRE_ID_POST))
        texte = _texte_ou_none(brut.get(CLE_COMMENTAIRE_TEXTE))
        if not identifiant or not id_post or not texte:
            continue
        if id_post not in ids_posts_autorises:
            orphelins += 1
            continue

        commentaires.append(
            CommentaireReddit(
                id=identifiant,
                id_post=id_post,
                texte=texte,
                date_creation=_normaliser_date(brut.get(CLE_COMMENTAIRE_DATE)),
                score=_entier_ou_none(brut.get(CLE_COMMENTAIRE_SCORE)),
                profondeur=_entier_ou_none(brut.get(CLE_COMMENTAIRE_PROFONDEUR)),
                auteur_pseudonymise=pseudonymiser(brut.get(CLE_COMMENTAIRE_AUTEUR)),
            )
        )
    if orphelins:
        _LOG.warning("%s commentaire(s) rattaché(s) à un post hors corpus écarté(s).", orphelins)
    return commentaires


def calculer_stats(
    posts_collectes: list[PostReddit],
    posts_retenus: list[PostReddit],
    ids_approfondis: set[str],
    commentaires: list[CommentaireReddit],
) -> StatsCorpus:
    """Calcule les statistiques descriptives du corpus.

    Les répartitions portent sur les posts retenus, seul corpus effectivement
    livré ; le décompte avant filtrage reste disponible dans
    `nb_posts_collectes`.

    Args:
        posts_collectes: Posts dédoublonnés, avant filtrage de pertinence.
        posts_retenus: Posts après filtrage.
        ids_approfondis: Identifiants des posts soumis à la phase B.
        commentaires: Commentaires collectés.

    Returns:
        Les statistiques du corpus.
    """
    dates = sorted(
        date
        for date in (
            [post.date_creation for post in posts_retenus]
            + [commentaire.date_creation for commentaire in commentaires]
        )
        if date
    )
    return StatsCorpus(
        nb_posts_collectes=len(posts_collectes),
        nb_posts_retenus=len(posts_retenus),
        nb_posts_approfondis=len(ids_approfondis),
        nb_commentaires=len(commentaires),
        repartition_par_subreddit=dict(
            Counter(post.subreddit for post in posts_retenus).most_common()
        ),
        repartition_par_portee=dict(
            Counter(post.portee for post in posts_retenus).most_common()
        ),
        date_plus_ancienne=dates[0] if dates else None,
        date_plus_recente=dates[-1] if dates else None,
    )
