"""Grille de potentiel et verdict.

Le partage des rôles est la garantie centrale de ce module :

- **le modèle note** chaque critère de la grille, avec justification et
  fondements référencés ;
- **le code corrige** ces notes (critère noté malgré une entrée absente,
  plafonnement « effet de mode ») ;
- **le code décide** : `appliquer_regle` est une fonction pure, déterministe et
  rejouable. À grille identique, verdict identique, sans appel réseau.

⚠️ La règle implémentée est une **hypothèse de travail** : ni le cahier des
charges ni la spécification fonctionnelle ne définissent le « potentiel
commercial ». Ses seuils vivent dans `config.py` et sont destinés à être
recalibrés. Chaque sortie porte `statut_regle="hypothese_de_travail_a_valider"`.
"""

from __future__ import annotations

import json

from langchain_core.prompts import ChatPromptTemplate

from config import (
    CONFIANCE_ELEVEE,
    CONFIANCE_FAIBLE,
    CONFIANCE_MOYENNE,
    CRITERE_DEMANDE,
    CRITERE_DIFFERENCIATION,
    DEFINITION_SCORES,
    GRILLE_CRITERES,
    IDS_CRITERES,
    MAX_NON_EVALUABLES_POSITIF,
    MIN_CRITERES_EVALUES,
    MOTIF_PLAFONNEMENT_MODE,
    PLAFOND_DEMANDE_SI_EFFET_DE_MODE,
    SCORE_NON_EVALUABLE,
    SCORES_POSSIBLES,
    SEUIL_NEGATIF,
    SEUIL_POSITIF,
    STATUT_REGLE,
    VERDICT_INDETERMINE,
    VERDICT_NEGATIF,
    VERDICT_POSITIF,
    construire_modele,
    invoquer_structure,
    logger,
)
from schemas import (
    DossierSynthese,
    FicheProduit,
    GrilleNotee,
    NoteCritere,
    QualiteDonnees,
    SortieConditionsReexamen,
    StatutAnalyse,
    VerdictPotentiel,
)

PHASE_NOTATION: str = "notation_grille"
PHASE_CONDITIONS: str = "conditions_reexamen"

_SYSTEME_NOTATION = (
    "Tu es analyste stratégique. Tu notes une grille de potentiel commercial à "
    "partir d'un DOSSIER DE SYNTHÈSE et de rien d'autre.\n\n"
    "Produit étudié : {produit_nom}\n"
    "Description : {produit_description}\n"
    "Marché : {marche}\n"
    "Langue d'analyse : {langue_analyse} — rédige tout dans cette langue.\n\n"
    "Barème : " + DEFINITION_SCORES + ".\n\n"
    "Consignes impératives :\n"
    "- Note CHACUN des critères listés, en recopiant son identifiant à l'identique.\n"
    "- Si les données nécessaires à un critère sont absentes ou trop pauvres, mets "
    "`non_evaluable=true` et `score=null`. **C'est une réponse attendue, pas un "
    "échec** : une entrée manquante rend un critère non évaluable, elle ne le rend "
    "pas mauvais. Ne compense jamais une absence par une note moyenne.\n"
    "- Chaque note porte au moins un fondement. Un fondement de type « fait » DOIT "
    "citer une `ref` EXACTE du dossier ; sans ref valide, utilise « hypothese ».\n"
    "- N'utilise AUCUNE connaissance extérieure au dossier : ni fait de marché "
    "mémorisé, ni notoriété de marque, ni ordre de grandeur supposé.\n"
    "- La justification dit ce qui fonde la note ET ce qui manquerait pour la "
    "rendre plus sûre.\n"
    "- Ne propose aucun verdict global : ce n'est pas ton rôle, le code le calcule."
    "{erreur_precedente}"
)

_HUMAIN_NOTATION = (
    "CRITÈRES À NOTER\n{criteres}\n\n"
    "DOSSIER DE SYNTHÈSE\n{dossier}\n\n"
    "RÉFÉRENCES CITABLES (toute autre ref sera rejetée)\n{refs}"
)

