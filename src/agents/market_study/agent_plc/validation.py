"""Post-validation déterministe du résultat assemblé.

**Aucun appel LLM dans ce module.** Six garanties y sont produites :

1. toute `ref` citée existe dans le dossier PLC ; un fondement « fait » sans ref
   valide est retiré, jamais requalifié en hypothèse ;
2. `scores_par_phase`, `phase_probable` et `incertitude` sont **recalculés** par
   `agreger` : toute divergence est corrigée au profit du code et tracée ;
3. `phase_probable` appartient à `PHASES` ou vaut `None` — une seule phase,
   jamais une liste ;
4. la cohérence de déclenchement est vérifiée : un non-déclenchement n'emporte
   ni classification, ni signaux, ni recommandations ; une exécution forcée
   porte sa limite de traçage ;
5. toute recommandation privée de fondement factuel est **marquée « non
   ancrée »**, jamais supprimée en silence ;
6. `faits_cles.valeur` est recopiée depuis le dossier PLC, écrasant toute valeur
   proposée par un modèle.
"""

from __future__ import annotations

from config import (
    EFFORTS,
    HORIZONS,
    LIMITE_EXECUTION_FORCEE,
    MENTION_NON_ANCREE,
    MODE_FORCE,
    MODE_NON_DECLENCHE,
    PHASES,
    PRIORITES,
    TYPE_FAIT,
    TYPE_HYPOTHESE,
    logger,
)
from classification import agreger
from schemas import (
    AlerteCoherence,
    DossierPLC,
    Fondement,
    ResultatPLC,
    StatutAnalyse,
)

PHASE_POST_VALIDATION: str = "post_validation"


def _valider_fondements(
    fondements: list[Fondement], refs_valides: set[str], compteurs: dict[str, int]
) -> list[Fondement]:
    """Filtre les fondements dont la référence n'existe pas.

    Un fondement « fait » sans ref valide est retiré : le laisser passer en
    hypothèse maquillerait une citation inventée en jugement assumé.

    Args:
        fondements: Fondements proposés.
        refs_valides: Références citables du dossier PLC.
        compteurs: Compteurs de correction, enrichis sur place.

    Returns:
        Les fondements conservés.
    """
    gardes: list[Fondement] = []
    for fondement in fondements:
        if fondement.type not in (TYPE_FAIT, TYPE_HYPOTHESE):
            fondement.type = TYPE_HYPOTHESE
            compteurs["types_corriges"] = compteurs.get("types_corriges", 0) + 1
        if fondement.type == TYPE_FAIT:
            if not fondement.ref or fondement.ref not in refs_valides:
                compteurs["faits_retires"] = compteurs.get("faits_retires", 0) + 1
                continue
        elif fondement.ref and fondement.ref not in refs_valides:
            fondement.ref = None
            compteurs["refs_nettoyees"] = compteurs.get("refs_nettoyees", 0) + 1
        gardes.append(fondement)
    return gardes


def _normaliser(
    valeur: str, autorisees: tuple[str, ...], defaut: str, compteurs: dict[str, int]
) -> str:
    """Ramène une valeur dans son vocabulaire contrôlé.

    Args:
        valeur: Valeur proposée.
        autorisees: Valeurs admises.
        defaut: Valeur de repli.
        compteurs: Compteurs de correction, enrichis sur place.

    Returns:
        La valeur si elle est admise, le défaut sinon.
    """
    propre = (valeur or "").strip().lower()
    if propre in autorisees:
        return propre
    compteurs["vocabulaires_corriges"] = compteurs.get("vocabulaires_corriges", 0) + 1
    return defaut


