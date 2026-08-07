"""Orchestration de bout en bout de l'agent PLC.

Séquence : chargement → condition de déclenchement → signaux → orientation puis
agrégation → recommandations de phase → post-validation → assemblage.

Deux invariants :

- **le non-déclenchement est un résultat, pas une erreur** : l'agent produit
  alors une sortie courte valide, sans le moindre appel LLM de classification ;
- **l'agent produit toujours une sortie complète** quand il est déclenché. Une
  entrée absente rend des familles non évaluables et dégrade la confiance ;
  l'échec d'une chaîne vide un bloc. Aucun de ces cas n'empêche la sortie d'être
  conforme au schéma.
"""

from __future__ import annotations

from datetime import UTC, datetime

from chargement import charger_entrees, evaluer_declenchement
from classification import (
    agreger,
    conditions_par_gabarit,
    corriger_orientations,
    deriver_confiance,
    enoncer_regle,
    orienter_signaux,
)
from config import (
    CONFIANCE_FAIBLE,
    HYPOTHESES_SYSTEMATIQUES,
    INCERTITUDE_ELEVEE,
    LIMITE_D4_PUBLICITE,
    LIMITES_SYSTEMATIQUES,
    MAX_CONDITIONS_REEXAMEN,
    MAX_FAITS_CLES,
    MAX_INDICATEURS_PAR_FAIT_CLE,
    MODE_NON_DECLENCHE,
    NIVEAUX_CONFIANCE,
    POIDS_FAMILLES,
    STATUT_REGLE,
    FAMILLE_PUBLICITE,
    logger,
    verifier_cle_api,
)
from recommandations import produire_recommandations_phase
from schemas import (
    Classification,
    ConfianceGlobale,
    DossierPLC,
    EntreeRecommandations,
    FaitCle,
    FicheProduit,
    ParametresMarche,
    ResultatPLC,
    StatutAnalyse,
)
from signaux import construire_dossier, limites_du_dossier
from validation import valider


def _horodatage() -> str:
    """Retourne l'horodatage courant en ISO 8601 UTC.

    Returns:
        Un horodatage à la seconde, suffixé « Z ».
    """
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dedupliquer(elements: list[str], plafond: int) -> list[str]:
    """Déduplique une liste de chaînes en conservant l'ordre.

    Args:
        elements: Chaînes à dédupliquer.
        plafond: Nombre maximal d'éléments conservés.

    Returns:
        La liste dédupliquée et bornée.
    """
    vues: set[str] = set()
    retenus: list[str] = []
    for element in elements:
        propre = element.strip()
        if not propre or propre in vues:
            continue
        vues.add(propre)
        retenus.append(propre)
    return retenus[:plafond]


def _faits_cles(dossier: DossierPLC) -> list[FaitCle]:
    """Construit les faits clés PAR LE CODE depuis le dossier PLC.

    Aucune valeur ne provient d'un modèle : l'énoncé est un gabarit, la valeur
    est recopiée telle quelle.

    Args:
        dossier: Dossier PLC construit.

    Returns:
        Les faits clés, bornés.
    """
    faits: list[FaitCle] = []
    for famille in dossier.familles:
        if not famille.disponible:
            continue
        for indicateur in famille.indicateurs[:MAX_INDICATEURS_PAR_FAIT_CLE]:
            faits.append(
                FaitCle(
                    enonce=f"{famille.intitule} — {indicateur.libelle}",
                    ref=indicateur.ref,
                    valeur=indicateur.valeur,
                )
            )
    return faits[:MAX_FAITS_CLES]


def _synthese_de_repli(
    classification: Classification, dossier: DossierPLC, produit: FicheProduit
) -> str:
    """Rédige une synthèse minimale, sans LLM.

    Args:
        classification: Classification calculée.
        dossier: Dossier PLC.
        produit: Fiche du produit étudié.

    Returns:
        Une synthèse factuelle de quelques lignes.
    """
    lignes = [
        f"Produit : {produit.nom}.",
        f"Phase de cycle de vie du marché retenue : "
        f"**{classification.phase_probable or 'aucune'}** "
        f"(incertitude {classification.incertitude}, "
        f"{classification.nb_familles_evaluees} famille(s) de signaux évaluée(s)).",
    ]
    for famille in dossier.familles:
        etat = "disponible" if famille.disponible else "non évaluable"
        lignes.append(
            f"- {famille.famille} ({POIDS_FAMILLES.get(famille.famille, 0.0):.2f}) : {etat}"
        )
    lignes.append(
        "La rédaction analytique n'a pas pu être produite (chaîne en échec) : seules "
        "la classification et ses scores sont livrés."
    )
    lignes.append(
        "Rappel : la grille de lecture des phases est une hypothèse de travail non "
        "validée, et cette classification décrit le marché de la catégorie tel "
        "qu'observé dans le corpus, pas le produit lui-même."
    )
    return "\n".join(lignes)


