"""Recommandations **dédiées à la phase classée** et synthèse exécutive.

Cet agent ne produit jamais de recommandation généraliste produit / prix /
positionnement / marketing : c'est le rôle de F5. Il produit uniquement ce que la
phase de cycle de vie retenue impose ou déconseille, et le dit explicitement.

Si aucune phase n'est retenue, aucune recommandation de phase n'est produite :
des conditions de réexamen substantielles prennent leur place. Recommander sur
une phase inconnue reviendrait à recommander au hasard.
"""

from __future__ import annotations

import json

from langchain_core.prompts import ChatPromptTemplate

from config import (
    ANGLES_PAR_PHASE,
    DOMAINE_PLC,
    GRILLE_LECTURE,
    MAX_CONDITIONS_REEXAMEN,
    MAX_RECOMMANDATIONS_PHASE,
    MIN_RECOMMANDATIONS_PHASE,
    PIEGES_OPPOSABLES,
    construire_modele,
    invoquer_structure,
)
from schemas import (
    Classification,
    DossierPLC,
    FicheProduit,
    OrientationSignal,
    SortieRecommandationsPhase,
    StatutAnalyse,
)

PHASE_RECOMMANDATIONS: str = "recommandations_phase"

_SYSTEME = (
    "Tu es consultant en stratégie e-commerce. Une phase de cycle de vie de marché "
    "a été CLASSÉE PAR UNE RÈGLE DÉTERMINISTE à partir de signaux temporels. Tu "
    "rédiges ce que cette phase — et elle seule — impose.\n\n"
    "Produit étudié : {produit_nom}\n"
    "Description : {produit_description}\n"
    "Marché : {marche}\n"
    "Phase classée : {phase} (incertitude {incertitude})\n"
    "Verdict de potentiel amont : {verdict_amont} — tu ne le commentes pas, tu ne "
    "le contredis pas, tu ne le recalcules pas.\n"
    "Langue d'analyse : {langue_analyse} — rédige tout dans cette langue.\n\n"
    + GRILLE_LECTURE
    + "\n\n"
    + PIEGES_OPPOSABLES
    + "\n\n"
    "Consignes impératives :\n"
    f"- Produis {MIN_RECOMMANDATIONS_PHASE} à {MAX_RECOMMANDATIONS_PHASE} "
    "recommandations, **toutes spécifiques à la phase classée** : l'énoncé de "
    "chacune NOMME la phase et explique ce qu'elle change.\n"
    "- INTERDIT : toute recommandation généraliste de produit, de prix, de "
    "positionnement ou de marketing qui vaudrait quelle que soit la phase. Ces "
    "recommandations-là relèvent d'un autre agent et seraient un doublon.\n"
    "- Chaque recommandation porte au moins un fondement de type « fait » citant "
    "une `ref` EXACTE du dossier de signaux. Sans ref valide, utilise « hypothese ».\n"
    "- Ces recommandations COMPLÈTENT les recommandations stratégiques amont, elles "
    "ne les contredisent pas. En cas de tension apparente, dis-le dans la "
    "justification plutôt que de la gommer.\n"
    "- Angles indicatifs pour cette phase (à choisir et adapter, jamais à recopier "
    "mécaniquement) : {angles}\n"
    "- L'incertitude de la classification doit se refléter dans la modalisation : "
    "une incertitude élevée interdit toute formulation catégorique.\n"
    "- Rédige aussi 3 à {max_conditions} conditions de réexamen OBSERVABLES : quel "
    "signal manquant ou contradictoire réobserver, et sur quelle durée.\n"
    "- Rédige enfin une synthèse exécutive de 10 lignes maximum : la phase, ce qui "
    "la fonde, ce qu'elle implique, ce qui reste incertain.\n"
    "- N'utilise AUCUNE connaissance extérieure au dossier et n'invente aucun "
    "chiffre. Ne généralise jamais le corpus à une population."
    "{erreur_precedente}"
)

