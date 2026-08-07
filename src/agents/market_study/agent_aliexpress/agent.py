"""Orchestration de bout en bout de la collecte de prix AliExpress.

Enchaînement, strictement séquentiel :

    contrôle qualité de la fiche
    → dérivation des requêtes marketplace
    → phase A : recherche (requêtes × pages)
    → dédoublonnage puis sélection déterministe
    → phase B : détail par SKU de la seule sélection
    → normalisation, statistiques, appareil critique

Le découpage en deux phases est une contrainte de quota : le détail coûte un
appel par produit. Il n'est payé que pour les produits retenus.

Aucun parallélisme : la maîtrise du quota et la lisibilité des statuts priment
sur la vitesse, et la méthode de recherche est de toute façon instable au point
qu'une rafale d'appels concurrents n'apporterait rien.

Dégradation gracieuse à tous les étages — l'échec d'une requête n'empêche pas
les autres, l'échec de la phase B n'annule pas la phase A, et un échec total
produit un résultat vide mais valide, jamais une exception.
"""

from __future__ import annotations

import time

from aliexpress_source import (
    compteur_appels,
    detailler_produit,
    rechercher_produits,
    reinitialiser_compteur,
)
from config import (
    ETAPE_REAUTORISATION_OAUTH,
    ETAPE_STRATEGIE,
    HYPOTHESE_ASSIMILATION_REQUETES,
    HYPOTHESE_NOTE_COMPARABLE,
    HYPOTHESE_PRIX_ANNONCE,
    HYPOTHESE_SELECTION_PHASE_B,
    LIMITE_AUCUNE_DONNEE,
    LIMITE_PHASE_A_PARTIELLE,
    LIMITE_PHASE_B_ABSENTE,
    LIMITE_PHASE_B_PARTIELLE,
    LIMITE_REQUETES_NON_OPTIMISEES,
    LIMITES_METHODOLOGIQUES,
    NB_MAX_PAGES_PAR_REQUETE,
    NB_MAX_REQUETES,
    PAUSE_ENTRE_APPELS_SECS,
    TAILLE_PAGE,
    obtenir_logger,
)
from normalize import (
    calculer_stats,
    dedoublonner,
    horodatage_utc,
    normaliser_detail,
    normaliser_produits_recherche,
    selectionner_produits,
)
from schemas import (
    FicheProduit,
    ParametresMarche,
    ProduitDetaille,
    ProduitRecherche,
    ResultatCollecteAliExpressAPI,
    StatutCollecte,
)
from strategy import controler_fiche_produit, deriver_requetes

_LOG = obtenir_logger(__name__)


def collecter_aliexpress_api(
    produit: FicheProduit, marche: ParametresMarche
) -> ResultatCollecteAliExpressAPI:
    """Collecte les produits et prix par SKU d'une région d'étude donnée.

    Args:
        produit: Fiche produit soumise à l'étude.
        marche: Région d'étude — pays de livraison, langue et devise. Ce triplet
            est propagé à chaque appel API et recopié dans chaque ligne de prix.

    Returns:
        Le résultat structuré : produits, prix par SKU dans la devise d'étude,
        statistiques, statuts d'appels, limites et hypothèses. Aucune exception
        n'est levée, y compris en cas d'échec total.
    """
    reinitialiser_compteur()
    _LOG.info(
        "Collecte AliExpress API — produit=« %s », région=%s/%s/%s",
        produit.nom,
        marche.geo,
        marche.devise,
        marche.langue,
    )

    statuts: list[StatutCollecte] = []
    limites: list[str] = list(LIMITES_METHODOLOGIQUES)

    alertes = controler_fiche_produit(produit, marche)
    strategie, repli_utilise = deriver_requetes(produit, marche)
    if repli_utilise:
        limites.append(LIMITE_REQUETES_NON_OPTIMISEES)
        statuts.append(
            StatutCollecte(
                etape=ETAPE_STRATEGIE,
                cible=produit.nom,
                succes=False,
                message_erreur=LIMITE_REQUETES_NON_OPTIMISEES,
                nb_items=len(strategie.requetes),
            )
        )

    requetes = strategie.requetes[:NB_MAX_REQUETES]

    produits, statuts_a, limites_a, totaux, interrompu = _phase_a(
        requetes, marche, statuts
    )
    statuts.extend(statuts_a)
    limites.extend(limites_a)

    produits = dedoublonner(produits)
    _LOG.info("Phase A : %s produit(s) unique(s).", len(produits))

    selection: list[ProduitRecherche] = []
    produits_detailles: list[ProduitDetaille] = []
    if produits and not interrompu:
        selection, limites_selection = selectionner_produits(produits, requetes)
        limites.extend(limites_selection)
        _LOG.info("Sélection : %s produit(s) à détailler.", len(selection))

        produits_detailles, statuts_b, limites_b = _phase_b(selection, marche)
        statuts.extend(statuts_b)
        limites.extend(limites_b)

    if selection and not produits_detailles:
        limites.append(LIMITE_PHASE_B_ABSENTE)
    elif selection and len(produits_detailles) < len(selection):
        limites.append(LIMITE_PHASE_B_PARTIELLE)

    if not produits:
        limites.append(LIMITE_AUCUNE_DONNEE)

    stats = calculer_stats(
        marche=marche,
        produits=produits,
        nb_retenus=len(selection),
        produits_detailles=produits_detailles,
        total_annonce_par_requete=totaux,
        nb_appels_api=compteur_appels(),
    )

    _LOG.info(
        "Collecte terminée : %s produit(s), %s détaillé(s), %s SKU, %s appel(s) API.",
        stats.nb_produits_recherche,
        stats.nb_produits_detailles,
        stats.nb_skus,
        stats.nb_appels_api,
    )

    return ResultatCollecteAliExpressAPI(
        produit=produit,
        marche=marche,
        alertes_qualite_input=alertes,
        requetes=requetes,
        justification_requetes=strategie.justification,
        produits=produits,
        produits_detailles=produits_detailles,
        stats=stats,
        statuts_collecte=statuts,
        donnees_disponibles=bool(produits),
        limites=_uniques(limites),
        hypotheses=[
            HYPOTHESE_ASSIMILATION_REQUETES,
            HYPOTHESE_SELECTION_PHASE_B,
            HYPOTHESE_PRIX_ANNONCE,
            HYPOTHESE_NOTE_COMPARABLE,
        ],
    )


