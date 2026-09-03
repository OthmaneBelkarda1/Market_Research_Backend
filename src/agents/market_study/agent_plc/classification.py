"""Orientation des familles de signaux par le modèle, **agrégation par le code**.

Le partage des rôles est la garantie centrale de ce module :

- **le modèle oriente** chaque famille disponible vers une phase (ou « neutre »),
  avec une force et une justification référencée ;
- **le code corrige** ces orientations (famille orientée alors qu'elle est
  indisponible, force absente, phase inconnue) ;
- **le code décide** : `agreger` est une fonction pure, déterministe et
  rejouable. À orientations identiques, classification identique, sans appel
  réseau.

⚠️ La grille de lecture et les pondérations sont des **hypothèses de travail**.
Elles vivent dans `config.py` et sont destinées à être recalibrées. Chaque
sortie porte `statut_regle="hypothese_de_travail_a_valider"`.
"""

from __future__ import annotations

import json

from langchain_core.prompts import ChatPromptTemplate

from config import (
    CONFIANCE_ELEVEE,
    CONFIANCE_FAIBLE,
    CONFIANCE_MOYENNE,
    FORCE_DEFAUT,
    FORCES,
    GRILLE_LECTURE,
    IDS_FAMILLES,
    INCERTITUDE_ELEVEE,
    INCERTITUDE_FAIBLE,
    INCERTITUDE_MOYENNE,
    MAX_CONDITIONS_REEXAMEN,
    MIN_FAMILLES_EVALUEES,
    NIVEAUX_CONFIANCE,
    ORIENTATION_NEUTRE,
    PHASES,
    PIEGES_OPPOSABLES,
    POIDS_FAMILLE_STRUCTURANTE,
    POIDS_FAMILLES,
    SEUIL_ECART_ELEVEE,
    SEUIL_ECART_MOYENNE,
    STATUT_REGLE,
    VALEUR_FORCE,
    construire_modele,
    invoquer_structure,
    logger,
)
from schemas import (
    Classification,
    DossierPLC,
    FicheProduit,
    OrientationSignal,
    SortieOrientations,
    StatutAnalyse,
)

PHASE_ORIENTATION: str = "orientation_signaux"

_SYSTEME_ORIENTATION = (
    "Tu es analyste de marché. Tu lis un DOSSIER DE SIGNAUX TEMPORELS et rien "
    "d'autre, et tu ORIENTES chaque famille de signaux vers une phase de cycle de "
    "vie de marché.\n\n"
    "Produit étudié : {produit_nom}\n"
    "Description : {produit_description}\n"
    "Marché : {marche}\n"
    "Langue d'analyse : {langue_analyse} — rédige tout dans cette langue.\n\n"
    "Phases possibles : introduction, croissance, maturite, declin. Tu peux aussi "
    "répondre « neutre » si la famille ne penche vers aucune phase.\n\n"
    + GRILLE_LECTURE
    + "\n\n"
    + PIEGES_OPPOSABLES
    + "\n\n"
    "Consignes impératives :\n"
    "- Oriente CHACUNE des familles listées, en recopiant son identifiant à "
    "l'identique.\n"
    "- Si les indicateurs d'une famille sont insuffisants pour trancher, mets "
    "`non_evaluable=true`. **C'est une réponse attendue, pas un échec** : une "
    "donnée manquante ne rend pas un marché mature, elle le rend illisible.\n"
    "- La `force` dit à quel point le signal est net : faible, moyenne ou forte. "
    "Une force « forte » exige des indicateurs concordants, pas un seul chiffre.\n"
    "- Chaque orientation porte au moins un fondement. Un fondement de type "
    "« fait » DOIT citer une `ref` EXACTE du dossier ; sans ref valide, utilise "
    "« hypothese ».\n"
    "- N'utilise AUCUNE connaissance extérieure au dossier : ni fait de marché "
    "mémorisé, ni notoriété de marque, ni ordre de grandeur supposé.\n"
    "- Ne propose AUCUNE phase globale : ce n'est pas ton rôle, le code agrège et "
    "décide. Ne commente pas non plus le verdict de potentiel amont.\n"
    "- Ne généralise jamais le corpus à une population : parle des annonces, des "
    "offres et des messages collectés."
    "{erreur_precedente}"
)

