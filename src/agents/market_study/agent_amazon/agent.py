"""Orchestration de bout en bout de la collecte Amazon.

Séquence : résolution de la région en marketplace → contrôle de la fiche → plan
de recherches → runs Apify (avec relance des recherches restées vides) → filtres
déterministes → classification LLM → contrôle de volume (avec au plus un cycle
de repli) → collecte des avis sur les meilleurs produits → statistiques →
résultat.

La résolution de la région passe en premier et fait office de garde : si le pays
étudié n'a pas son propre site Amazon, l'agent s'arrête là, sans dépenser ni run
Apify ni appel LLM, et renvoie un résultat `region_couverte=False`.

Aucune exception n'est propagée : tout échec est converti en statut de collecte
et en limite explicite.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from amazon_source import TYPE_RUN_PRODUITS, collecter_avis, collecter_produits
from config import (
    HYPOTHESE_ASSIMILATION_RECHERCHES,
    HYPOTHESE_MARKETPLACE,
    HYPOTHESE_SEUILS,
    LIMITE_AUCUNE_DONNEE,
    LIMITE_AVIS_INDISPONIBLES,
    LIMITE_BLOCAGE_AMAZON,
    LIMITE_COLLECTE_PARTIELLE,
    LIMITE_CORPUS_INSUFFISANT,
    LIMITE_CORPUS_NON_CLASSIFIE,
    LIMITE_CORPUS_PARTIELLEMENT_CLASSIFIE,
    LIMITE_PLAN_INCOMPLET,
    LIMITE_REGION_NON_COUVERTE,
    LIMITES_METHODOLOGIQUES,
    MOTIF_ABSENCE_LIVRAISON,
    NB_PRODUITS_AVIS,
    PARALLELISME_MAX,
    PAUSE_AVANT_REPLI_SECS,
    SEUIL_MIN_PRODUITS,
    SEUIL_PERTINENCE,
    obtenir_logger,
)
from filtering import (
    CompteursFiltrage,
    appliquer_seuil_pertinence,
    classifier_produits,
    filtrer_deterministe,
)
from normalize import calculer_stats, compter_erreurs, normaliser_avis, normaliser_produits
from schemas import (
    AlerteQualiteInput,
    FicheProduit,
    Marketplace,
    ParametresMarche,
    ProduitAmazon,
    RecherchePlanifiee,
    ResultatRechercheAmazon,
    StatsCollecte,
    StatutCollecte,
)
from strategy import (
    controler_fiche_produit,
    generer_plan_recherches,
    generer_recherches_repli,
    relancer_sans_filtres,
    resoudre_marketplace,
)

_LOG = obtenir_logger(__name__)


class _Compteurs:
    """Cumul des décomptes sur l'ensemble des cycles de collecte."""

    def __init__(self) -> None:
        """Initialise tous les compteurs à zéro."""
        self.collectes = 0
        self.doublons = 0
        self.hors_criteres = 0
        self.sous_seuil = 0
        self.non_classifies = 0
        self.erreurs_actor = 0

    def ajouter_filtrage(self, compteurs: CompteursFiltrage) -> None:
        """Cumule les décomptes d'un passage de filtres déterministes.

        Args:
            compteurs: Décomptes du passage.
        """
        self.doublons += compteurs.doublons
        self.hors_criteres += compteurs.hors_criteres


# --------------------------------------------------------------------------- #
# Collecte
# --------------------------------------------------------------------------- #


def _executer_recherches(
    recherches: list[RecherchePlanifiee],
    marketplace: Marketplace,
    compteurs: _Compteurs,
) -> tuple[list[tuple[RecherchePlanifiee, list[ProduitAmazon]]], list[StatutCollecte]]:
    """Exécute un ensemble de recherches, une par run, en parallélisme borné.

    Args:
        recherches: Recherches à exécuter.
        marketplace: Marketplace interrogée.
        compteurs: Cumul des décomptes, **modifié en place**.

    Returns:
        Un couple `(produits_par_recherche, statuts)`. Les produits sont
        normalisés mais ni dédoublonnés ni filtrés.
    """
    if not recherches:
        return [], []

    with ThreadPoolExecutor(max_workers=max(1, PARALLELISME_MAX)) as executeur:
        resultats = list(
            executeur.map(
                lambda recherche: collecter_produits(recherche, marketplace), recherches
            )
        )

    par_recherche: list[tuple[RecherchePlanifiee, list[ProduitAmazon]]] = []
    statuts: list[StatutCollecte] = []

    for recherche, (items, statut) in zip(recherches, resultats):
        statuts.append(statut)
        compteurs.erreurs_actor += compter_erreurs(items)
        produits = normaliser_produits(items, recherche)
        compteurs.collectes += len(produits)
        par_recherche.append((recherche, produits))

    return par_recherche, statuts