def _phase_a(
    requetes: list[str], marche: ParametresMarche, statuts_amont: list[StatutCollecte]
) -> tuple[list[ProduitRecherche], list[StatutCollecte], list[str], dict[str, int], bool]:
    """Exécute la recherche pour chaque requête et chaque page.

    Une requête dont une page échoue n'interrompt pas les suivantes. Une page
    renvoyant moins de `TAILLE_PAGE` items met fin à la pagination de cette
    requête : la suivante serait vide.

    Args:
        requetes: Requêtes marketplace à soumettre.
        marche: Région d'étude.
        statuts_amont: Statuts déjà produits, consultés pour ne rien tenter
            après une demande de ré-autorisation OAuth.

    Returns:
        Un quintuplet `(produits, statuts, limites, totaux_annonces, interrompu)`.
    """
    produits: list[ProduitRecherche] = []
    statuts: list[StatutCollecte] = []
    limites: list[str] = []
    totaux: dict[str, int] = {}
    au_moins_un_echec = False
    interrompu = _reautorisation_demandee(statuts_amont)

    for requete in requetes:
        if interrompu:
            break
        for page in range(1, NB_MAX_PAGES_PAR_REQUETE + 1):
            items, statut = rechercher_produits(requete, marche, page)
            statuts.append(statut)

            if statut.etape == ETAPE_REAUTORISATION_OAUTH:
                _LOG.error(
                    "Ré-autorisation OAuth requise : collecte interrompue avant "
                    "d'engager d'autres appels."
                )
                interrompu = True
                break

            if not statut.succes:
                au_moins_un_echec = True
                break

            if statut.total_annonce is not None:
                totaux[requete] = statut.total_annonce

            horodatage = horodatage_utc()
            normalises, anomalies, limites_page = normaliser_produits_recherche(
                items, requete, marche, horodatage
            )
            produits.extend(normalises)
            statuts.extend(anomalies)
            limites.extend(limites_page)

            if len(items) < TAILLE_PAGE:
                break

    if au_moins_un_echec:
        limites.append(LIMITE_PHASE_A_PARTIELLE)
    return produits, statuts, limites, totaux, interrompu


def _phase_b(
    selection: list[ProduitRecherche], marche: ParametresMarche
) -> tuple[list[ProduitDetaille], list[StatutCollecte], list[str]]:
    """Récupère le détail par SKU des produits sélectionnés.

    Args:
        selection: Produits retenus à l'issue de la phase A.
        marche: Région d'étude, propagée telle quelle au détail.

    Returns:
        Un triplet `(produits_detailles, statuts, limites)`.
    """
    detailles: list[ProduitDetaille] = []
    statuts: list[StatutCollecte] = []
    limites: list[str] = []

    for produit in selection:
        brut, statut = detailler_produit(produit.item_id, marche)
        statuts.append(statut)

        if statut.etape == ETAPE_REAUTORISATION_OAUTH:
            _LOG.error("Ré-autorisation OAuth requise : phase B interrompue.")
            break
        if not statut.succes or brut is None:
            continue

        horodatage = horodatage_utc()
        detaille, anomalies, limites_produit = normaliser_detail(
            brut, produit.item_id, marche, horodatage
        )
        statuts.extend(anomalies)
        limites.extend(limites_produit)
        if detaille is not None:
            detailles.append(detaille)

        if PAUSE_ENTRE_APPELS_SECS:
            time.sleep(PAUSE_ENTRE_APPELS_SECS)

    return detailles, statuts, limites


def _reautorisation_demandee(statuts: list[StatutCollecte]) -> bool:
    """Indique qu'une ré-autorisation OAuth a déjà été signalée.

    Args:
        statuts: Statuts produits jusqu'ici.

    Returns:
        Vrai si un statut réclame une ré-autorisation.
    """
    return any(statut.etape == ETAPE_REAUTORISATION_OAUTH for statut in statuts)


def _uniques(valeurs: list[str]) -> list[str]:
    """Dédoublonne une liste en conservant l'ordre d'apparition.

    Args:
        valeurs: Limites accumulées, potentiellement répétées.

    Returns:
        Les valeurs uniques, dans l'ordre.
    """
    vues: set[str] = set()
    uniques: list[str] = []
    for valeur in valeurs:
        if valeur in vues:
            continue
        vues.add(valeur)
        uniques.append(valeur)
    return uniques
