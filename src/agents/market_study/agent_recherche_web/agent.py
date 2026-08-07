"""Orchestration de bout en bout de la collecte de pages web.

Séquence : contrôle de la fiche → plan de requêtes → runs Apify → filtres
déterministes → classification LLM → contrôle de couverture (avec au plus un
cycle de repli par axe) → statistiques → résultat.

Aucune exception n'est propagée : tout échec est converti en statut de collecte
et en limite explicite.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from config import (
    AXES_ANALYSE,
    CIBLAGE_TLD,
    HYPOTHESE_ASSIMILATION_REQUETES,
    HYPOTHESE_MAPPING_TLD,
    HYPOTHESE_SEUILS,
    LIMITE_AUCUNE_DONNEE,
    LIMITE_AXES_SOUS_COUVERTS,
    LIMITE_COLLECTE_PARTIELLE,
    LIMITE_CORPUS_NON_CLASSIFIE,
    LIMITE_CORPUS_PARTIELLEMENT_CLASSIFIE,
    LIMITE_PLAN_INCOMPLET,
    LIMITE_RESULTATS_SUGGERES,
    LIMITE_TLD_PEU_FOURNI,
    LIMITES_METHODOLOGIQUES,
    PARALLELISME_MAX,
    SEUIL_MIN_PAGES_PAR_AXE,
    SEUIL_PERTINENCE,
    TYPE_RESULTAT_SUGGERE,
    obtenir_logger,
)
from filtering import (
    CompteursFiltrage,
    appliquer_seuil_pertinence,
    classifier_pages,
    filtrer_deterministe,
)
from normalize import calculer_stats, deriver_tld, normaliser_pages
from queries import (
    controler_fiche_produit,
    generer_plan_requetes,
    generer_requetes_repli,
)
from schemas import (
    AlerteQualiteInput,
    FicheProduit,
    PageWeb,
    ParametresMarche,
    RequetePlanifiee,
    ResultatRechercheWeb,
    StatsCouverture,
    StatutCollecte,
)
from web_source import rechercher_pages

_LOG = obtenir_logger(__name__)


class _Compteurs:
    """Cumul des décomptes de filtrage sur l'ensemble des cycles de collecte."""

    def __init__(self) -> None:
        """Initialise tous les compteurs à zéro."""
        self.collectees = 0
        self.doublons = 0
        self.domaines_exclus = 0
        self.trop_courtes = 0
        self.sous_seuil = 0
        self.non_classifiees = 0

    def ajouter_filtrage(self, compteurs: CompteursFiltrage) -> None:
        """Cumule les décomptes d'un passage de filtres déterministes.

        Args:
            compteurs: Décomptes du passage.
        """
        self.doublons += compteurs.doublons
        self.domaines_exclus += compteurs.domaines_exclus
        self.trop_courtes += compteurs.trop_courtes


def _executer_requetes(
    requetes: list[RequetePlanifiee],
) -> tuple[list[PageWeb], list[StatutCollecte], int]:
    """Exécute un ensemble de requêtes, une par run, en parallélisme borné.

    Chaque run passe par l'infrastructure SERP gérée d'Apify : contrairement à
    une collecte Google Trends, où les sessions concurrentes se font bloquer par
    l'anti-bot, un parallélisme modéré est ici sans risque. Ramener
    `PARALLELISME_MAX` à 1 rétablit une exécution strictement séquentielle.

    Args:
        requetes: Requêtes à exécuter.

    Returns:
        Un triplet `(pages, statuts, nb_items_bruts)`. Les pages sont
        normalisées mais ni dédoublonnées ni filtrées.
    """
    if not requetes:
        return [], [], 0

    pages: list[PageWeb] = []
    statuts: list[StatutCollecte] = []
    nb_items = 0

    with ThreadPoolExecutor(max_workers=max(1, PARALLELISME_MAX)) as executeur:
        resultats = list(executeur.map(rechercher_pages, requetes))

    for requete, (items, statut) in zip(requetes, resultats):
        statuts.append(statut)
        nb_items += len(items)
        pages.extend(normaliser_pages(items, requete))

    return pages, statuts, nb_items