_HUMAIN_ORIENTATION = (
    "FAMILLES À ORIENTER\n{familles}\n\n"
    "DOSSIER DE SIGNAUX\n{dossier}\n\n"
    "RÉFÉRENCES CITABLES (toute autre ref sera rejetée)\n{refs}"
)


def enoncer_regle() -> str:
    """Énonce littéralement la règle d'agrégation avec ses seuils effectifs.

    Returns:
        L'énoncé publié dans `regle_appliquee`.
    """
    poids = ", ".join(f"{cle} = {valeur:.2f}" for cle, valeur in POIDS_FAMILLES.items())
    forces = ", ".join(f"{cle} = {valeur}" for cle, valeur in VALEUR_FORCE.items())
    return (
        f"Règle appliquée par le code (HYPOTHÈSE DE TRAVAIL, non validée) : le score "
        f"d'une phase est la somme, sur les familles évaluées orientées vers elle, du "
        f"produit poids × valeur de force. Pondérations : {poids}. Valeurs de force : "
        f"{forces}. Une famille orientée « {ORIENTATION_NEUTRE} » n'alimente aucune "
        f"phase. `phase_probable` est la phase de score maximal ; elle vaut null si "
        f"moins de {MIN_FAMILLES_EVALUEES} familles sont évaluées, si aucune phase "
        f"n'obtient de score, ou en cas d'égalité stricte entre les deux premières. "
        f"Incertitude « {INCERTITUDE_ELEVEE} » si l'écart relatif entre la première "
        f"et la deuxième phase est inférieur à {SEUIL_ECART_ELEVEE:.2f} ou si une "
        f"famille de poids ≥ {POIDS_FAMILLE_STRUCTURANTE:.2f} est non évaluable ; "
        f"« {INCERTITUDE_MOYENNE} » si cet écart est inférieur à "
        f"{SEUIL_ECART_MOYENNE:.2f} ; « {INCERTITUDE_FAIBLE} » au-delà."
    )


def orienter_signaux(
    dossier: DossierPLC,
    produit: FicheProduit,
    marche: str,
    langue_analyse: str,
) -> tuple[list[OrientationSignal], StatutAnalyse]:
    """Fait orienter chaque famille de signaux par le modèle.

    Args:
        dossier: Dossier PLC, seul contenu transmis.
        produit: Fiche du produit étudié.
        marche: Libellé du marché.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le couple `(orientations, statut)`. La liste est vide en cas d'échec.
    """
    modele = construire_modele()
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_ORIENTATION), ("human", _HUMAIN_ORIENTATION)]
    )
    chaine = gabarit | modele.with_structured_output(SortieOrientations)

    familles = [
        {
            "id": famille.famille,
            "intitule": famille.intitule,
            "disponible": famille.disponible,
            "nb_indicateurs": len(famille.indicateurs),
        }
        for famille in dossier.familles
    ]
    resultat, tentatives, erreur = invoquer_structure(
        chaine,
        {
            "produit_nom": produit.nom,
            "produit_description": produit.description,
            "marche": marche,
            "langue_analyse": langue_analyse,
            "familles": json.dumps(familles, ensure_ascii=False, separators=(",", ":")),
            "dossier": dossier.model_dump_json(),
            "refs": json.dumps(sorted(dossier.references()), ensure_ascii=False, separators=(",", ":")),
        },
        PHASE_ORIENTATION,
    )
    orientations = resultat.orientations if resultat is not None else []
    return orientations, StatutAnalyse(
        phase=PHASE_ORIENTATION,
        succes=resultat is not None,
        message_erreur=erreur,
        nb_elements=len(orientations),
        nb_tentatives=tentatives,
    )


