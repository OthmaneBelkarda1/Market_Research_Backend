"""Orchestration de bout en bout de la collecte Reddit.

La collecte est découpée en deux phases, par contrainte de coût : les
commentaires sont facturés à l'item, on ne les collecte donc que pour les posts
ayant survécu au filtrage de pertinence.

Les runs Apify sont exécutés **séquentiellement** : au plus
`1 + NB_MAX_SUBREDDITS_CIBLES + 1` runs par exécution. Le volume ne justifie
aucun parallélisme, chaque run facture des frais de démarrage, et la lisibilité
des statuts prime sur la latence.

Aucune exception n'est propagée : tout échec est converti en statut de collecte
et en limite explicite.
"""

from __future__ import annotations

from config import (
    HYPOTHESE_ASSIMILATION_REQUETES,
    HYPOTHESE_PORTEE,
    HYPOTHESE_SUBREDDITS_NON_VERIFIES,
    LIMITE_ANCRAGE_REGIONAL_FAIBLE,
    LIMITE_AUCUNE_DONNEE,
    LIMITE_CORPUS_NON_FILTRE,
    LIMITE_CORPUS_PARTIELLEMENT_FILTRE,
    LIMITE_POSTS_SANS_COMMENTAIRES_ECARTES,
    LIMITE_PROSPECTION_PARTIELLE,
    LIMITE_SANS_ANCRAGE_REGIONAL,
    LIMITE_SANS_COMMENTAIRES,
    LIMITES_METHODOLOGIQUES,
    NB_MAX_COMMENTAIRES_PAR_POST,
    NB_MAX_POSTS_APPROFONDIS,
    NB_MAX_POSTS_PAR_SUBREDDIT,
    NB_MAX_POSTS_RECHERCHE_GLOBALE,
    NB_MAX_REQUETES,
    NB_MAX_SUBREDDITS_CIBLES,
    ORIGINE_RECHERCHE_GLOBALE,
    ORIGINE_SUBREDDIT_CIBLE,
    PHASE_COMMENTAIRES,
    PHASE_PROSPECTION_GLOBALE,
    PORTEE_REGIONALE,
    PREFIXE_SUBREDDIT,
    obtenir_logger,
)
from normalize import calculer_stats, normaliser_commentaires, normaliser_posts
from reddit_source import collecter_commentaires, rechercher_posts
from relevance import dedoublonner, filtrer_par_pertinence
from schemas import (
    CommentaireReddit,
    FicheProduit,
    ParametresMarche,
    PostReddit,
    ResultatCollecteReddit,
    StatsCorpus,
    StatutCollecte,
    StrategieRecherche,
)
from strategy import controler_fiche_produit, deriver_strategie

_LOG = obtenir_logger(__name__)

_STATS_VIDES = StatsCorpus(
    nb_posts_collectes=0,
    nb_posts_retenus=0,
    nb_posts_approfondis=0,
    nb_commentaires=0,
)


def _selectionner_requetes(strategie: StrategieRecherche) -> list[str]:
    """Assemble les requêtes effectivement soumises, dans le plafond de coût.

    Les requêtes en langue du marché sont prioritaires : elles portent le
    corpus régional, que les requêtes anglaises ne font que compléter.

    Les deux listes sont dédoublonnées ensemble. Sur un marché anglophone, la
    chaîne produit fréquemment les mêmes formulations des deux côtés : sans ce
    filtre, la moitié du plafond de collecte partirait en requêtes identiques,
    facturées deux fois pour les mêmes posts.

    Args:
        strategie: Stratégie dérivée par le LLM.

    Returns:
        Au plus `NB_MAX_REQUETES` requêtes uniques, marché puis anglais.
    """
    vues: set[str] = set()
    uniques: list[str] = []
    for requete in strategie.requetes_marche + strategie.requetes_globales:
        propre = requete.strip()
        cle = propre.casefold()
        if not propre or cle in vues:
            continue
        vues.add(cle)
        uniques.append(propre)

    doublons = len(strategie.requetes_marche) + len(strategie.requetes_globales) - len(uniques)
    if doublons:
        _LOG.warning(
            "%s requête(s) en doublon entre listes marché et globale — écartée(s).",
            doublons,
        )

    retenues = uniques[:NB_MAX_REQUETES]
    if len(uniques) > len(retenues):
        _LOG.info(
            "Requêtes tronquées au plafond : %s unique(s) → %s retenue(s).",
            len(uniques),
            len(retenues),
        )
    return retenues