def _collecter_et_qualifier(
    requetes: list[RequetePlanifiee],
    produit: FicheProduit,
    marche: ParametresMarche,
    urls_vues: set[str],
    compteurs: _Compteurs,
) -> tuple[list[PageWeb], list[StatutCollecte]]:
    """Exécute un cycle complet : collecte, filtres, classification, seuil.

    Args:
        requetes: Requêtes du cycle.
        produit: Fiche produit étudiée.
        marche: Région d'étude.
        urls_vues: URLs déjà retenues, **modifié en place** pour que les cycles
            successifs ne se recouvrent pas.
        compteurs: Cumul des décomptes, **modifié en place**.

    Returns:
        Un couple `(pages_retenues, statuts)`.
    """
    pages, statuts, nb_items = _executer_requetes(requetes)
    compteurs.collectees += nb_items

    pages, filtrage = filtrer_deterministe(pages, urls_vues)
    compteurs.ajouter_filtrage(filtrage)

    pages, non_classifiees = classifier_pages(pages, produit, marche)
    compteurs.non_classifiees += non_classifiees

    pages, ecartees = appliquer_seuil_pertinence(pages)
    compteurs.sous_seuil += ecartees

    return pages, statuts


def _axes_deficitaires(pages: list[PageWeb]) -> list[str]:
    """Identifie les axes dont la couverture reste sous le seuil.

    Args:
        pages: Corpus retenu à ce stade.

    Returns:
        Les axes sous-couverts, dans l'ordre de la nomenclature.
    """
    return [
        axe
        for axe in AXES_ANALYSE
        if sum(1 for page in pages if axe in page.axes_servis) < SEUIL_MIN_PAGES_PAR_AXE
    ]


def _executer_repli(
    axes: list[str],
    produit: FicheProduit,
    marche: ParametresMarche,
    tld: str,
    requetes_utilisees: list[str],
    urls_vues: set[str],
    compteurs: _Compteurs,
) -> tuple[list[PageWeb], list[StatutCollecte], list[RequetePlanifiee]]:
    """Exécute un unique cycle de repli pour les axes sous-couverts.

    Les requêtes de repli de tous les axes déficitaires sont générées d'abord,
    puis exécutées en un seul cycle : elles partagent ainsi le parallélisme et
    le dédoublonnage.

    Args:
        axes: Axes déficitaires.
        produit: Fiche produit étudiée.
        marche: Région d'étude.
        tld: TLD national imposé.
        requetes_utilisees: Textes des requêtes déjà exécutées, pour imposer un
            angle différent.
        urls_vues: URLs déjà retenues, **modifié en place**.
        compteurs: Cumul des décomptes, **modifié en place**.

    Returns:
        Un triplet `(pages, statuts, requetes_repli)`.
    """
    requetes: list[RequetePlanifiee] = []
    for axe in axes:
        _LOG.warning(
            "Axe %s sous-couvert (< %s pages) — cycle de repli déclenché.",
            axe,
            SEUIL_MIN_PAGES_PAR_AXE,
        )
        requetes.extend(
            generer_requetes_repli(axe, produit, marche, tld, requetes_utilisees)
        )

    if not requetes:
        _LOG.warning("Aucune requête de repli exploitable : couverture inchangée.")
        return [], [], []

    pages, statuts = _collecter_et_qualifier(
        requetes, produit, marche, urls_vues, compteurs
    )
    return pages, statuts, requetes