_SYSTEME_CONDITIONS = (
    "Tu es analyste stratégique. Le verdict de potentiel a été calculé par une "
    "règle déterministe. Tu rédiges les CONDITIONS DE RÉEXAMEN : ce qui devrait "
    "changer, et être observé, pour justifier de reprendre la décision.\n\n"
    "Produit : {produit_nom}\n"
    "Verdict calculé : {verdict} (score {score_total} sur "
    "{nb_criteres_evalues} critère(s) évalué(s))\n"
    "Langue d'analyse : {langue_analyse}.\n\n"
    "Consignes impératives :\n"
    "- 3 à 6 conditions, chacune OBSERVABLE et vérifiable : un seuil, un signal "
    "mesurable, une collecte à relancer. Pas d'intention vague.\n"
    "- Appuie-toi sur les critères faibles ou non évaluables de la grille.\n"
    "- N'invente aucun chiffre absent de la grille fournie.\n"
    "- **Ne propose JAMAIS de convertir une devise ni d'obtenir un taux de change.** "
    "Deux prix libellés dans des devises différentes décrivent deux marchés "
    "différents, pas le même montant : le remède à un benchmark manquant dans une "
    "devise est de COLLECTER des prix dans cette devise et cette région, jamais de "
    "convertir ceux d'une autre.\n"
    "- Ne remets pas en cause le verdict : il est calculé par le code."
    "{erreur_precedente}"
)

_HUMAIN_CONDITIONS = "GRILLE NOTÉE\n{grille}\n\nRÈGLE APPLIQUÉE\n{regle}"


def enoncer_regle() -> str:
    """Énonce littéralement la règle de verdict avec ses seuils effectifs.

    Returns:
        L'énoncé de la règle, tel que publié dans `regle_appliquee`.
    """
    return (
        f"Règle appliquée par le code (HYPOTHÈSE DE TRAVAIL, non validée) : "
        f"si moins de {MIN_CRITERES_EVALUES} critères sont évalués, le verdict est "
        f"« {VERDICT_INDETERMINE} » d'office. Sinon, verdict « {VERDICT_POSITIF} » si "
        f"score_total ≥ {SEUIL_POSITIF} ET aucun critère noté 0 ET au plus "
        f"{MAX_NON_EVALUABLES_POSITIF} critère non évaluable. Verdict "
        f"« {VERDICT_NEGATIF} » si score_total ≤ {SEUIL_NEGATIF} OU si les critères "
        f"« {CRITERE_DEMANDE} » et « {CRITERE_DIFFERENCIATION} » sont tous deux notés 0. "
        f"Sinon « {VERDICT_INDETERMINE} ». Barème par critère : {DEFINITION_SCORES}. "
        f"Plafond « effet de mode » : le critère « {CRITERE_DEMANDE} » est ramené à "
        f"{PLAFOND_DEMANDE_SI_EFFET_DE_MODE} au maximum lorsque la source Tendances "
        f"signale un effet de mode."
    )


def noter_grille(
    dossier: DossierSynthese,
    produit: FicheProduit,
    marche: str,
    langue_analyse: str,
) -> tuple[list[NoteCritere], StatutAnalyse]:
    """Fait noter la grille de potentiel par le modèle.

    Args:
        dossier: Dossier de synthèse, seul contenu transmis.
        produit: Fiche du produit étudié.
        marche: Libellé du marché.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le couple `(notes, statut)`. Les notes sont vides en cas d'échec.
    """
    modele = construire_modele()
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_NOTATION), ("human", _HUMAIN_NOTATION)]
    )
    chaine = gabarit | modele.with_structured_output(GrilleNotee)

    criteres = [
        {
            "id": critere["id"],
            "intitule": critere["intitule"],
            "question": critere["question"],
            "fonde_sur": critere["fonde_sur"],
            "sources_attendues": list(critere["sources_attendues"]),
        }
        for critere in GRILLE_CRITERES
    ]
    resultat, tentatives, erreur = invoquer_structure(
        chaine,
        {
            "produit_nom": produit.nom,
            "produit_description": produit.description,
            "marche": marche,
            "langue_analyse": langue_analyse,
            "criteres": json.dumps(criteres, ensure_ascii=False, indent=1),
            "dossier": dossier.model_dump_json(indent=1),
            "refs": json.dumps(sorted(dossier.references()), ensure_ascii=False, indent=1),
        },
        PHASE_NOTATION,
    )
    notes = resultat.notes if resultat is not None else []
    return notes, StatutAnalyse(
        phase=PHASE_NOTATION,
        succes=resultat is not None,
        message_erreur=erreur,
        nb_elements=len(notes),
        nb_tentatives=tentatives,
    )


