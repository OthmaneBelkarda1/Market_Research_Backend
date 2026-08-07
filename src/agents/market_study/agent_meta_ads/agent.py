"""Orchestration de bout en bout de la collecte Meta Ads.

Séquence : résolution de la région en pays de diffusion → contrôle de la fiche →
plan de recherches (plus, le cas échéant, les annonceurs imposés) → runs Apify
(avec élargissement des recherches restées vides) → filtres déterministes →
classification LLM → contrôle de volume (avec au plus un cycle de repli) →
statistiques → résultat.

La résolution de la région passe en premier et fait office de garde : si elle
échoue, l'agent s'arrête là, sans dépenser ni run Apify ni appel LLM
supplémentaire, et renvoie un résultat `region_couverte=False`.

Aucune exception n'est propagée : tout échec est converti en statut de collecte
et en limite explicite.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from config import (
    HYPOTHESE_ASSIMILATION_RECHERCHES,
    HYPOTHESE_CANAL,
    HYPOTHESE_SEUILS,
    LIMITE_AUCUNE_DONNEE,
    LIMITE_COLLECTE_PARTIELLE,
    LIMITE_CORPUS_INSUFFISANT,
    LIMITE_CORPUS_NON_CLASSIFIE,
    LIMITE_CORPUS_PARTIELLEMENT_CLASSIFIE,
    LIMITE_PLAFOND_ATTEINT,
    LIMITE_PLAN_INCOMPLET,
    LIMITE_RECHERCHES_VIDES,
    LIMITE_REGION_NON_COUVERTE,
    LIMITES_METHODOLOGIQUES,
    MAX_ANNONCES_PAR_RECHERCHE,
    MOTIF_CIBLAGE_PAYS,
    PARALLELISME_MAX,
    PAUSE_AVANT_REPLI_SECS,
    SEUIL_MIN_ANNONCES,
    SEUIL_PERTINENCE,
    obtenir_logger,
)
from filtering import (
    CompteursFiltrage,
    appliquer_seuil_pertinence,
    classifier_annonces,
    filtrer_deterministe,
)
from meta_ads_source import collecter_annonces
from normalize import calculer_stats, normaliser_annonces
from schemas import (
    AlerteQualiteInput,
    Annonce,
    FicheProduit,
    ParametresMarche,
    PaysCible,
    RecherchePlanifiee,
    ResultatRechercheMetaAds,
    StatsCollecte,
    StatutCollecte,
)
from strategy import (
    controler_fiche_produit,
    elargir,
    generer_plan_recherches,
    generer_recherches_repli,
    peut_etre_elargie,
    recherche_annonceur,
    resoudre_pays,
)

_LOG = obtenir_logger(__name__)


class _Compteurs:
    """Cumul des décomptes sur l'ensemble des cycles de collecte."""

    def __init__(self) -> None:
        """Initialise tous les compteurs à zéro."""
        self.collectees = 0
        self.doublons = 0
        self.doublons_creatif = 0
        self.hors_criteres = 0
        self.sous_seuil = 0
        self.non_classifiees = 0

    def ajouter_filtrage(self, compteurs: CompteursFiltrage) -> None:
        """Cumule les décomptes d'un passage de filtres déterministes.

        Args:
            compteurs: Décomptes du passage.
        """
        self.doublons += compteurs.doublons
        self.doublons_creatif += compteurs.doublons_creatif
        self.hors_criteres += compteurs.hors_criteres


# --------------------------------------------------------------------------- #
# Collecte
# --------------------------------------------------------------------------- #


def _executer_recherches(
    recherches: list[RecherchePlanifiee],
    max_annonces: int,
    compteurs: _Compteurs,
) -> tuple[list[tuple[RecherchePlanifiee, list[Annonce]]], list[StatutCollecte]]:
    """Exécute un ensemble de recherches, une par run, en parallélisme borné.

    Args:
        recherches: Recherches à exécuter.
        max_annonces: Plafond d'annonces par run.
        compteurs: Cumul des décomptes, **modifié en place**.

    Returns:
        Un couple `(annonces_par_recherche, statuts)`. Les annonces sont
        normalisées mais ni dédoublonnées ni filtrées.
    """
    if not recherches:
        return [], []

    with ThreadPoolExecutor(max_workers=max(1, PARALLELISME_MAX)) as executeur:
        resultats = list(
            executeur.map(
                lambda recherche: collecter_annonces(recherche, max_annonces), recherches
            )
        )

    par_recherche: list[tuple[RecherchePlanifiee, list[Annonce]]] = []
    statuts: list[StatutCollecte] = []

    for recherche, (items, statut) in zip(recherches, resultats):
        statuts.append(statut)
        annonces = normaliser_annonces(items, recherche)
        compteurs.collectees += len(annonces)
        par_recherche.append((recherche, annonces))

    return par_recherche, statuts