def _construire_limites(
    statuts: list[StatutCollecte],
    pages: list[PageWeb],
    compteurs: _Compteurs,
    plan_complet: bool,
    axes_sous_couverts: list[str],
    tld_sans_resultat: bool,
) -> list[str]:
    """Assemble les limites méthodologiques et conjoncturelles du résultat.

    Args:
        statuts: Comptes rendus de toutes les requêtes exécutées.
        pages: Corpus final.
        compteurs: Décomptes cumulés du filtrage et de la classification.
        plan_complet: Vrai si tous les quotas du plan ont été atteints.
        axes_sous_couverts: Axes encore déficitaires après repli.
        tld_sans_resultat: Vrai si une requête `tld` n'a rien renvoyé.

    Returns:
        La liste des limites à joindre au résultat.
    """
    limites = list(LIMITES_METHODOLOGIQUES)

    if not plan_complet:
        limites.append(LIMITE_PLAN_INCOMPLET)

    echecs = [statut for statut in statuts if not statut.succes]
    if statuts and len(echecs) == len(statuts):
        limites.append(LIMITE_AUCUNE_DONNEE)
    elif echecs:
        limites.append(LIMITE_COLLECTE_PARTIELLE)

    if tld_sans_resultat:
        limites.append(LIMITE_TLD_PEU_FOURNI)

    if pages and compteurs.non_classifiees == len(pages):
        limites.append(LIMITE_CORPUS_NON_CLASSIFIE)
    elif compteurs.non_classifiees:
        limites.append(LIMITE_CORPUS_PARTIELLEMENT_CLASSIFIE)

    if any(page.type_resultat_serp == TYPE_RESULTAT_SUGGERE for page in pages):
        limites.append(LIMITE_RESULTATS_SUGGERES)

    if axes_sous_couverts:
        limites.append(LIMITE_AXES_SOUS_COUVERTS)

    return limites


def _construire_hypotheses(
    plan: list[RequetePlanifiee], marche: ParametresMarche, tld: str
) -> list[str]:
    """Assemble les hypothèses sous-jacentes au corpus livré.

    Args:
        plan: Requêtes effectivement exécutées.
        marche: Région d'étude.
        tld: TLD national appliqué.

    Returns:
        La liste des hypothèses à joindre au résultat.
    """
    assimilation = HYPOTHESE_ASSIMILATION_REQUETES
    justifications = [
        f"« {requete.texte} » : {requete.justification}"
        for requete in plan
        if requete.justification
    ]
    if justifications:
        assimilation = f"{assimilation} Intentions retenues — " + " ; ".join(justifications)

    mapping = f"{HYPOTHESE_MAPPING_TLD} Appliqué ici : {marche.geo} → .{tld}."

    seuils = HYPOTHESE_SEUILS.format(
        seuil_pertinence=SEUIL_PERTINENCE, seuil_pages=SEUIL_MIN_PAGES_PAR_AXE
    )
    return [assimilation, mapping, seuils]


def _resultat_sans_donnees(
    produit: FicheProduit,
    marche: ParametresMarche,
    alertes: list[AlerteQualiteInput],
    tld: str,
    message: str,
) -> ResultatRechercheWeb:
    """Construit le résultat d'une exécution n'ayant produit aucune page.

    Args:
        produit: Fiche produit étudiée.
        marche: Région d'étude.
        alertes: Alertes du contrôle qualité de la fiche.
        tld: TLD national dérivé.
        message: Cause de l'absence de données.

    Returns:
        Un résultat exploitable, `donnees_disponibles=False`.
    """
    return ResultatRechercheWeb(
        produit=produit,
        marche=marche,
        alertes_qualite_input=alertes,
        plan_requetes=[],
        pages=[],
        stats=StatsCouverture(
            nb_pages_collectees=0,
            nb_pages_retenues=0,
            nb_pages_axe1=0,
            nb_pages_axe2=0,
            axes_sous_couverts=list(AXES_ANALYSE),
        ),
        statuts_collecte=[
            StatutCollecte(
                requete="—",
                succes=False,
                message_erreur=message,
                nb_pages=0,
                nb_tentatives=0,
            )
        ],
        donnees_disponibles=False,
        limites=[*LIMITES_METHODOLOGIQUES, LIMITE_AUCUNE_DONNEE],
        hypotheses=_construire_hypotheses([], marche, tld),
    )