def _confiance_globale(
    dossier: DossierPLC, classification: Classification | None
) -> ConfianceGlobale:
    """Détermine la confiance globale de la sortie.

    Args:
        dossier: Dossier PLC.
        classification: Classification calculée, ou `None`.

    Returns:
        Le niveau de confiance et sa justification.
    """
    disponibles = dossier.familles_disponibles()
    facteurs = [
        f"{len(disponibles)} famille(s) de signaux disponible(s) sur "
        f"{len(dossier.familles)}",
    ]
    facteurs.extend(
        f"[{entree}] confiance amont {niveau or 'non déclarée'}"
        for entree, niveau in dossier.confiances_amont.items()
    )
    for famille in dossier.familles:
        if not famille.disponible:
            facteurs.append(f"[{famille.famille}] famille non évaluable")

    niveau = classification.confiance if classification else CONFIANCE_FAIBLE
    if niveau not in NIVEAUX_CONFIANCE:
        niveau = CONFIANCE_FAIBLE
    manquantes = [f.famille for f in dossier.familles if not f.disponible]
    if manquantes:
        justification = (
            f"Famille(s) de signaux non évaluable(s) : {', '.join(manquantes)}. La "
            f"classification repose sur une vue partielle des signaux temporels."
        )
    else:
        justification = (
            "Les quatre familles de signaux sont renseignées. La grille de lecture "
            "reste néanmoins une hypothèse de travail."
        )
    return ConfianceGlobale(niveau=niveau, justification=justification, facteurs=facteurs)


def _sortie_non_declenchee(
    entrees_recommandations: EntreeRecommandations,
    produit: FicheProduit,
    marche: ParametresMarche,
    sources: list,
    alertes: list,
    declenchement,
    limites: list[str],
) -> ResultatPLC:
    """Construit la sortie courte de non-déclenchement (F6.4).

    Args:
        entrees_recommandations: Sortie F5 validée.
        produit: Fiche du produit.
        marche: Marché de l'étude.
        sources: Comptes rendus de chargement.
        alertes: Alertes de cohérence.
        declenchement: Déclenchement évalué.
        limites: Limites déjà collectées.

    Returns:
        Le résultat court, valide et complet.
    """
    verdict = entrees_recommandations.verdict_potentiel
    conditions = _dedupliquer(
        list(verdict.conditions_reexamen), MAX_CONDITIONS_REEXAMEN
    ) or [
        "Relancer l'analyse de potentiel après une nouvelle collecte : seule une "
        "conclusion positive de l'agent amont déclenche la classification de phase."
    ]
    synthese = "\n".join(
        [
            f"Produit : {produit.nom} — marché {marche.geo}.",
            f"La classification de phase de cycle de vie n'a pas été déclenchée : le "
            f"verdict de potentiel amont est « {verdict.verdict} » "
            f"(score {verdict.score_total} sur {verdict.nb_criteres_evalues} "
            f"critère(s) évalué(s)), et le cahier des charges réserve cette "
            f"classification aux produits à potentiel positif.",
            "Ce n'est pas une erreur : aucune phase n'est proposée, aucune "
            "recommandation de phase n'est produite, et les conditions de réexamen "
            "héritées de l'analyse amont sont rappelées ci-dessous.",
        ]
    )
    logger.info("non-déclenchement : verdict amont « %s »", verdict.verdict)
    return ResultatPLC(
        produit=produit,
        marche=marche,
        horodatage_utc=_horodatage(),
        sources_utilisees=sources,
        alertes_coherence=alertes,
        declenchement=declenchement,
        dossier_plc=None,
        signaux=[],
        classification=None,
        recommandations_phase=[],
        conditions_reexamen=conditions,
        faits_cles=[],
        synthese_executive=synthese,
        statuts_analyse=[
            StatutAnalyse(
                phase="declenchement",
                succes=True,
                message_erreur=declenchement.motif,
                nb_elements=0,
            )
        ],
        donnees_suffisantes=False,
        confiance_globale=ConfianceGlobale(
            niveau=CONFIANCE_FAIBLE,
            justification=(
                "Aucune classification n'a été produite : la condition de "
                "déclenchement du cahier des charges n'est pas remplie."
            ),
            facteurs=[f"verdict amont : {verdict.verdict}"],
        ),
        limites=limites,
        hypotheses=list(HYPOTHESES_SYSTEMATIQUES),
    )