def _relancer_recherches_vides(
    par_recherche: list[tuple[RecherchePlanifiee, list[ProduitAmazon]]],
    marketplace: Marketplace,
    compteurs: _Compteurs,
) -> tuple[list[tuple[RecherchePlanifiee, list[ProduitAmazon]]], list[StatutCollecte]]:
    """Rejoue une fois les recherches n'ayant remonté aucun produit.

    Une recherche vide vient le plus souvent d'une page bloquée par l'anti-bot,
    parfois d'une facette de prix que la marketplace n'a pas acceptée. La
    relance répond aux deux cas : elle attend d'abord, pour ne pas réutiliser la
    session proxy qui vient d'être refusée, puis rejoue l'URL débarrassée de sa
    facette — les critères de prix restent appliqués côté Python.

    Args:
        par_recherche: Résultat du premier passage.
        marketplace: Marketplace interrogée.
        compteurs: Cumul des décomptes, **modifié en place**.

    Returns:
        Un couple `(produits_par_recherche, statuts)` pour les seules relances.
    """
    vides = [recherche for recherche, produits in par_recherche if not produits]
    if not vides:
        return [], []

    _LOG.warning(
        "%s recherche(s) sans produit — relance dans %s s, sans les filtres d'URL.",
        len(vides),
        PAUSE_AVANT_REPLI_SECS,
    )
    time.sleep(PAUSE_AVANT_REPLI_SECS)
    return _executer_recherches(
        [relancer_sans_filtres(recherche) for recherche in vides], marketplace, compteurs
    )


def _collecter_et_qualifier(
    recherches: list[RecherchePlanifiee],
    marketplace: Marketplace,
    produit_reference: FicheProduit,
    cles_vues: set[str],
    compteurs: _Compteurs,
) -> tuple[list[ProduitAmazon], list[StatutCollecte], list[RecherchePlanifiee]]:
    """Exécute un cycle complet : collecte, relance, filtres, classification.

    Args:
        recherches: Recherches du cycle.
        marketplace: Marketplace interrogée.
        produit_reference: Fiche produit étudiée.
        cles_vues: Clés de produits déjà retenues, **modifié en place** pour que
            les cycles successifs ne se recouvrent pas.
        compteurs: Cumul des décomptes, **modifié en place**.

    Returns:
        Un triplet `(produits_retenus, statuts, recherches_executees)`. Les
        recherches exécutées incluent les relances, dont l'URL diffère.
    """
    par_recherche, statuts = _executer_recherches(recherches, marketplace, compteurs)

    relances, statuts_relance = _relancer_recherches_vides(
        par_recherche, marketplace, compteurs
    )
    par_recherche.extend(relances)
    statuts.extend(statuts_relance)

    retenus: list[ProduitAmazon] = []
    for recherche, produits in par_recherche:
        filtres, decomptes = filtrer_deterministe(produits, recherche, cles_vues)
        compteurs.ajouter_filtrage(decomptes)
        retenus.extend(filtres)

    retenus, non_classifies = classifier_produits(retenus, produit_reference)
    compteurs.non_classifies += non_classifies

    retenus, ecartes = appliquer_seuil_pertinence(retenus)
    compteurs.sous_seuil += ecartes

    executees = [recherche for recherche, _ in par_recherche]
    return retenus, statuts, executees


# --------------------------------------------------------------------------- #
# Avis
# --------------------------------------------------------------------------- #