def valider(
    resultat: ResultatPLC, dossier: DossierPLC | None
) -> tuple[ResultatPLC, list[StatutAnalyse], list[AlerteCoherence]]:
    """Corrige le résultat assemblé et trace chaque correction.

    Args:
        resultat: Résultat brut, avant publication.
        dossier: Dossier PLC, source de vérité des références, ou `None` si la
            classification n'a pas été déclenchée.

    Returns:
        Le triplet `(resultat_corrige, statuts, alertes)`.
    """
    compteurs: dict[str, int] = {}
    alertes: list[AlerteCoherence] = []
    non_ancrees: list[str] = []

    # --- 1. Cohérence de déclenchement -------------------------------------- #
    if resultat.declenchement.mode == MODE_NON_DECLENCHE:
        if resultat.classification is not None or resultat.signaux or (
            resultat.recommandations_phase
        ):
            compteurs["non_declenchement_nettoye"] = 1
            alertes.append(
                AlerteCoherence(
                    type="declenchement_incoherent",
                    detail=(
                        "des éléments de classification étaient présents malgré un "
                        "non-déclenchement : ils ont été retirés par le code."
                    ),
                )
            )
        resultat.classification = None
        resultat.signaux = []
        resultat.recommandations_phase = []
        resultat.dossier_plc = None
    elif resultat.declenchement.mode == MODE_FORCE and (
        LIMITE_EXECUTION_FORCEE not in resultat.limites
    ):
        resultat.limites.insert(0, LIMITE_EXECUTION_FORCEE)
        compteurs["limite_forcage_ajoutee"] = 1

    if dossier is None or resultat.classification is None:
        return resultat, _statuts(compteurs, non_ancrees, alertes), alertes

    refs_valides = dossier.references()
    valeurs = dossier.valeurs()

    # --- 2. Fondements des orientations ------------------------------------- #
    for signal in resultat.signaux:
        signal.fondements = _valider_fondements(signal.fondements, refs_valides, compteurs)
        if not signal.non_evaluable and signal.orientation_phase not in (
            *PHASES,
            "neutre",
        ):
            signal.orientation_phase = "neutre"
            compteurs["orientations_corrigees"] = (
                compteurs.get("orientations_corrigees", 0) + 1
            )

    # --- 3. Agrégation recalculée ------------------------------------------- #
    recalculee = agreger(resultat.signaux)
    actuelle = resultat.classification
    if (
        recalculee.phase_probable != actuelle.phase_probable
        or recalculee.incertitude != actuelle.incertitude
        or recalculee.scores_par_phase != actuelle.scores_par_phase
        or recalculee.nb_familles_evaluees != actuelle.nb_familles_evaluees
    ):
        compteurs["classification_corrigee"] = 1
        alertes.append(
            AlerteCoherence(
                type="classification_recalculee",
                detail=(
                    f"la classification publiée divergeait de celle que la règle "
                    f"produit sur les orientations retenues : "
                    f"« {actuelle.phase_probable} » corrigée en "
                    f"« {recalculee.phase_probable} ». Le code fait foi."
                ),
            )
        )
    actuelle.phase_probable = recalculee.phase_probable
    actuelle.incertitude = recalculee.incertitude
    actuelle.scores_par_phase = recalculee.scores_par_phase
    actuelle.nb_familles_evaluees = recalculee.nb_familles_evaluees
    actuelle.regle_appliquee = recalculee.regle_appliquee
    actuelle.statut_regle = recalculee.statut_regle

    if actuelle.phase_probable is not None and actuelle.phase_probable not in PHASES:
        actuelle.phase_probable = None
        compteurs["phase_hors_vocabulaire"] = 1

    # --- 4. Recommandations de phase ---------------------------------------- #
    if actuelle.phase_probable is None and resultat.recommandations_phase:
        compteurs["recommandations_sans_phase_retirees"] = len(
            resultat.recommandations_phase
        )
        alertes.append(
            AlerteCoherence(
                type="recommandations_sans_phase",
                detail=(
                    "des recommandations de phase étaient présentes alors qu'aucune "
                    "phase n'est retenue : elles ont été retirées. Recommander sur "
                    "une phase inconnue reviendrait à recommander au hasard."
                ),
            )
        )
        resultat.recommandations_phase = []

    for recommandation in resultat.recommandations_phase:
        recommandation.fondements = _valider_fondements(
            recommandation.fondements, refs_valides, compteurs
        )
        recommandation.priorite = _normaliser(
            recommandation.priorite, tuple(p.lower() for p in PRIORITES), "p3", compteurs
        ).upper()
        recommandation.horizon = _normaliser(
            recommandation.horizon, HORIZONS, HORIZONS[-1], compteurs
        )
        recommandation.effort_estime = _normaliser(
            recommandation.effort_estime, EFFORTS, EFFORTS[1], compteurs
        )
        if not any(f.type == TYPE_FAIT for f in recommandation.fondements):
            recommandation.fondements.append(
                Fondement(type=TYPE_HYPOTHESE, ref=None, detail=MENTION_NON_ANCREE)
            )
            non_ancrees.append(recommandation.id_reco)

    # --- 5. Faits clés : ref existante et valeur recopiée ------------------- #
    faits_retenus = []
    for fait in resultat.faits_cles:
        if fait.ref not in refs_valides:
            compteurs["faits_cles_retires"] = compteurs.get("faits_cles_retires", 0) + 1
            continue
        valeur_dossier = valeurs.get(fait.ref, "")
        if fait.valeur != valeur_dossier:
            compteurs["valeurs_ecrasees"] = compteurs.get("valeurs_ecrasees", 0) + 1
        fait.valeur = valeur_dossier
        faits_retenus.append(fait)
    resultat.faits_cles = faits_retenus

    logger.debug("post-validation : %s", compteurs or "aucune correction")
    return resultat, _statuts(compteurs, non_ancrees, alertes), alertes