def classifier_plc(
    chemin_recommandations: str,
    chemin_insights: str | None,
    chemin_concurrence: str | None,
    forcer: bool,
    langue_analyse: str,
) -> ResultatPLC:
    """Classe la phase de cycle de vie du marché du produit.

    Args:
        chemin_recommandations: Sortie de F5 — requise.
        chemin_insights: Sortie de F3, ou `None`.
        chemin_concurrence: Sortie de F4, ou `None`.
        forcer: Exécuter malgré un verdict amont non positif (étude et test).
        langue_analyse: Code langue de rédaction.

    Returns:
        Le résultat validé.

    Raises:
        ErreurCoherenceProduit: Si les fichiers portent des produits différents.
        ValueError: Si la sortie F5 est absente ou inexploitable.
        RuntimeError: Si la clé API est absente alors que la classification est
            déclenchée.
    """
    entrees, sources, alertes = charger_entrees(
        chemin_recommandations, chemin_insights, chemin_concurrence
    )
    if entrees.recommandations is None:
        raise ValueError(
            "la sortie F5 (--recommandations) est absente ou inexploitable : sans "
            "verdict ni dossier de synthèse amont, aucune classification de phase "
            "n'est possible."
        )

    recommandations_amont = entrees.recommandations
    produit = entrees.produit or FicheProduit(nom="inconnu", description="")
    marche = entrees.marche or ParametresMarche(geo="??", langue=langue_analyse)
    libelle_marche = f"{marche.geo} ({marche.langue})"

    limites = list(entrees.limites_amont)
    limites.extend(LIMITES_SYSTEMATIQUES)
    for compte_rendu in sources:
        limites.extend(
            f"[{compte_rendu.source}] {avertissement}"
            for avertissement in compte_rendu.avertissements
        )

    declenchement, limites_declenchement = evaluer_declenchement(
        recommandations_amont, forcer
    )
    limites = limites_declenchement + limites

    if declenchement.mode == MODE_NON_DECLENCHE:
        resultat = _sortie_non_declenchee(
            recommandations_amont,
            produit,
            marche,
            sources,
            alertes,
            declenchement,
            limites,
        )
        resultat, statuts_validation, alertes_validation = valider(resultat, None)
        resultat.statuts_analyse.extend(statuts_validation)
        resultat.alertes_coherence.extend(alertes_validation)
        return resultat

    # --- Classification déclenchée ------------------------------------------ #
    dossier = construire_dossier(entrees)
    limites.extend(limites_du_dossier(dossier))
    famille_publicite = dossier.famille(FAMILLE_PUBLICITE)
    if famille_publicite is not None and not famille_publicite.disponible:
        limites.insert(0, LIMITE_D4_PUBLICITE)

    verifier_cle_api()
    statuts: list[StatutAnalyse] = [
        StatutAnalyse(
            phase="declenchement",
            succes=True,
            message_erreur=declenchement.motif,
            nb_elements=len(dossier.familles_disponibles()),
        )
    ]

    orientations, statut_orientation = orienter_signaux(
        dossier, produit, libelle_marche, langue_analyse
    )
    statuts.append(statut_orientation)

    signaux, corrections = corriger_orientations(orientations, dossier)
    if corrections:
        statuts.append(
            StatutAnalyse(
                phase="correction_orientations",
                succes=True,
                message_erreur="; ".join(corrections[:8]),
                nb_elements=len(corrections),
            )
        )

    classification = agreger(signaux)
    classification.confiance = deriver_confiance(dossier, classification)

    if not orientations:
        limites.append(
            "La chaîne d'orientation des signaux a échoué : aucune famille n'a pu "
            "être orientée, la classification est vide par défaut et non par analyse."
        )

    sortie, statut_recommandations = produire_recommandations_phase(
        dossier, signaux, classification, produit, libelle_marche, langue_analyse
    )
    statuts.append(statut_recommandations)

    conditions = conditions_par_gabarit(dossier, classification)
    if sortie is not None:
        conditions = _dedupliquer(
            conditions + list(sortie.conditions_reexamen), MAX_CONDITIONS_REEXAMEN
        )
    else:
        conditions = _dedupliquer(conditions, MAX_CONDITIONS_REEXAMEN)

    synthese = (
        sortie.synthese_executive.strip()
        if sortie is not None and sortie.synthese_executive.strip()
        else _synthese_de_repli(classification, dossier, produit)
    )

    if classification.phase_probable is None:
        limites.append(
            "Aucune phase n'a pu être retenue : les signaux disponibles ne "
            "départagent pas les hypothèses. Aucune recommandation de phase n'est "
            "produite — seules des conditions de réexamen le sont."
        )
    elif classification.incertitude == INCERTITUDE_ELEVEE:
        limites.append(
            f"Incertitude élevée sur la phase « {classification.phase_probable} » : "
            f"les deux premières hypothèses sont trop proches, ou une famille de "
            f"signaux structurante est non évaluable. La phase est indicative."
        )

    resultat = ResultatPLC(
        produit=produit,
        marche=marche,
        horodatage_utc=_horodatage(),
        sources_utilisees=sources,
        alertes_coherence=alertes,
        declenchement=declenchement,
        dossier_plc=dossier,
        signaux=signaux,
        classification=classification,
        recommandations_phase=list(sortie.recommandations) if sortie else [],
        conditions_reexamen=conditions,
        faits_cles=_faits_cles(dossier),
        synthese_executive=synthese,
        statuts_analyse=statuts,
        donnees_suffisantes=bool(dossier.familles_disponibles()),
        confiance_globale=_confiance_globale(dossier, classification),
        limites=_dedupliquer(limites, 200),
        hypotheses=list(HYPOTHESES_SYSTEMATIQUES),
    )
    if resultat.classification is not None:
        resultat.classification.regle_appliquee = enoncer_regle()
        resultat.classification.statut_regle = STATUT_REGLE

    resultat, statuts_validation, alertes_validation = valider(resultat, dossier)
    resultat.statuts_analyse.extend(statuts_validation)
    resultat.alertes_coherence.extend(alertes_validation)
    return resultat