def corriger_notes(
    notes: list[NoteCritere],
    dossier: DossierSynthese,
    entrees_absentes: set[str],
    motif_plafond: str | None,
) -> tuple[list[NoteCritere], list[str]]:
    """Corrige les notes par le code avant application de la règle.

    Trois corrections déterministes :

    1. un critère absent de la réponse est ajouté comme non évaluable ;
    2. un critère noté alors que toutes ses sources sont absentes est **forcé**
       en non évaluable — un modèle qui note sans données invente ;
    3. le plafond « effet de mode » est appliqué au critère de demande.

    Args:
        notes: Notes proposées par le modèle.
        dossier: Dossier de synthèse.
        entrees_absentes: Entrées non chargées.
        motif_plafond: Motif de plafonnement, ou `None`.

    Returns:
        Le couple `(notes_corrigées, corrections_décrites)`.
    """
    corrections: list[str] = []
    par_id: dict[str, NoteCritere] = {}
    for note in notes:
        identifiant = (note.critere or "").strip()
        if identifiant not in IDS_CRITERES:
            corrections.append(f"critère inconnu « {identifiant} » écarté")
            continue
        if identifiant in par_id:
            corrections.append(f"note en double pour « {identifiant} » écartée")
            continue
        par_id[identifiant] = note

    resultat: list[NoteCritere] = []
    for critere in GRILLE_CRITERES:
        identifiant = critere["id"]
        note = par_id.get(identifiant)
        if note is None:
            resultat.append(
                NoteCritere(
                    critere=identifiant,
                    score=None,
                    non_evaluable=True,
                    justification=(
                        "Critère non noté par la chaîne de notation : traité comme "
                        "non évaluable par le code."
                    ),
                )
            )
            corrections.append(f"critère « {identifiant} » manquant → non évaluable")
            continue

        sources_absentes = set(critere["sources_attendues"]) & entrees_absentes
        toutes_absentes = sources_absentes == set(critere["sources_attendues"])

        if toutes_absentes and not note.non_evaluable:
            note.non_evaluable = True
            note.score = None
            note.justification = (
                f"Forcé non évaluable par le code : l'entrée "
                f"{', '.join(sorted(sources_absentes))} est absente, aucune donnée ne "
                f"peut fonder ce critère. "
            ) + note.justification
            corrections.append(
                f"critère « {identifiant} » noté malgré l'absence de "
                f"{', '.join(sorted(sources_absentes))} → forcé non évaluable"
            )

        if note.non_evaluable:
            note.score = None
        elif note.score not in SCORES_POSSIBLES:
            corrections.append(
                f"score invalide ({note.score}) pour « {identifiant} » → non évaluable"
            )
            note.non_evaluable = True
            note.score = None

        if (
            identifiant == CRITERE_DEMANDE
            and motif_plafond is not None
            and note.score is not None
            and note.score > PLAFOND_DEMANDE_SI_EFFET_DE_MODE
        ):
            ancien = note.score
            note.score = PLAFOND_DEMANDE_SI_EFFET_DE_MODE
            note.plafonnement_applique = motif_plafond
            note.justification += (
                f" [Plafonnement appliqué par le code : note ramenée de {ancien} à "
                f"{PLAFOND_DEMANDE_SI_EFFET_DE_MODE} — "
                f"{dossier.demande.motif_effet_de_mode if dossier.demande else motif_plafond}. "
                f"Une demande portée par un effet de mode ne peut pas être créditée "
                f"d'une dynamique établie.]"
            )
            corrections.append(
                f"critère « {identifiant} » plafonné à "
                f"{PLAFOND_DEMANDE_SI_EFFET_DE_MODE} ({motif_plafond})"
            )

        resultat.append(note)
    return resultat, corrections


def _confiance_verdict(qualite: QualiteDonnees, nb_evalues: int) -> str:
    """Dérive la confiance du verdict de la qualité des entrées.

    Args:
        qualite: Qualité des entrées.
        nb_evalues: Nombre de critères effectivement évalués.

    Returns:
        « elevee », « moyenne » ou « faible ».
    """
    if qualite.nb_entrees_presentes < len(qualite.entrees) or nb_evalues < len(
        IDS_CRITERES
    ):
        return CONFIANCE_FAIBLE
    if qualite.nb_entrees_degradees:
        return CONFIANCE_MOYENNE
    return CONFIANCE_ELEVEE