def _ordonner(produits: list[ProduitAmazon]) -> list[ProduitAmazon]:
    """Classe le corpus par pertinence décroissante, puis par rang de collecte.

    Le rang de collecte départage les ex æquo : c'est l'ordre dans lequel Amazon
    a servi les produits pour le tri demandé.

    Args:
        produits: Corpus qualifié.

    Returns:
        Le corpus ordonné.
    """
    return sorted(
        produits,
        key=lambda produit: (-(produit.pertinence or 0.0), produit.rang_collecte),
    )


def _collecter_avis(
    produits: list[ProduitAmazon], nb_produits: int
) -> list[StatutCollecte]:
    """Enrichit d'avis les premiers produits du corpus, **en place**.

    Un run d'actor PAR produit : ce nombre est le principal levier de coût du
    module, d'où l'enrichissement des seuls produits de tête.

    Args:
        produits: Corpus ordonné.
        nb_produits: Nombre de produits à enrichir, 0 pour ne rien collecter.

    Returns:
        Les statuts des runs d'avis.
    """
    cibles = [produit for produit in produits[:nb_produits] if produit.url]
    if not cibles:
        return []

    _LOG.info("Collecte des avis sur %s produit(s).", len(cibles))
    with ThreadPoolExecutor(max_workers=max(1, PARALLELISME_MAX)) as executeur:
        resultats = list(executeur.map(lambda produit: collecter_avis(produit.url), cibles))

    statuts: list[StatutCollecte] = []
    for produit, (items, statut) in zip(cibles, resultats):
        produit.avis = normaliser_avis(items)
        statuts.append(statut)
    return statuts


# --------------------------------------------------------------------------- #
# Limites et hypothèses
# --------------------------------------------------------------------------- #


def _construire_limites(
    statuts: list[StatutCollecte],
    produits: list[ProduitAmazon],
    compteurs: _Compteurs,
    plan_complet: bool,
) -> list[str]:
    """Assemble les limites méthodologiques et conjoncturelles du résultat.

    Args:
        statuts: Comptes rendus de tous les runs exécutés.
        produits: Corpus final.
        compteurs: Décomptes cumulés du filtrage et de la classification.
        plan_complet: Vrai si le plan a atteint le nombre de recherches visé.

    Returns:
        La liste des limites à joindre au résultat.
    """
    limites = list(LIMITES_METHODOLOGIQUES)

    if not plan_complet:
        limites.append(LIMITE_PLAN_INCOMPLET)

    runs_produits = [statut for statut in statuts if statut.type_run == TYPE_RUN_PRODUITS]
    echecs = [statut for statut in runs_produits if not statut.succes]
    if runs_produits and len(echecs) == len(runs_produits):
        limites.append(LIMITE_AUCUNE_DONNEE)
    elif echecs:
        limites.append(LIMITE_COLLECTE_PARTIELLE)

    if compteurs.erreurs_actor:
        limites.append(LIMITE_BLOCAGE_AMAZON)

    if produits and compteurs.non_classifies >= len(produits):
        limites.append(LIMITE_CORPUS_NON_CLASSIFIE)
    elif compteurs.non_classifies:
        limites.append(LIMITE_CORPUS_PARTIELLEMENT_CLASSIFIE)

    if produits and len(produits) < SEUIL_MIN_PRODUITS:
        limites.append(LIMITE_CORPUS_INSUFFISANT)

    if produits and not any(produit.avis for produit in produits):
        limites.append(LIMITE_AVIS_INDISPONIBLES)

    return limites


def _construire_hypotheses(
    plan: list[RecherchePlanifiee],
    marche: ParametresMarche,
    marketplace: Marketplace,
    nb_produits_avis: int,
) -> list[str]:
    """Assemble les hypothèses sous-jacentes au corpus livré.

    Args:
        plan: Recherches effectivement exécutées.
        marche: Région d'étude.
        marketplace: Marketplace interrogée.
        nb_produits_avis: Nombre de produits enrichis d'avis.

    Returns:
        La liste des hypothèses à joindre au résultat.
    """
    marketplace_appliquee = (
        f"{HYPOTHESE_MARKETPLACE} Appliqué ici : {marche.geo} → "
        f"{marketplace.domaine}. {marketplace.explication}"
    )

    assimilation = HYPOTHESE_ASSIMILATION_RECHERCHES
    intentions = [
        f"« {recherche.mots_cles} » : {recherche.justification}"
        for recherche in plan
        if recherche.justification
    ]
    if intentions:
        assimilation = f"{assimilation} Angles retenus — " + " ; ".join(intentions)

    seuils = HYPOTHESE_SEUILS.format(
        seuil_pertinence=SEUIL_PERTINENCE,
        seuil_produits=SEUIL_MIN_PRODUITS,
        nb_produits_avis=nb_produits_avis,
    )
    return [marketplace_appliquee, MOTIF_ABSENCE_LIVRAISON, assimilation, seuils]


