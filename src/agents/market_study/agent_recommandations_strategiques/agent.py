"""Orchestration de bout en bout de l'agent Recommandations Stratégiques.

Séquence : chargement → signaux → diagnostic → potentiel (notation puis règle)
→ recommandations → post-validation → assemblage.

Invariant central : **l'agent produit toujours un verdict**. Une entrée absente
rend des critères non évaluables et dégrade la confiance ; l'échec d'une chaîne
rédactionnelle vide un bloc. Aucun de ces cas n'empêche la sortie d'être
complète et conforme au schéma. Le seul verdict impossible à produire est celui
dont la notation elle-même a échoué : il vaut alors « indetermine », grille vide,
statut en échec.
"""

from __future__ import annotations

from datetime import UTC, datetime

from chargement import charger_entrees
from config import (
    CONFIANCE_ELEVEE,
    CONFIANCE_FAIBLE,
    CONFIANCE_MOYENNE,
    ENTREE_CONCURRENCE,
    HYPOTHESES_SYSTEMATIQUES,
    LIMITES_SYSTEMATIQUES,
    MOTIF_PLAFONNEMENT_MODE,
    STATUT_REGLE,
    TYPE_RISQUE_DONNEES,
    TYPE_RISQUE_EFFET_DE_MODE,
    VERDICT_INDETERMINE,
    VERDICT_NEGATIF,
    logger,
    verifier_cle_api,
)
from diagnostic import etablir_diagnostic
from potentiel import (
    appliquer_regle,
    corriger_notes,
    enoncer_regle,
    noter_grille,
    rediger_conditions_reexamen,
)
from recommandations import (
    produire_opportunites_risques,
    produire_recommandations,
    produire_restitution,
)
from schemas import (
    ConfianceGlobale,
    DossierSynthese,
    FicheProduit,
    Fondement,
    ParametresMarche,
    ResultatRecommandations,
    Risque,
    StatutAnalyse,
    VerdictPotentiel,
)
from signaux import construire_dossier, entrees_manquantes, motif_plafonnement
from validation import valider


def _horodatage() -> str:
    """Retourne l'horodatage courant en ISO 8601 UTC.

    Returns:
        Un horodatage à la seconde, suffixé « Z ».
    """
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _confiance_globale(dossier: DossierSynthese, verdict: VerdictPotentiel) -> ConfianceGlobale:
    """Détermine la confiance globale de l'analyse.

    Args:
        dossier: Dossier de synthèse.
        verdict: Verdict calculé.

    Returns:
        Le niveau de confiance et sa justification.
    """
    qualite = dossier.qualite_donnees
    facteurs = [
        f"{qualite.nb_entrees_presentes} entrée(s) sur {len(qualite.entrees)} présente(s)",
        f"{verdict.nb_criteres_evalues} critère(s) évalué(s) sur {len(verdict.grille)}",
    ]
    for entree in qualite.entrees:
        facteurs.extend(f"[{entree.entree}] {a}" for a in entree.avertissements)

    if qualite.nb_entrees_presentes < len(qualite.entrees):
        manquantes = [e.entree for e in qualite.entrees if not e.presente]
        niveau = CONFIANCE_FAIBLE
        justification = (
            f"Entrée(s) manquante(s) : {', '.join(manquantes)}. Le verdict repose sur "
            f"une vue partielle du marché."
        )
    elif qualite.nb_entrees_degradees:
        niveau = CONFIANCE_MOYENNE
        justification = (
            f"{qualite.nb_entrees_degradees} entrée(s) dégradée(s) (données "
            f"insuffisantes ou confiance amont faible) : la qualité de ces "
            f"recommandations est bornée par celle des analyses amont."
        )
    else:
        niveau = CONFIANCE_ELEVEE
        justification = (
            "Les trois entrées sont présentes et déclarent des données suffisantes. "
            "La règle de verdict reste néanmoins une hypothèse de travail."
        )
    return ConfianceGlobale(niveau=niveau, justification=justification, facteurs=facteurs)