def _elargir_recherches_vides(
    par_recherche: list[tuple[RecherchePlanifiee, list[Annonce]]],
    pays: PaysCible,
    max_annonces: int,
    compteurs: _Compteurs,
) -> tuple[list[tuple[RecherchePlanifiee, list[Annonce]]], list[StatutCollecte]]:
    """Rejoue une fois, sans filtres, les recherches restées sans annonce.

    Une recherche vide vient souvent d'un filtre de statut trop serré : «
    actives » exclut tout ce qui vient de s'arrêter. La relance retire les
    filtres d'URL sans toucher aux mots-clés — et n'est tentée que là où il
    reste effectivement un filtre à relâcher, pour ne pas payer un run qui
    rejouerait la même requête.

    Args:
        par_recherche: Résultat du premier passage.
        pays: Pays de diffusion interrogé.
        max_annonces: Plafond d'annonces par run.
        compteurs: Cumul des décomptes, **modifié en place**.

    Returns:
        Un couple `(annonces_par_recherche, statuts)` pour les seules relances.
    """
    vides = [
        recherche
        for recherche, annonces in par_recherche
        if not annonces and peut_etre_elargie(recherche)
    ]
    if not vides:
        return [], []

    _LOG.warning(
        "%s recherche(s) sans annonce — relance élargie dans %s s.",
        len(vides),
        PAUSE_AVANT_REPLI_SECS,
    )
    time.sleep(PAUSE_AVANT_REPLI_SECS)
    return _executer_recherches(
        [elargir(recherche, pays.code_pays) for recherche in vides], max_annonces, compteurs
    )


def _collecter_et_qualifier(
    recherches: list[RecherchePlanifiee],
    pays: PaysCible,
    produit_reference: FicheProduit,
    max_annonces: int,
    cles_vues: set[str],
    creatifs_vus: set[str],
    compteurs: _Compteurs,
) -> tuple[list[Annonce], list[StatutCollecte], list[RecherchePlanifiee]]:
    """Exécute un cycle complet : collecte, élargissement, filtres, classification.

    Args:
        recherches: Recherches du cycle.
        pays: Pays de diffusion interrogé.
        produit_reference: Fiche produit étudiée.
        max_annonces: Plafond d'annonces par run.
        cles_vues: Identifiants d'annonces déjà vus, **modifié en place**.
        creatifs_vus: Créatifs déjà retenus, **modifié en place**, pour que les
            cycles successifs ne se recouvrent pas.
        compteurs: Cumul des décomptes, **modifié en place**.

    Returns:
        Un triplet `(annonces_retenues, statuts, recherches_executees)`. Les
        recherches exécutées incluent les relances élargies, dont l'URL diffère.
    """
    par_recherche, statuts = _executer_recherches(recherches, max_annonces, compteurs)

    relances, statuts_relance = _elargir_recherches_vides(
        par_recherche, pays, max_annonces, compteurs
    )
    par_recherche.extend(relances)
    statuts.extend(statuts_relance)

    retenues: list[Annonce] = []
    for recherche, annonces in par_recherche:
        filtrees, decomptes = filtrer_deterministe(
            annonces, recherche, cles_vues, creatifs_vus
        )
        compteurs.ajouter_filtrage(decomptes)
        retenues.extend(filtrees)

    retenues, non_classifiees = classifier_annonces(retenues, produit_reference)
    compteurs.non_classifiees += non_classifiees

    retenues, ecartees = appliquer_seuil_pertinence(retenues)
    compteurs.sous_seuil += ecartees

    executees = [recherche for recherche, _ in par_recherche]
    return retenues, statuts, executees


def _ordonner(annonces: list[Annonce]) -> list[Annonce]:
    """Classe le corpus par pertinence, puis par longévité de diffusion.

    La durée de diffusion départage les annonces également pertinentes : une
    annonce diffusée depuis des mois est celle qu'il faut lire en premier. Ce
    n'est pas une mesure de performance (voir les limites du résultat), mais
    c'est le seul signal de sélection dont ce corpus dispose.

    Args:
        annonces: Corpus qualifié.

    Returns:
        Le corpus ordonné.
    """
    return sorted(
        annonces,
        key=lambda annonce: (
            -(annonce.pertinence or 0.0),
            -(annonce.duree_diffusion_jours or 0),
            annonce.rang_collecte,
        ),
    )