def _resultat_sans_donnees(
    produit: FicheProduit,
    marche: ParametresMarche,
    marketplace: Marketplace,
    alertes: list[AlerteQualiteInput],
    message: str,
    nb_produits_avis: int,
) -> ResultatRechercheAmazon:
    """Construit le résultat d'une exécution n'ayant produit aucun corpus.

    Args:
        produit: Fiche produit étudiée.
        marche: Région d'étude.
        marketplace: Marketplace retenue.
        alertes: Alertes du contrôle qualité de la fiche.
        message: Cause de l'absence de données.
        nb_produits_avis: Nombre de produits qui auraient été enrichis d'avis.

    Returns:
        Un résultat exploitable, `donnees_disponibles=False`.
    """
    return ResultatRechercheAmazon(
        produit=produit,
        marche=marche,
        region_couverte=True,
        marketplace=marketplace,
        alertes_qualite_input=alertes,
        plan_recherches=[],
        produits=[],
        stats=StatsCollecte(nb_produits_collectes=0, nb_produits_retenus=0),
        statuts_collecte=[
            StatutCollecte(
                recherche="—",
                type_run=TYPE_RUN_PRODUITS,
                succes=False,
                message_erreur=message,
                nb_items=0,
                nb_tentatives=0,
            )
        ],
        donnees_disponibles=False,
        limites=[*LIMITES_METHODOLOGIQUES, LIMITE_AUCUNE_DONNEE],
        hypotheses=_construire_hypotheses([], marche, marketplace, nb_produits_avis),
    )


def _resultat_region_non_couverte(
    produit: FicheProduit, marche: ParametresMarche, explication: str
) -> ResultatRechercheAmazon:
    """Construit le résultat d'une région à laquelle l'agent ne s'applique pas.

    Aucun run n'est lancé et aucune hypothèse n'est formulée : il n'y a pas de
    corpus sur lequel raisonner. Les limites méthodologiques habituelles sont
    elles aussi omises — elles décriraient un corpus qui n'existe pas.

    Args:
        produit: Fiche produit étudiée.
        marche: Région d'étude.
        explication: Motif précis du refus, tel que produit par `strategy`.

    Returns:
        Un résultat exploitable, `region_couverte=False`.
    """
    return ResultatRechercheAmazon(
        produit=produit,
        marche=marche,
        region_couverte=False,
        marketplace=None,
        alertes_qualite_input=[],
        plan_recherches=[],
        produits=[],
        stats=StatsCollecte(nb_produits_collectes=0, nb_produits_retenus=0),
        statuts_collecte=[
            StatutCollecte(
                recherche="—",
                type_run=TYPE_RUN_PRODUITS,
                succes=False,
                message_erreur=explication,
                nb_items=0,
                nb_tentatives=0,
            )
        ],
        donnees_disponibles=False,
        limites=[LIMITE_REGION_NON_COUVERTE, explication],
        hypotheses=[],
    )


# --------------------------------------------------------------------------- #
# Point d'entrée du module
# --------------------------------------------------------------------------- #