def appliquer_regle(
    notes: list[NoteCritere], qualite: QualiteDonnees | None = None
) -> VerdictPotentiel:
    """Applique la règle de verdict de manière déterministe et auditable.

    Le modèle note ; le code décide. Fonction pure : aucun appel réseau, aucun
    état, aucun aléa — à grille identique, verdict identique.

    RÈGLE = **HYPOTHÈSE DE TRAVAIL** (voir `config.py`).

    Args:
        notes: Grille notée et corrigée.
        qualite: Qualité des entrées, pour dériver la confiance du verdict.

    Returns:
        Le verdict complet, `declenche_plc` compris.
    """
    evaluees = [n for n in notes if not n.non_evaluable and n.score is not None]
    non_evaluables = [n for n in notes if n.non_evaluable or n.score is None]
    score_total = sum(n.score or 0 for n in evaluees)
    nb_evalues = len(evaluees)
    scores_par_critere = {n.critere: n.score for n in evaluees}

    aucun_zero = all((n.score or 0) > 0 for n in evaluees)
    demande_nulle = scores_par_critere.get(CRITERE_DEMANDE) == 0
    differenciation_nulle = scores_par_critere.get(CRITERE_DIFFERENCIATION) == 0

    if nb_evalues < MIN_CRITERES_EVALUES:
        verdict = VERDICT_INDETERMINE
    elif (
        score_total >= SEUIL_POSITIF
        and aucun_zero
        and len(non_evaluables) <= MAX_NON_EVALUABLES_POSITIF
    ):
        verdict = VERDICT_POSITIF
    elif score_total <= SEUIL_NEGATIF or (demande_nulle and differenciation_nulle):
        verdict = VERDICT_NEGATIF
    else:
        verdict = VERDICT_INDETERMINE

    confiance = (
        _confiance_verdict(qualite, nb_evalues) if qualite is not None else CONFIANCE_FAIBLE
    )
    logger.debug(
        "verdict : %s (score %d, %d critère(s) évalué(s), %d non évaluable(s))",
        verdict,
        score_total,
        nb_evalues,
        len(non_evaluables),
    )
    return VerdictPotentiel(
        verdict=verdict,
        declenche_plc=(verdict == VERDICT_POSITIF),
        score_total=score_total,
        nb_criteres_evalues=nb_evalues,
        grille=notes,
        regle_appliquee=enoncer_regle(),
        statut_regle=STATUT_REGLE,
        confiance=confiance,
        conditions_reexamen=[],
    )


def _conditions_par_gabarit(verdict: VerdictPotentiel) -> list[str]:
    """Rédige des conditions de réexamen par gabarits, sans LLM.

    Args:
        verdict: Verdict calculé.

    Returns:
        Des conditions actionnables dérivées de la grille.
    """
    conditions: list[str] = []
    for note in verdict.grille:
        if note.non_evaluable:
            conditions.append(
                f"Rendre le critère « {note.critere} » évaluable en relançant la "
                f"collecte correspondante, puis relancer cette analyse."
            )
        elif note.score == 0:
            conditions.append(
                f"Observer un renversement documenté du critère « {note.critere} », "
                f"aujourd'hui noté 0 : sans lui, le verdict ne peut pas évoluer."
            )
        elif note.plafonnement_applique:
            conditions.append(
                f"Vérifier sur un nouvel horizon si la demande se maintient après le "
                f"pic : le critère « {note.critere} » est aujourd'hui plafonné pour "
                f"cause de {note.plafonnement_applique}."
            )
    if not conditions:
        conditions.append(
            "Réexaminer si l'un des critères de la grille change de note d'au moins "
            "un point après une nouvelle collecte."
        )
    return conditions[:6]


def rediger_conditions_reexamen(
    verdict: VerdictPotentiel, produit: FicheProduit, langue_analyse: str
) -> tuple[list[str], StatutAnalyse]:
    """Rédige les conditions de réexamen du verdict.

    Args:
        verdict: Verdict calculé par le code.
        produit: Fiche du produit étudié.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le couple `(conditions, statut)`. En cas d'échec, des gabarits de code
        prennent le relais : le verdict n'est jamais livré sans conditions.
    """
    modele = construire_modele()
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_CONDITIONS), ("human", _HUMAIN_CONDITIONS)]
    )
    chaine = gabarit | modele.with_structured_output(SortieConditionsReexamen)

    resultat, tentatives, erreur = invoquer_structure(
        chaine,
        {
            "produit_nom": produit.nom,
            "langue_analyse": langue_analyse,
            "verdict": verdict.verdict,
            "score_total": verdict.score_total,
            "nb_criteres_evalues": verdict.nb_criteres_evalues,
            "grille": json.dumps(
                [n.model_dump() for n in verdict.grille], ensure_ascii=False, indent=1
            ),
            "regle": verdict.regle_appliquee,
        },
        PHASE_CONDITIONS,
    )
    if resultat is not None and resultat.conditions:
        return list(resultat.conditions[:6]), StatutAnalyse(
            phase=PHASE_CONDITIONS,
            succes=True,
            nb_elements=len(resultat.conditions),
            nb_tentatives=tentatives,
        )
    return _conditions_par_gabarit(verdict), StatutAnalyse(
        phase=PHASE_CONDITIONS,
        succes=False,
        message_erreur=(
            f"{erreur or 'aucune condition produite'} — conditions générées par "
            f"gabarits de code."
        ),
        nb_tentatives=tentatives,
    )