def _selectionner_subreddits(strategie: StrategieRecherche) -> list[str]:
    """Assemble les subreddits effectivement interrogés, dans le plafond de coût.

    Les subreddits régionaux passent avant les thématiques : le corpus régional
    est le plus difficile à obtenir et le plus fragile.

    Args:
        strategie: Stratégie dérivée par le LLM.

    Returns:
        Au plus `NB_MAX_SUBREDDITS_CIBLES` subreddits, sans doublon.
    """
    vus: set[str] = set()
    retenus: list[str] = []
    for nom in strategie.subreddits_regionaux + strategie.subreddits_thematiques:
        cle = nom.strip().casefold().removeprefix(PREFIXE_SUBREDDIT).strip("/ ")
        if not cle or cle in vus:
            continue
        vus.add(cle)
        retenus.append(nom.strip())
        if len(retenus) == NB_MAX_SUBREDDITS_CIBLES:
            break
    return retenus


def _prospecter(
    strategie: StrategieRecherche,
    marche: ParametresMarche,
    requetes: list[str],
    subreddits: list[str],
) -> tuple[list[PostReddit], list[StatutCollecte]]:
    """Exécute la phase A : recherche globale puis recherches par subreddit.

    Args:
        strategie: Stratégie dérivée, pour l'attribution de la portée.
        marche: Marché ciblé.
        requetes: Requêtes retenues, marché puis anglais.
        subreddits: Subreddits cibles retenus.

    Returns:
        Un couple `(posts, statuts)` : les posts normalisés de tous les runs,
        doublons compris, et le compte rendu de chaque run.
    """
    posts: list[PostReddit] = []
    statuts: list[StatutCollecte] = []

    items, statut = rechercher_posts(requetes, None, NB_MAX_POSTS_RECHERCHE_GLOBALE)
    statuts.append(statut)
    posts.extend(
        normaliser_posts(
            items,
            ORIGINE_RECHERCHE_GLOBALE,
            strategie.subreddits_regionaux,
            strategie.requetes_marche,
            marche.langue,
        )
    )

    # Les runs restreints privilégient les requêtes en langue du marché ; sans
    # elles, la recherche resterait vide dans un subreddit régional.
    requetes_restreintes = strategie.requetes_marche[:NB_MAX_REQUETES] or requetes
    for subreddit in subreddits:
        items, statut = rechercher_posts(
            requetes_restreintes, subreddit, NB_MAX_POSTS_PAR_SUBREDDIT
        )
        statuts.append(statut)
        posts.extend(
            normaliser_posts(
                items,
                ORIGINE_SUBREDDIT_CIBLE,
                strategie.subreddits_regionaux,
                strategie.requetes_marche,
                marche.langue,
            )
        )

    return posts, statuts


def _selectionner_posts_approfondis(posts: list[PostReddit]) -> list[PostReddit]:
    """Sélectionne les posts dont les fils de commentaires seront collectés.

    Le classement combine la pertinence et le volume de commentaires : un post
    très pertinent mais sans discussion n'apporte aucun avis consommateur. Les
    posts annonçant zéro commentaire sont écartés — les interroger coûterait un
    item facturé pour un rendement nul.

    Args:
        posts: Posts retenus après filtrage de pertinence.

    Returns:
        Au plus `NB_MAX_POSTS_APPROFONDIS` posts, les plus prometteurs d'abord.
    """
    candidats = [post for post in posts if (post.nb_commentaires or 0) > 0]
    candidats.sort(
        key=lambda post: (post.pertinence or 0.0, post.nb_commentaires or 0),
        reverse=True,
    )
    return candidats[:NB_MAX_POSTS_APPROFONDIS]