def rechercher_amazon(
    produit: FicheProduit,
    marche: ParametresMarche,
    domaine_force: str | None = None,
    nb_produits_avis: int = NB_PRODUITS_AVIS,
) -> ResultatRechercheAmazon:
    """Collecte et qualifie un corpus de produits Amazon pour une région.

    **L'agent ne s'applique qu'aux pays où Amazon exploite son propre site.**
    Pour tout autre pays, la fonction s'arrête immédiatement et renvoie un
    résultat `region_couverte=False`, sans lancer le moindre run : se rabattre
    sur une marketplace voisine livrerait le marché d'un autre pays sous
    l'étiquette de celui qui est étudié.

    Le pays retenu sélectionne UNIQUEMENT la marketplace interrogée : aucune
    adresse de livraison n'accompagne la collecte, de sorte que le corpus est le
    catalogue complet de ce site, dans sa propre devise.

    Cette fonction ne lève jamais d'exception : un échec total de la collecte
    retourne un résultat exploitable, avec `donnees_disponibles=False` et le
    détail des statuts de chaque run.

    Args:
        produit: Fiche produit à étudier.
        marche: Région d'étude et langue du marché.
        domaine_force: Marketplace imposée (« amazon.de »). Décision
            d'opérateur : elle court-circuite le contrôle de couverture du pays.
        nb_produits_avis: Produits de tête enrichis d'avis, 0 pour ne collecter
            aucun avis. Un run d'actor par produit.

    Returns:
        Le corpus qualifié, ses statistiques, ses statuts de collecte, ses
        limites et ses hypothèses.
    """
    # Le contrôle de couverture passe AVANT tout le reste : sur une région non
    # couverte, il ne doit être dépensé ni un run Apify, ni un appel LLM.
    marketplace, explication = resoudre_marketplace(marche.geo, domaine_force)
    if marketplace is None:
        return _resultat_region_non_couverte(produit, marche, explication)
    _LOG.info("Marketplace retenue : %s", explication)

    alertes = controler_fiche_produit(produit, marche)

    plan, plan_complet = generer_plan_recherches(produit, marche, marketplace)
    if not plan:
        _LOG.error("Collecte abandonnée : aucun plan de recherches exploitable.")
        return _resultat_sans_donnees(
            produit,
            marche,
            marketplace,
            alertes,
            "Génération du plan de recherches impossible : aucune recherche exploitable.",
            nb_produits_avis,
        )

    compteurs = _Compteurs()
    cles_vues: set[str] = set()

    produits, statuts, executees = _collecter_et_qualifier(
        plan, marketplace, produit, cles_vues, compteurs
    )
    plan = executees

    if len(produits) < SEUIL_MIN_PRODUITS:
        _LOG.warning(
            "Corpus sous le seuil (%s < %s) — cycle de repli déclenché.",
            len(produits),
            SEUIL_MIN_PRODUITS,
        )
        recherches_repli = generer_recherches_repli(
            produit, marche, marketplace, [recherche.mots_cles for recherche in plan]
        )
        if recherches_repli:
            produits_repli, statuts_repli, executees_repli = _collecter_et_qualifier(
                recherches_repli, marketplace, produit, cles_vues, compteurs
            )
            produits.extend(produits_repli)
            statuts.extend(statuts_repli)
            plan.extend(executees_repli)
        else:
            _LOG.warning("Aucune recherche de repli exploitable : corpus inchangé.")

    # Un corpus encore trop court après le cycle de repli est livré tel quel :
    # aucun second cycle n'est déclenché, sous aucune condition.
    produits = _ordonner(produits)
    statuts.extend(_collecter_avis(produits, nb_produits_avis))

    stats = calculer_stats(
        nb_produits_collectes=compteurs.collectes,
        produits_retenus=produits,
        nb_doublons_ecartes=compteurs.doublons,
        nb_produits_hors_criteres=compteurs.hors_criteres,
        nb_produits_sous_seuil=compteurs.sous_seuil,
        nb_produits_non_classifies=compteurs.non_classifies,
        nb_enregistrements_erreur=compteurs.erreurs_actor,
    )

    _LOG.info(
        "Collecte terminée sur %s : %s produit(s) retenu(s) sur %s collecté(s), "
        "%s run(s) exécuté(s), %s avis.",
        marketplace.domaine,
        stats.nb_produits_retenus,
        stats.nb_produits_collectes,
        len(statuts),
        stats.nb_avis_collectes,
    )

    return ResultatRechercheAmazon(
        produit=produit,
        marche=marche,
        region_couverte=True,
        marketplace=marketplace,
        alertes_qualite_input=alertes,
        plan_recherches=plan,
        produits=produits,
        stats=stats,
        statuts_collecte=statuts,
        donnees_disponibles=bool(produits),
        limites=_construire_limites(statuts, produits, compteurs, plan_complet),
        hypotheses=_construire_hypotheses(plan, marche, marketplace, nb_produits_avis),
    )