# --------------------------------------------------------------------------- #
# Limites et hypothèses
# --------------------------------------------------------------------------- #


def _construire_limites(
    statuts: list[StatutCollecte],
    annonces: list[Annonce],
    compteurs: _Compteurs,
    plan_complet: bool,
) -> list[str]:
    """Assemble les limites méthodologiques et conjoncturelles du résultat.

    Args:
        statuts: Comptes rendus de tous les runs exécutés.
        annonces: Corpus final.
        compteurs: Décomptes cumulés du filtrage et de la classification.
        plan_complet: Vrai si le plan a atteint le nombre de recherches visé.

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

    if any(statut.succes and not statut.nb_items for statut in statuts):
        limites.append(LIMITE_RECHERCHES_VIDES)

    if any(statut.plafond_atteint for statut in statuts):
        limites.append(LIMITE_PLAFOND_ATTEINT)

    if annonces and compteurs.non_classifiees >= len(annonces):
        limites.append(LIMITE_CORPUS_NON_CLASSIFIE)
    elif compteurs.non_classifiees:
        limites.append(LIMITE_CORPUS_PARTIELLEMENT_CLASSIFIE)

    if len(annonces) < SEUIL_MIN_ANNONCES:
        limites.append(LIMITE_CORPUS_INSUFFISANT)

    return limites


def _construire_hypotheses(
    plan: list[RecherchePlanifiee], pays: PaysCible, max_annonces: int
) -> list[str]:
    """Assemble les hypothèses sous-jacentes au corpus livré.

    Args:
        plan: Recherches effectivement exécutées.
        pays: Pays de diffusion interrogé.
        max_annonces: Plafond d'annonces par recherche.

    Returns:
        La liste des hypothèses à joindre au résultat.
    """
    ciblage = f"{MOTIF_CIBLAGE_PAYS} Appliqué ici : {pays.explication}"

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
        seuil_annonces=SEUIL_MIN_ANNONCES,
        max_annonces=max_annonces,
    )
    return [ciblage, HYPOTHESE_CANAL, assimilation, seuils]


def _resultat_sans_donnees(
    produit: FicheProduit,
    marche: ParametresMarche,
    pays: PaysCible,
    alertes: list[AlerteQualiteInput],
    message: str,
    max_annonces: int,
) -> ResultatRechercheMetaAds:
    """Construit le résultat d'une exécution n'ayant produit aucun corpus.

    Args:
        produit: Fiche produit étudiée.
        marche: Région d'étude.
        pays: Pays de diffusion retenu.
        alertes: Alertes du contrôle qualité de la fiche.
        message: Cause de l'absence de données.
        max_annonces: Plafond d'annonces par recherche.

    Returns:
        Un résultat exploitable, `donnees_disponibles=False`.
    """
    return ResultatRechercheMetaAds(
        produit=produit,
        marche=marche,
        region_couverte=True,
        pays=pays,
        alertes_qualite_input=alertes,
        plan_recherches=[],
        annonces=[],
        stats=StatsCollecte(nb_annonces_collectees=0, nb_annonces_retenues=0),
        statuts_collecte=[
            StatutCollecte(
                recherche="—",
                url="—",
                succes=False,
                message_erreur=message,
                nb_items=0,
                nb_tentatives=0,
            )
        ],
        donnees_disponibles=False,
        limites=[*LIMITES_METHODOLOGIQUES, LIMITE_AUCUNE_DONNEE],
        hypotheses=_construire_hypotheses([], pays, max_annonces),
    )


def _resultat_region_non_resolue(
    produit: FicheProduit, marche: ParametresMarche, explication: str
) -> ResultatRechercheMetaAds:
    """Construit le résultat d'une région que le module n'a pas su résoudre.

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
    return ResultatRechercheMetaAds(
        produit=produit,
        marche=marche,
        region_couverte=False,
        pays=None,
        alertes_qualite_input=[],
        plan_recherches=[],
        annonces=[],
        stats=StatsCollecte(nb_annonces_collectees=0, nb_annonces_retenues=0),
        statuts_collecte=[
            StatutCollecte(
                recherche="—",
                url="—",
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


def rechercher_meta_ads(
    produit: FicheProduit,
    marche: ParametresMarche,
    urls_annonceurs: list[str] | None = None,
    max_annonces_par_recherche: int = MAX_ANNONCES_PAR_RECHERCHE,
) -> ResultatRechercheMetaAds:
    """Collecte et qualifie un corpus d'annonces Meta pour une région.

    Le pays retenu sélectionne les annonces DIFFUSÉES dans ce pays, quel que
    soit le pays de l'annonceur : c'est la pression publicitaire subie sur le
    marché étudié qui est collectée, pas l'activité des entreprises locales.

    Cette fonction ne lève jamais d'exception : un échec total de la collecte
    retourne un résultat exploitable, avec `donnees_disponibles=False` et le
    détail des statuts de chaque run.

    Args:
        produit: Fiche produit à étudier.
        marche: Région d'étude et langue du marché.
        urls_annonceurs: URLs de Pages Facebook à surveiller directement, en
            plus du plan de recherches. Un run par URL, sans aucun filtre de
            pays ni de statut : c'est l'annonceur qui est suivi, pas un marché.
        max_annonces_par_recherche: Plafond d'annonces par run. **L'actor est
            facturé à l'annonce** : c'est le principal levier de coût.

    Returns:
        Le corpus qualifié, ses statistiques, ses statuts de collecte, ses
        limites et ses hypothèses.
    """
    pays, explication = resoudre_pays(marche.geo)
    if pays is None:
        return _resultat_region_non_resolue(produit, marche, explication)
    _LOG.info("Pays retenu : %s", explication)

    alertes = controler_fiche_produit(produit, marche)

    plan, plan_complet = generer_plan_recherches(produit, marche, pays)
    annonceurs = [recherche_annonceur(url) for url in (urls_annonceurs or []) if url.strip()]
    plan.extend(annonceurs)

    if not plan:
        _LOG.error("Collecte abandonnée : aucun plan de recherches exploitable.")
        return _resultat_sans_donnees(
            produit,
            marche,
            pays,
            alertes,
            "Génération du plan de recherches impossible : aucune recherche exploitable.",
            max_annonces_par_recherche,
        )

    compteurs = _Compteurs()
    cles_vues: set[str] = set()
    creatifs_vus: set[str] = set()

    annonces, statuts, executees = _collecter_et_qualifier(
        plan,
        pays,
        produit,
        max_annonces_par_recherche,
        cles_vues,
        creatifs_vus,
        compteurs,
    )
    plan = executees

    if len(annonces) < SEUIL_MIN_ANNONCES:
        _LOG.warning(
            "Corpus sous le seuil (%s < %s) — cycle de repli déclenché.",
            len(annonces),
            SEUIL_MIN_ANNONCES,
        )
        recherches_repli = generer_recherches_repli(
            produit,
            marche,
            pays,
            [recherche.mots_cles for recherche in plan if not recherche.est_annonceur],
        )
        if recherches_repli:
            annonces_repli, statuts_repli, executees_repli = _collecter_et_qualifier(
                recherches_repli,
                pays,
                produit,
                max_annonces_par_recherche,
                cles_vues,
                creatifs_vus,
                compteurs,
            )
            annonces.extend(annonces_repli)
            statuts.extend(statuts_repli)
            plan.extend(executees_repli)
        else:
            _LOG.warning("Aucune recherche de repli exploitable : corpus inchangé.")

    # Un corpus encore trop court après le cycle de repli est livré tel quel :
    # aucun second cycle n'est déclenché, sous aucune condition.
    annonces = _ordonner(annonces)

    stats = calculer_stats(
        nb_annonces_collectees=compteurs.collectees,
        annonces_retenues=annonces,
        nb_doublons_ecartes=compteurs.doublons,
        nb_doublons_creatif=compteurs.doublons_creatif,
        nb_annonces_hors_criteres=compteurs.hors_criteres,
        nb_annonces_sous_seuil=compteurs.sous_seuil,
        nb_annonces_non_classifiees=compteurs.non_classifiees,
    )

    _LOG.info(
        "Collecte terminée sur %s : %s annonce(s) retenue(s) sur %s collectée(s), "
        "%s annonceur(s), %s run(s) exécuté(s).",
        pays.code_pays,
        stats.nb_annonces_retenues,
        stats.nb_annonces_collectees,
        stats.nb_annonceurs,
        len(statuts),
    )

    return ResultatRechercheMetaAds(
        produit=produit,
        marche=marche,
        region_couverte=True,
        pays=pays,
        alertes_qualite_input=alertes,
        plan_recherches=plan,
        annonces=annonces,
        stats=stats,
        statuts_collecte=statuts,
        donnees_disponibles=bool(annonces),
        limites=_construire_limites(statuts, annonces, compteurs, plan_complet),
        hypotheses=_construire_hypotheses(plan, pays, max_annonces_par_recherche),
    )