def _approfondir(
    posts_retenus: list[PostReddit],
) -> tuple[list[CommentaireReddit], StatutCollecte | None, set[str], bool]:
    """Exécute la phase B : collecte des commentaires des posts sélectionnés.

    Args:
        posts_retenus: Posts survivants du filtrage de pertinence.

    Returns:
        Un quadruplet `(commentaires, statut, ids_approfondis, posts_ecartes)`.
        `statut` vaut `None` si aucun post n'était éligible — aucun run n'a
        alors été lancé. `posts_ecartes` signale que des posts sans commentaire
        ont été exclus de la sélection.
    """
    selection = _selectionner_posts_approfondis(posts_retenus)
    ecartes = len(selection) < len(posts_retenus)

    if not selection:
        _LOG.warning("Phase B non lancée : aucun post éligible à l'approfondissement.")
        return [], None, set(), ecartes

    ids = {post.id for post in selection}
    items, statut = collecter_commentaires(
        [post.url for post in selection], NB_MAX_COMMENTAIRES_PAR_POST
    )
    commentaires = normaliser_commentaires(items, ids)
    _LOG.info(
        "Phase B : %s post(s) approfondi(s) → %s commentaire(s).",
        len(selection),
        len(commentaires),
    )
    return commentaires, statut, ids, ecartes


def _construire_limites(
    statuts: list[StatutCollecte],
    nb_posts_dedoublonnes: int,
    nb_non_scores: int,
    nb_commentaires: int,
    posts_ecartes_phase_b: bool,
    subreddits_regionaux: list[str],
    nb_posts_regionaux: int,
) -> list[str]:
    """Assemble les limites méthodologiques et conjoncturelles du résultat.

    Args:
        statuts: Comptes rendus de tous les runs exécutés.
        nb_posts_dedoublonnes: Posts uniques collectés, avant filtrage.
        nb_non_scores: Posts conservés sans score de pertinence.
        nb_commentaires: Commentaires effectivement collectés.
        posts_ecartes_phase_b: Vrai si des posts sans commentaire ont été exclus
            de la phase B.
        subreddits_regionaux: Subreddits régionaux effectivement ciblés.
        nb_posts_regionaux: Posts retenus de portée régionale.

    Returns:
        La liste des limites à joindre au résultat.
    """
    limites = list(LIMITES_METHODOLOGIQUES)

    if not subreddits_regionaux:
        limites.append(LIMITE_SANS_ANCRAGE_REGIONAL)
    elif nb_posts_dedoublonnes and not nb_posts_regionaux:
        limites.append(LIMITE_ANCRAGE_REGIONAL_FAIBLE)

    statuts_prospection = [
        statut for statut in statuts if statut.phase != PHASE_COMMENTAIRES
    ]
    echecs = [statut for statut in statuts_prospection if not statut.succes]
    if echecs and len(echecs) == len(statuts_prospection):
        limites.append(LIMITE_AUCUNE_DONNEE)
    elif echecs:
        limites.append(LIMITE_PROSPECTION_PARTIELLE)

    if nb_posts_dedoublonnes and nb_non_scores == nb_posts_dedoublonnes:
        limites.append(LIMITE_CORPUS_NON_FILTRE)
    elif nb_non_scores:
        limites.append(LIMITE_CORPUS_PARTIELLEMENT_FILTRE)

    if nb_posts_dedoublonnes and not nb_commentaires:
        limites.append(LIMITE_SANS_COMMENTAIRES)
    if posts_ecartes_phase_b:
        limites.append(LIMITE_POSTS_SANS_COMMENTAIRES_ECARTES)

    return limites