def corriger_orientations(
    orientations: list[OrientationSignal], dossier: DossierPLC
) -> tuple[list[OrientationSignal], list[str]]:
    """Corrige les orientations par le code avant agrégation.

    Trois corrections déterministes :

    1. une famille absente de la réponse est ajoutée comme non évaluable ;
    2. une famille orientée alors que son bloc de signaux est indisponible est
       **forcée** non évaluable — orienter sans indicateurs revient à inventer ;
    3. une force absente ou hors vocabulaire est ramenée à « faible », une phase
       inconnue à « neutre ».

    Args:
        orientations: Orientations proposées par le modèle.
        dossier: Dossier PLC, source de vérité de la disponibilité.

    Returns:
        Le couple `(orientations_corrigées, corrections_décrites)`.
    """
    corrections: list[str] = []
    par_famille: dict[str, OrientationSignal] = {}
    for orientation in orientations:
        identifiant = (orientation.famille or "").strip()
        if identifiant not in IDS_FAMILLES:
            corrections.append(f"famille inconnue « {identifiant} » écartée")
            continue
        if identifiant in par_famille:
            corrections.append(f"orientation en double pour « {identifiant} » écartée")
            continue
        par_famille[identifiant] = orientation

    resultat: list[OrientationSignal] = []
    for famille in dossier.familles:
        identifiant = famille.famille
        orientation = par_famille.get(identifiant)
        if orientation is None:
            resultat.append(
                OrientationSignal(
                    famille=identifiant,
                    non_evaluable=True,
                    orientation_phase=None,
                    force=None,
                    justification=(
                        "Famille non orientée par la chaîne d'orientation : traitée "
                        "comme non évaluable par le code."
                    ),
                )
            )
            corrections.append(f"famille « {identifiant} » manquante → non évaluable")
            continue

        if not famille.disponible and not orientation.non_evaluable:
            orientation.non_evaluable = True
            orientation.justification = (
                "Forcée non évaluable par le code : les signaux de cette famille sont "
                "indisponibles dans les entrées, aucune orientation ne peut être "
                "fondée. "
            ) + orientation.justification
            corrections.append(
                f"famille « {identifiant} » orientée malgré des signaux "
                f"indisponibles → forcée non évaluable"
            )

        if orientation.non_evaluable:
            orientation.orientation_phase = None
            orientation.force = None
        else:
            phase = (orientation.orientation_phase or "").strip().lower()
            if phase not in PHASES and phase != ORIENTATION_NEUTRE:
                corrections.append(
                    f"phase inconnue « {orientation.orientation_phase} » pour "
                    f"« {identifiant} » → neutre"
                )
                phase = ORIENTATION_NEUTRE
            orientation.orientation_phase = phase
            force = (orientation.force or "").strip().lower()
            if force not in FORCES:
                if orientation.force:
                    corrections.append(
                        f"force inconnue « {orientation.force} » pour « {identifiant} » "
                        f"→ {FORCE_DEFAUT}"
                    )
                force = FORCE_DEFAUT
            orientation.force = force

        resultat.append(orientation)
    return resultat, corrections


def agreger(signaux: list[OrientationSignal]) -> Classification:
    """Agrège les orientations en une classification de phase.

    Scores par phase = somme des `POIDS_FAMILLES[f] × VALEUR_FORCE[force]` des
    familles orientées vers cette phase. `phase_probable` = argmax ; incertitude
    selon les seuils d'écart et les familles non évaluables. **Le modèle
    oriente ; le code décide.** Fonction pure : aucun appel réseau, aucun état,
    aucun aléa — à orientations identiques, classification identique.

    RÈGLE = **HYPOTHÈSE DE TRAVAIL** (voir `config.py`).

    Args:
        signaux: Orientations corrigées, une par famille.

    Returns:
        La classification complète, `confiance` mise à part.
    """
    scores: dict[str, float] = {phase: 0.0 for phase in PHASES}
    nb_evaluees = 0
    familles_non_evaluables: set[str] = set()

    for signal in signaux:
        if signal.non_evaluable:
            familles_non_evaluables.add(signal.famille)
            continue
        nb_evaluees += 1
        phase = signal.orientation_phase
        if phase not in scores:
            continue
        poids = POIDS_FAMILLES.get(signal.famille, 0.0)
        scores[phase] += poids * VALEUR_FORCE.get(signal.force or FORCE_DEFAUT, 1)

    scores = {phase: round(valeur, 4) for phase, valeur in scores.items()}
    classement = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    premier = classement[0][1] if classement else 0.0
    second = classement[1][1] if len(classement) > 1 else 0.0
    ecart_relatif = (premier - second) / premier if premier > 0 else 0.0

    famille_structurante_absente = any(
        POIDS_FAMILLES.get(famille, 0.0) >= POIDS_FAMILLE_STRUCTURANTE
        for famille in familles_non_evaluables
    )

    if nb_evaluees < MIN_FAMILLES_EVALUEES or premier <= 0.0 or premier == second:
        phase_probable: str | None = None
        incertitude = INCERTITUDE_ELEVEE
    else:
        phase_probable = classement[0][0]
        if ecart_relatif < SEUIL_ECART_ELEVEE or famille_structurante_absente:
            incertitude = INCERTITUDE_ELEVEE
        elif ecart_relatif < SEUIL_ECART_MOYENNE:
            incertitude = INCERTITUDE_MOYENNE
        else:
            incertitude = INCERTITUDE_FAIBLE

    logger.debug(
        "classification : phase=%s incertitude=%s scores=%s (%d famille(s) évaluée(s))",
        phase_probable,
        incertitude,
        scores,
        nb_evaluees,
    )
    return Classification(
        phase_probable=phase_probable,
        incertitude=incertitude,
        scores_par_phase=scores,
        nb_familles_evaluees=nb_evaluees,
        regle_appliquee=enoncer_regle(),
        statut_regle=STATUT_REGLE,
        confiance=CONFIANCE_FAIBLE,
    )