def _statuts(
    compteurs: dict[str, int],
    non_ancrees: list[str],
    alertes: list[AlerteCoherence],
) -> list[StatutAnalyse]:
    """Traduit les compteurs de correction en statuts publiables.

    Args:
        compteurs: Compteurs de correction.
        non_ancrees: Identifiants des recommandations non ancrées.
        alertes: Alertes déjà collectées, enrichies sur place.

    Returns:
        Les statuts de post-validation ; un statut neutre si rien n'a été corrigé.
    """
    messages = {
        "faits_retires": "fondement(s) déclaré(s) « fait » sans référence valide retiré(s)",
        "refs_nettoyees": "référence(s) inexistante(s) retirée(s) d'une hypothèse",
        "types_corriges": "type(s) de fondement inconnu(s) ramené(s) à « hypothese »",
        "orientations_corrigees": "orientation(s) hors vocabulaire ramenée(s) à « neutre »",
        "classification_corrigee": "classification corrigée au profit du calcul du code",
        "phase_hors_vocabulaire": "phase hors vocabulaire ramenée à `null`",
        "recommandations_sans_phase_retirees": (
            "recommandation(s) retirée(s) faute de phase retenue"
        ),
        "non_declenchement_nettoye": (
            "éléments de classification retirés d'une sortie non déclenchée"
        ),
        "limite_forcage_ajoutee": "limite de traçage du forçage ajoutée par le code",
        "faits_cles_retires": "fait(s) clé(s) citant une référence inexistante retiré(s)",
        "valeurs_ecrasees": "valeur(s) de fait clé réécrite(s) depuis le dossier PLC",
        "vocabulaires_corriges": (
            "valeur(s) hors vocabulaire contrôlé ramenée(s) à une valeur admise "
            "(priorité, horizon, effort)"
        ),
    }
    statuts: list[StatutAnalyse] = []
    for cle, libelle in messages.items():
        if compteurs.get(cle):
            statuts.append(
                StatutAnalyse(
                    phase=PHASE_POST_VALIDATION,
                    succes=True,
                    message_erreur=f"{compteurs[cle]} {libelle}.",
                    nb_elements=compteurs[cle],
                )
            )
    if non_ancrees:
        statuts.append(
            StatutAnalyse(
                phase=PHASE_POST_VALIDATION,
                succes=True,
                message_erreur=(
                    f"{len(non_ancrees)} recommandation(s) sans fondement factuel "
                    f"marquée(s) « non ancrée » : {', '.join(non_ancrees[:6])}."
                ),
                nb_elements=len(non_ancrees),
            )
        )
        alertes.append(
            AlerteCoherence(
                type="recommandation_non_ancree",
                detail=(
                    f"{len(non_ancrees)} recommandation(s) ne s'appuient sur aucun fait "
                    f"vérifiable du dossier PLC. Elles sont conservées et signalées, "
                    f"jamais supprimées en silence."
                ),
            )
        )
    if not statuts:
        statuts.append(
            StatutAnalyse(phase=PHASE_POST_VALIDATION, succes=True, nb_elements=0)
        )
    return statuts