def _risque_donnees(dossier: DossierSynthese) -> Risque | None:
    """Fabrique le risque « données » lorsque des entrées manquent.

    Le prompt l'exige déjà du modèle ; ce filet garantit sa présence même si la
    chaîne échoue ou l'oublie — décider sur données incomplètes est un risque
    qui ne doit jamais disparaître de la sortie.

    Args:
        dossier: Dossier de synthèse.

    Returns:
        Le risque, ou `None` si toutes les entrées sont présentes et saines.
    """
    qualite = dossier.qualite_donnees
    manquantes = [e.entree for e in qualite.entrees if not e.presente]
    degradees = [
        e.entree
        for e in qualite.entrees
        if e.presente and (not e.donnees_suffisantes or e.confiance_heritee == CONFIANCE_FAIBLE)
    ]
    if not manquantes and not degradees:
        return None
    morceaux = []
    if manquantes:
        morceaux.append(f"entrée(s) absente(s) : {', '.join(manquantes)}")
    if degradees:
        morceaux.append(f"entrée(s) dégradée(s) : {', '.join(degradees)}")
    return Risque(
        libelle="Décision fondée sur des données incomplètes",
        type=TYPE_RISQUE_DONNEES,
        gravite="elevee" if manquantes else "moyenne",
        fondements=[
            Fondement(
                type="hypothese",
                ref=None,
                detail=(
                    "Constat du code sur la qualité des entrées, hors chaîne LLM : "
                    + " ; ".join(morceaux)
                ),
            )
        ],
        attenuation=(
            "Relancer les collecteurs concernés et rejouer cette analyse avant tout "
            "engagement significatif. Les critères correspondants de la grille sont "
            "non évaluables : le verdict est structurellement incomplet."
        ),
    )


def _risque_effet_de_mode(dossier: DossierSynthese) -> Risque:
    """Fabrique le risque « effet de mode » exigé par la règle.

    Args:
        dossier: Dossier de synthèse.

    Returns:
        Le risque, prêt à être ajouté s'il manque.
    """
    motif = dossier.demande.motif_effet_de_mode if dossier.demande else ""
    return Risque(
        libelle="Demande portée par un effet de mode",
        type=TYPE_RISQUE_EFFET_DE_MODE,
        gravite="elevee",
        fondements=[
            Fondement(
                type="fait",
                ref="tendances.indicateurs.profil_courbe",
                detail=f"Drapeau posé par le code : {motif}.",
            )
        ],
        attenuation=(
            "Limiter les engagements de stock et la durée des contrats, prévoir un "
            "critère d'arrêt chiffré, et réévaluer la demande sur un nouvel horizon "
            "avant tout réassort. Le critère « demande » de la grille est plafonné "
            "pour ce motif."
        ),
    )


def _synthese_de_repli(verdict: VerdictPotentiel, produit: FicheProduit) -> str:
    """Rédige une synthèse minimale, sans LLM.

    Args:
        verdict: Verdict calculé.
        produit: Fiche du produit étudié.

    Returns:
        Une synthèse factuelle de quelques lignes.
    """
    lignes = [
        f"Produit : {produit.nom}.",
        f"Verdict calculé par la règle : **{verdict.verdict}** "
        f"(score {verdict.score_total} sur {verdict.nb_criteres_evalues} critère(s) "
        f"évalué(s), confiance {verdict.confiance}).",
        f"Déclenchement du module aval : {'oui' if verdict.declenche_plc else 'non'}.",
    ]
    for note in verdict.grille:
        etat = "non évaluable" if note.non_evaluable else f"note {note.score}"
        plafond = (
            f", plafonné ({note.plafonnement_applique})" if note.plafonnement_applique else ""
        )
        lignes.append(f"- {note.critere} : {etat}{plafond}")
    lignes.append(
        "La rédaction analytique n'a pas pu être produite (chaînes en échec) : "
        "seuls le verdict et sa grille sont livrés."
    )
    lignes.append(
        "Rappel : la règle de verdict est une hypothèse de travail non validée."
    )
    return "\n".join(lignes)