def _construire_hypotheses(
    strategie: StrategieRecherche, subreddits_cibles: list[str]
) -> list[str]:
    """Assemble les hypothèses sous-jacentes au corpus livré.

    Args:
        strategie: Stratégie effectivement appliquée.
        subreddits_cibles: Subreddits réellement interrogés.

    Returns:
        La liste des hypothèses à joindre au résultat.
    """
    assimilation = HYPOTHESE_ASSIMILATION_REQUETES
    if strategie.justification:
        assimilation = f"{assimilation} Justification de la stratégie : {strategie.justification}"

    subreddits = HYPOTHESE_SUBREDDITS_NON_VERIFIES
    if subreddits_cibles:
        subreddits = f"{subreddits} Subreddits interrogés : {', '.join(subreddits_cibles)}."

    return [assimilation, subreddits, HYPOTHESE_PORTEE]


def collecter_reddit(
    produit: FicheProduit,
    marche: ParametresMarche,
) -> ResultatCollecteReddit:
    """Collecte et qualifie un corpus de discussions Reddit pour un produit.

    Cette fonction ne lève jamais d'exception : un échec total de la collecte
    retourne un résultat exploitable, avec `donnees_disponibles=False` et le
    détail des statuts de chaque run.

    Args:
        produit: Fiche produit à étudier.
        marche: Région et langue de l'étude.

    Returns:
        Le corpus qualifié, ses statistiques, ses statuts de collecte, ses
        limites et ses hypothèses.
    """
    alertes = controler_fiche_produit(produit, marche)

    try:
        strategie = deriver_strategie(produit, marche)
    except RuntimeError as exception:
        _LOG.error("Collecte abandonnée : %s", exception)
        return ResultatCollecteReddit(
            produit=produit,
            marche=marche,
            alertes_qualite_input=alertes,
            strategie=StrategieRecherche(justification=str(exception)),
            posts=[],
            commentaires=[],
            stats=_STATS_VIDES.model_copy(),
            statuts_collecte=[
                StatutCollecte(
                    phase=PHASE_PROSPECTION_GLOBALE,
                    cible="—",
                    succes=False,
                    message_erreur=str(exception),
                    nb_items=0,
                    nb_tentatives=0,
                )
            ],
            donnees_disponibles=False,
            limites=[*LIMITES_METHODOLOGIQUES, LIMITE_AUCUNE_DONNEE],
            hypotheses=_construire_hypotheses(StrategieRecherche(), []),
        )

    requetes = _selectionner_requetes(strategie)
    subreddits_cibles = _selectionner_subreddits(strategie)

    posts_bruts, statuts = _prospecter(strategie, marche, requetes, subreddits_cibles)
    posts_dedoublonnes = dedoublonner(posts_bruts)
    posts_retenus, nb_non_scores = filtrer_par_pertinence(posts_dedoublonnes, produit)

    commentaires, statut_phase_b, ids_approfondis, posts_ecartes = _approfondir(posts_retenus)
    if statut_phase_b is not None:
        statuts.append(statut_phase_b)

    stats = calculer_stats(posts_dedoublonnes, posts_retenus, ids_approfondis, commentaires)
    limites = _construire_limites(
        statuts,
        len(posts_dedoublonnes),
        nb_non_scores,
        len(commentaires),
        posts_ecartes,
        strategie.subreddits_regionaux,
        sum(1 for post in posts_retenus if post.portee == PORTEE_REGIONALE),
    )

    _LOG.info(
        "Collecte terminée : %s post(s) retenu(s), %s commentaire(s), %s run(s) Apify.",
        len(posts_retenus),
        len(commentaires),
        len(statuts),
    )

    return ResultatCollecteReddit(
        produit=produit,
        marche=marche,
        alertes_qualite_input=alertes,
        strategie=strategie,
        posts=posts_retenus,
        commentaires=commentaires,
        stats=stats,
        statuts_collecte=statuts,
        donnees_disponibles=bool(posts_retenus),
        limites=limites,
        hypotheses=_construire_hypotheses(strategie, subreddits_cibles),
    )