def rechercher_web(
    produit: FicheProduit,
    marche: ParametresMarche,
) -> ResultatRechercheWeb:
    """Collecte et qualifie un corpus de pages web pour un produit et un marché.

    Cette fonction ne lève jamais d'exception : un échec total de la collecte
    retourne un résultat exploitable, avec `donnees_disponibles=False` et le
    détail des statuts de chaque requête.

    Args:
        produit: Fiche produit à étudier.
        marche: Région et langue de l'étude.

    Returns:
        Le corpus qualifié, ses statistiques de couverture, ses statuts de
        collecte, ses limites et ses hypothèses.
    """
    alertes = controler_fiche_produit(produit, marche)

    tld = deriver_tld(marche.geo)
    _LOG.info("Ciblage régional : %s → site:.%s", marche.geo, tld)

    plan, plan_complet = generer_plan_requetes(produit, marche, tld)
    if not plan:
        _LOG.error("Collecte abandonnée : aucun plan de requêtes exploitable.")
        return _resultat_sans_donnees(
            produit,
            marche,
            alertes,
            tld,
            "Génération du plan de requêtes impossible : aucune requête exploitable.",
        )

    compteurs = _Compteurs()
    urls_vues: set[str] = set()

    pages, statuts = _collecter_et_qualifier(
        plan, produit, marche, urls_vues, compteurs
    )

    axes_deficitaires = _axes_deficitaires(pages)
    if axes_deficitaires:
        pages_repli, statuts_repli, requetes_repli = _executer_repli(
            axes_deficitaires,
            produit,
            marche,
            tld,
            [requete.texte for requete in plan],
            urls_vues,
            compteurs,
        )
        pages.extend(pages_repli)
        statuts.extend(statuts_repli)
        plan.extend(requetes_repli)

    # Un axe encore déficitaire après le cycle de repli est consigné tel quel :
    # aucun second cycle n'est déclenché, sous aucune condition.
    axes_sous_couverts = _axes_deficitaires(pages)

    # Rapprochement par texte de requête plutôt que par position : le plan et
    # les statuts sont concaténés en deux temps (cycle initial puis repli).
    textes_tld = {requete.texte for requete in plan if requete.ciblage == CIBLAGE_TLD}
    tld_sans_resultat = any(
        statut.requete in textes_tld and statut.nb_pages == 0 for statut in statuts
    )

    stats = calculer_stats(
        nb_pages_collectees=compteurs.collectees,
        pages_retenues=pages,
        axes_sous_couverts=axes_sous_couverts,
        nb_doublons_ecartes=compteurs.doublons,
        nb_pages_exclues_domaine=compteurs.domaines_exclus,
        nb_pages_trop_courtes=compteurs.trop_courtes,
        nb_pages_sous_seuil=compteurs.sous_seuil,
        nb_pages_non_classifiees=compteurs.non_classifiees,
    )

    _LOG.info(
        "Collecte terminée : %s page(s) retenue(s) sur %s collectée(s), "
        "%s requête(s) exécutée(s), axes sous-couverts %s.",
        stats.nb_pages_retenues,
        stats.nb_pages_collectees,
        len(statuts),
        axes_sous_couverts or "aucun",
    )

    return ResultatRechercheWeb(
        produit=produit,
        marche=marche,
        alertes_qualite_input=alertes,
        plan_requetes=plan,
        pages=pages,
        stats=stats,
        statuts_collecte=statuts,
        donnees_disponibles=bool(pages),
        limites=_construire_limites(
            statuts,
            pages,
            compteurs,
            plan_complet,
            axes_sous_couverts,
            tld_sans_resultat,
        ),
        hypotheses=_construire_hypotheses(plan, marche, tld),
    )