_SYSTEME_SANS_PHASE = (
    "Tu es consultant en stratégie e-commerce. Une classification de phase de cycle "
    "de vie a été tentée à partir de signaux temporels : **elle n'a pas abouti**. "
    "Aucune phase ne peut être retenue.\n\n"
    "Produit étudié : {produit_nom}\n"
    "Description : {produit_description}\n"
    "Marché : {marche}\n"
    "Phase classée : {phase} (incertitude {incertitude})\n"
    "Verdict de potentiel amont : {verdict_amont} — tu ne le commentes pas.\n"
    "Langue d'analyse : {langue_analyse} — rédige tout dans cette langue.\n\n"
    + PIEGES_OPPOSABLES
    + "\n\n"
    "Consignes impératives :\n"
    "- Ne produis AUCUNE recommandation : laisse la liste `recommandations` vide. "
    "Recommander sur une phase inconnue reviendrait à recommander au hasard.\n"
    "- Produis en revanche 4 à {max_conditions} conditions de réexamen SUBSTANTIELLES "
    "et OBSERVABLES : quelle donnée manque, où la collecter, quel seuil ou quel "
    "signal permettrait de trancher, sur quelle durée l'observer.\n"
    "- Rédige une synthèse exécutive de 10 lignes maximum expliquant pourquoi la "
    "classification n'aboutit pas et ce qu'il faudrait pour qu'elle aboutisse.\n"
    "- Appuie-toi sur les familles non évaluables du dossier. N'invente aucun "
    "chiffre et n'utilise aucune connaissance extérieure au dossier.\n"
    "- Angles indicatifs (sans objet ici, fournis pour information) : {angles}"
    "{erreur_precedente}"
)

_HUMAIN = (
    "DOSSIER DE SIGNAUX\n{dossier}\n\n"
    "ORIENTATIONS RETENUES\n{orientations}\n\n"
    "CLASSIFICATION CALCULÉE PAR LE CODE\n{classification}\n\n"
    "RÉFÉRENCES CITABLES (toute autre ref sera rejetée)\n{refs}"
)


def produire_recommandations_phase(
    dossier: DossierPLC,
    signaux: list[OrientationSignal],
    classification: Classification,
    produit: FicheProduit,
    marche: str,
    langue_analyse: str,
) -> tuple[SortieRecommandationsPhase | None, StatutAnalyse]:
    """Produit les recommandations dédiées à la phase, ou les conditions à défaut.

    Args:
        dossier: Dossier PLC, seul contenu transmis.
        signaux: Orientations corrigées.
        classification: Classification calculée par le code.
        produit: Fiche du produit étudié.
        marche: Libellé du marché.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le couple `(sortie_ou_None, statut)`.
    """
    phase = classification.phase_probable
    modele = construire_modele()
    systeme = _SYSTEME if phase is not None else _SYSTEME_SANS_PHASE
    gabarit = ChatPromptTemplate.from_messages([("system", systeme), ("human", _HUMAIN)])
    chaine = gabarit | modele.with_structured_output(SortieRecommandationsPhase)

    angles = ANGLES_PAR_PHASE.get(phase or "", ())
    resultat, tentatives, erreur = invoquer_structure(
        chaine,
        {
            "produit_nom": produit.nom,
            "produit_description": produit.description,
            "marche": marche,
            "langue_analyse": langue_analyse,
            "phase": phase or "aucune phase retenue",
            "incertitude": classification.incertitude,
            "verdict_amont": dossier.verdict_amont or "inconnu",
            "angles": "; ".join(angles) if angles else "aucun",
            "max_conditions": MAX_CONDITIONS_REEXAMEN,
            "dossier": dossier.model_dump_json(),
            "orientations": json.dumps(
                [s.model_dump() for s in signaux], ensure_ascii=False, separators=(",", ":")
            ),
            "classification": classification.model_dump_json(),
            "refs": json.dumps(sorted(dossier.references()), ensure_ascii=False, separators=(",", ":")),
        },
        PHASE_RECOMMANDATIONS,
    )

    if resultat is not None:
        if phase is None:
            resultat.recommandations = []
        else:
            resultat.recommandations = resultat.recommandations[:MAX_RECOMMANDATIONS_PHASE]
            for rang, recommandation in enumerate(resultat.recommandations, start=1):
                recommandation.domaine = DOMAINE_PLC
                if not recommandation.id_reco.strip():
                    recommandation.id_reco = f"reco-plc-{rang}"
        resultat.conditions_reexamen = resultat.conditions_reexamen[
            :MAX_CONDITIONS_REEXAMEN
        ]

    return resultat, StatutAnalyse(
        phase=PHASE_RECOMMANDATIONS,
        succes=resultat is not None,
        message_erreur=erreur,
        nb_elements=len(resultat.recommandations) if resultat else 0,
        nb_tentatives=tentatives,
    )