def deriver_confiance(dossier: DossierPLC, classification: Classification) -> str:
    """Dérive la confiance de la classification des confiances amont.

    La confiance ne peut jamais dépasser la plus faible des confiances héritées :
    une classification n'est pas plus sûre que les analyses qui la nourrissent.

    Args:
        dossier: Dossier PLC, porteur des confiances amont.
        classification: Classification calculée.

    Returns:
        « elevee », « moyenne » ou « faible ».
    """
    heritees = [
        niveau
        for niveau in dossier.confiances_amont.values()
        if niveau in NIVEAUX_CONFIANCE
    ]
    plancher = min(
        (NIVEAUX_CONFIANCE.index(niveau) for niveau in heritees), default=0
    )
    if classification.phase_probable is None:
        return CONFIANCE_FAIBLE
    if classification.incertitude == INCERTITUDE_ELEVEE:
        propre = 0
    elif classification.incertitude == INCERTITUDE_MOYENNE or (
        classification.nb_familles_evaluees < len(POIDS_FAMILLES)
    ):
        propre = 1
    else:
        propre = 2
    return NIVEAUX_CONFIANCE[min(plancher, propre)]


def conditions_par_gabarit(
    dossier: DossierPLC, classification: Classification
) -> list[str]:
    """Rédige des conditions de réexamen par gabarits, sans LLM.

    Args:
        dossier: Dossier PLC.
        classification: Classification calculée.

    Returns:
        Des conditions observables dérivées de l'état des familles.
    """
    conditions: list[str] = []
    for famille in dossier.familles:
        if famille.disponible:
            continue
        conditions.append(
            f"Rendre la famille de signaux « {famille.famille} » évaluable en "
            f"relançant la collecte et l'analyse amont correspondantes, puis rejouer "
            f"cette classification : son poids d'agrégation est "
            f"{POIDS_FAMILLES.get(famille.famille, 0.0):.2f}."
        )
    if classification.phase_probable is None:
        conditions.append(
            "Obtenir au moins "
            f"{MIN_FAMILLES_EVALUEES} familles de signaux évaluables : en deçà, "
            "aucune phase ne peut être retenue."
        )
    elif classification.incertitude == INCERTITUDE_ELEVEE and not conditions:
        conditions.append(
            "Réobserver les signaux sur un nouvel horizon : les deux premières "
            "phases sont aujourd'hui trop proches pour que le classement soit "
            "exploitable en décision."
        )
    if not conditions:
        conditions.append(
            "Rejouer cette classification après une nouvelle collecte si l'un des "
            "signaux temporels change d'orientation."
        )
    return conditions[:MAX_CONDITIONS_REEXAMEN]


def confiance_libelle(niveau: str) -> str:
    """Retourne un niveau de confiance validé.

    Args:
        niveau: Niveau proposé.

    Returns:
        Le niveau s'il est admis, « faible » sinon.
    """
    propre = (niveau or "").strip().lower()
    return propre if propre in NIVEAUX_CONFIANCE else CONFIANCE_FAIBLE


CONFIANCES_ORDONNEES: tuple[str, ...] = (
    CONFIANCE_FAIBLE,
    CONFIANCE_MOYENNE,
    CONFIANCE_ELEVEE,
)