def recommander(
    chemin_insights: str | None,
    chemin_concurrence: str | None,
    chemin_tendances: str | None,
    langue_analyse: str,
) -> ResultatRecommandations:
    """Produit le diagnostic croisé, le verdict et les recommandations.

    Args:
        chemin_insights: Sortie de F3, ou `None`.
        chemin_concurrence: Sortie de F4, ou `None`.
        chemin_tendances: Sortie du collecteur Tendances, ou `None`.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le résultat validé.

    Raises:
        ErreurCoherenceProduit: Si les fichiers portent des produits différents.
        RuntimeError: Si la clé API est absente alors qu'une entrée exploitable
            a été chargée.
    """
    entrees, sources, alertes, qualites = charger_entrees(
        chemin_insights, chemin_concurrence, chemin_tendances
    )
    produit = entrees.produit or FicheProduit(nom="inconnu", description="")
    marche = entrees.marche or ParametresMarche(geo="??", langue=langue_analyse)
    libelle_marche = f"{marche.geo} ({marche.langue})"

    dossier = construire_dossier(entrees, qualites)
    absentes = entrees_manquantes(dossier)
    motif_plafond = motif_plafonnement(dossier)

    limites = list(entrees.limites_amont)
    limites.extend(LIMITES_SYSTEMATIQUES)
    for entree in qualites:
        for avertissement in entree.avertissements:
            limites.append(f"[{entree.entree}] {avertissement}")

    for compte_rendu in sources:
        for entree in qualites:
            if entree.entree == compte_rendu.source and entree.presente:
                compte_rendu.nb_items_exploites = compte_rendu.nb_items_charges

    if not entrees.au_moins_une():
        logger.warning("aucune entrée exploitable")
        return ResultatRecommandations(
            produit=produit,
            marche=marche,
            horodatage_utc=_horodatage(),
            sources_utilisees=sources,
            alertes_coherence=alertes,
            dossier_synthese=dossier,
            verdict_potentiel=VerdictPotentiel(
                verdict=VERDICT_INDETERMINE,
                declenche_plc=False,
                regle_appliquee=enoncer_regle(),
                statut_regle=STATUT_REGLE,
                confiance=CONFIANCE_FAIBLE,
                conditions_reexamen=[
                    "Fournir au moins une des trois analyses amont (Tendances, "
                    "Insights consommateurs, Analyse concurrentielle)."
                ],
            ),
            synthese_executive=(
                f"Aucune analyse amont exploitable n'a été fournie pour "
                f"« {produit.nom} » : aucun verdict fondé n'est possible."
            ),
            donnees_suffisantes=False,
            confiance_globale=ConfianceGlobale(
                niveau=CONFIANCE_FAIBLE,
                justification="Aucune entrée exploitable.",
            ),
            limites=limites,
            hypotheses=list(HYPOTHESES_SYSTEMATIQUES),
        )

    verifier_cle_api()
    statuts: list[StatutAnalyse] = []

    # --- Diagnostic croisé -------------------------------------------------- #
    diagnostic, statut_diagnostic = etablir_diagnostic(
        dossier, absentes, produit, libelle_marche, langue_analyse
    )
    statuts.append(statut_diagnostic)

    # --- Notation puis règle ------------------------------------------------ #
    notes, statut_notation = noter_grille(dossier, produit, libelle_marche, langue_analyse)
    statuts.append(statut_notation)

    if not notes:
        verdict = VerdictPotentiel(
            verdict=VERDICT_INDETERMINE,
            declenche_plc=False,
            grille=[],
            regle_appliquee=enoncer_regle(),
            statut_regle=STATUT_REGLE,
            confiance=CONFIANCE_FAIBLE,
        )
        limites.append(
            "La chaîne de notation de la grille a échoué : aucun critère n'a pu être "
            "noté, le verdict est indéterminé par défaut et non par analyse."
        )
    else:
        notes, corrections = corriger_notes(notes, dossier, absentes, motif_plafond)
        if corrections:
            statuts.append(
                StatutAnalyse(
                    phase="correction_grille",
                    succes=True,
                    message_erreur="; ".join(corrections[:8]),
                    nb_elements=len(corrections),
                )
            )
        verdict = appliquer_regle(notes, dossier.qualite_donnees)

    conditions, statut_conditions = rediger_conditions_reexamen(
        verdict, produit, langue_analyse
    )
    verdict.conditions_reexamen = conditions
    statuts.append(statut_conditions)

    # --- Recommandations ---------------------------------------------------- #
    recommandations, statut_recommandations = produire_recommandations(
        dossier, diagnostic, verdict, produit, libelle_marche, langue_analyse
    )
    statuts.append(statut_recommandations)

    opportunites_risques, statut_opportunites = produire_opportunites_risques(
        dossier, diagnostic, verdict, produit, langue_analyse
    )
    statuts.append(statut_opportunites)

    restitution, statut_restitution = produire_restitution(
        dossier,
        diagnostic,
        verdict,
        recommandations,
        opportunites_risques,
        produit,
        langue_analyse,
    )
    statuts.append(statut_restitution)

    # --- Assemblage --------------------------------------------------------- #
    risques = list(opportunites_risques.risques) if opportunites_risques else []
    if motif_plafond == MOTIF_PLAFONNEMENT_MODE and not any(
        r.type == TYPE_RISQUE_EFFET_DE_MODE for r in risques
    ):
        risques.insert(0, _risque_effet_de_mode(dossier))
        statuts.append(
            StatutAnalyse(
                phase="post_traitement",
                succes=True,
                message_erreur=(
                    "risque « effet_de_mode » ajouté par le code : le drapeau est posé "
                    "mais la chaîne ne l'avait pas produit"
                ),
                nb_elements=1,
            )
        )
    risque_donnees = _risque_donnees(dossier)
    if risque_donnees is not None and not any(r.type == TYPE_RISQUE_DONNEES for r in risques):
        risques.append(risque_donnees)

    prix = recommandations.recommandation_prix if recommandations else None
    if ENTREE_CONCURRENCE in absentes:
        prix = None
        limites.append(
            "Aucune analyse concurrentielle fournie : aucun benchmark de prix n'est "
            "disponible, la recommandation de prix est donc nulle. Recommander un "
            "prix sans référence de marché n'aurait aucune valeur."
        )

    donnees_a_completer = list(recommandations.donnees_a_completer) if recommandations else []
    if verdict.verdict == VERDICT_INDETERMINE and not donnees_a_completer:
        donnees_a_completer = [
            f"Relancer l'agent « {entree} » : son absence rend non évaluables les "
            f"critères qui en dépendent, ce qui empêche mécaniquement tout verdict "
            f"tranché."
            for entree in sorted(absentes)
        ] or [
            "Approfondir les critères notés 0 ou 1 : en l'état, la grille ne franchit "
            "aucun des deux seuils de la règle."
        ]

    if verdict.verdict == VERDICT_NEGATIF:
        limites.append(
            "Verdict négatif : les recommandations livrées se limitent à "
            "l'essentiel défendable et à des pivots, conformément à la règle."
        )

    resultat = ResultatRecommandations(
        produit=produit,
        marche=marche,
        horodatage_utc=_horodatage(),
        sources_utilisees=sources,
        alertes_coherence=alertes,
        dossier_synthese=dossier,
        diagnostic=diagnostic,
        verdict_potentiel=verdict,
        recommandations_produit=(
            recommandations.recommandations_produit if recommandations else []
        ),
        recommandation_prix=prix,
        recommandation_positionnement=(
            recommandations.recommandation_positionnement if recommandations else None
        ),
        recommandations_marketing=(
            recommandations.recommandations_marketing if recommandations else []
        ),
        opportunites=opportunites_risques.opportunites if opportunites_risques else [],
        risques=risques,
        donnees_a_completer=donnees_a_completer,
        faits_cles=restitution.faits_cles if restitution else [],
        hypotheses_globales=(
            restitution.hypotheses_globales
            if restitution
            else [
                "La règle de verdict appliquée est une hypothèse de travail non validée."
            ]
        ),
        synthese_executive=(
            restitution.synthese_executive
            if restitution and restitution.synthese_executive.strip()
            else _synthese_de_repli(verdict, produit)
        ),
        statuts_analyse=statuts,
        donnees_suffisantes=any(
            e.presente and e.donnees_suffisantes for e in dossier.qualite_donnees.entrees
        ),
        confiance_globale=_confiance_globale(dossier, verdict),
        limites=limites,
        hypotheses=list(HYPOTHESES_SYSTEMATIQUES),
    )

    resultat, statuts_validation, alertes_validation = valider(resultat, dossier)
    resultat.statuts_analyse.extend(statuts_validation)
    resultat.alertes_coherence.extend(alertes_validation)
    return resultat
