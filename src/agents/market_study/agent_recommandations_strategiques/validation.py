"""Post-validation déterministe du résultat assemblé.

**Aucun appel LLM dans ce module.** Six garanties y sont produites :

1. toute `ref` citée existe dans le dossier de synthèse ;
2. un fondement de type « fait » sans ref valide est retiré ;
3. une recommandation privée de tout fondement factuel est **marquée « non
   ancrée »**, jamais supprimée en silence ;
4. les fourchettes de prix restent dans les devises et l'étendue du benchmark F4 ;
5. le verdict est **recalculé** depuis la grille : toute divergence est corrigée
   au profit du code ;
6. `declenche_plc` vaut strictement `verdict == "positif"`, et
   `faits_cles.valeur` est recopiée depuis le dossier ;
7. les vocabulaires contrôlés (domaine, priorité, horizon, effort, type et
   gravité de risque) sont ramenés à leurs valeurs admises — un run réel a
   produit `gravite="critique"`, hors énumération.
"""

from __future__ import annotations

from config import (
    DOMAINES,
    EFFORTS,
    GRAVITES,
    HORIZONS,
    PRIORITES,
    TYPES_RISQUE,
    TYPE_FAIT,
    TYPE_HYPOTHESE,
    VERDICT_POSITIF,
    logger,
)
from potentiel import appliquer_regle
from schemas import (
    AlerteCoherence,
    DossierSynthese,
    Fondement,
    ResultatRecommandations,
    StatutAnalyse,
)

PHASE_POST_VALIDATION: str = "post_validation"

MENTION_NON_ANCREE: str = (
    "Recommandation non ancrée : aucun fondement factuel vérifiable dans le "
    "dossier de synthèse ne la soutient. Elle relève du jugement de l'analyste et "
    "doit être traitée comme telle."
)


def _valider_fondements(
    fondements: list[Fondement], refs_valides: set[str], compteurs: dict[str, int]
) -> list[Fondement]:
    """Filtre les fondements dont la référence n'existe pas.

    Un fondement « fait » sans ref valide est retiré : le laisser passer en
    hypothèse maquillerait une citation inventée en jugement assumé.

    Args:
        fondements: Fondements proposés.
        refs_valides: Références citables du dossier.
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

    Constaté sur un run réel : un modèle peut inventer une valeur hors
    énumération (« critique » pour une gravité). La laisser passer casserait
    silencieusement tout tri ou filtre en aval.

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


def _a_un_fait(fondements: list[Fondement]) -> bool:
    """Indique si une liste de fondements contient au moins un fait.

    Args:
        fondements: Fondements validés.

    Returns:
        Vrai si au moins un fondement est de type « fait ».
    """
    return any(f.type == TYPE_FAIT for f in fondements)


def valider(
    resultat: ResultatRecommandations, dossier: DossierSynthese
) -> tuple[ResultatRecommandations, list[StatutAnalyse], list[AlerteCoherence]]:
    """Corrige le résultat assemblé et trace chaque correction.

    Args:
        resultat: Résultat brut, avant publication.
        dossier: Dossier de synthèse, source de vérité des références.

    Returns:
        Le triplet `(resultat_corrige, statuts, alertes)`.
    """
    refs_valides = dossier.references()
    valeurs = dossier.valeurs()
    compteurs: dict[str, int] = {}
    alertes: list[AlerteCoherence] = []
    non_ancrees: list[str] = []

    # --- 1. Diagnostic ------------------------------------------------------ #
    if resultat.diagnostic is not None:
        for point in resultat.diagnostic.convergences:
            point.fondements = _valider_fondements(point.fondements, refs_valides, compteurs)
        for contradiction in resultat.diagnostic.contradictions:
            contradiction.fondements = _valider_fondements(
                contradiction.fondements, refs_valides, compteurs
            )

    # --- 2. Grille de notation --------------------------------------------- #
    for note in resultat.verdict_potentiel.grille:
        note.fondements = _valider_fondements(note.fondements, refs_valides, compteurs)

    # --- 3. Recommandations ------------------------------------------------- #
    def traiter(recommandation) -> None:
        recommandation.fondements = _valider_fondements(
            recommandation.fondements, refs_valides, compteurs
        )
        recommandation.domaine = _normaliser(
            recommandation.domaine, DOMAINES, DOMAINES[0], compteurs
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
        if not _a_un_fait(recommandation.fondements):
            recommandation.fondements.append(
                Fondement(type=TYPE_HYPOTHESE, ref=None, detail=MENTION_NON_ANCREE)
            )
            non_ancrees.append(recommandation.id_reco)

    for recommandation in resultat.recommandations_produit:
        traiter(recommandation)
    for recommandation in resultat.recommandations_marketing:
        traiter(recommandation)
    if resultat.recommandation_positionnement is not None:
        traiter(resultat.recommandation_positionnement)

    # --- 4. Fourchettes de prix -------------------------------------------- #
    if resultat.recommandation_prix is not None:
        prix = resultat.recommandation_prix
        prix.fondements = _valider_fondements(prix.fondements, refs_valides, compteurs)
        devises = set(dossier.concurrence.devises_benchmark) if dossier.concurrence else set()
        bornes = dossier.concurrence.bornes_benchmark if dossier.concurrence else {}
        retenues = []
        for fourchette in prix.fourchettes:
            if fourchette.devise not in devises:
                compteurs["fourchettes_hors_devise"] = (
                    compteurs.get("fourchettes_hors_devise", 0) + 1
                )
                continue
            limites = bornes.get(fourchette.devise)
            if limites:
                basse, haute = limites["min"], limites["max"]
                corrigee = False
                if fourchette.min < basse:
                    fourchette.min = basse
                    corrigee = True
                if fourchette.max > haute:
                    fourchette.max = haute
                    corrigee = True
                if fourchette.min > fourchette.max:
                    fourchette.min, fourchette.max = fourchette.max, fourchette.min
                    corrigee = True
                if corrigee:
                    compteurs["fourchettes_corrigees"] = (
                        compteurs.get("fourchettes_corrigees", 0) + 1
                    )
                    fourchette.logique_ancrage += (
                        f" [Bornes ramenées par le code dans l'étendue du benchmark "
                        f"{fourchette.devise} : {basse}–{haute}.]"
                    )
            retenues.append(fourchette)
        prix.fourchettes = retenues
        if not _a_un_fait(prix.fondements):
            prix.fondements.append(
                Fondement(type=TYPE_HYPOTHESE, ref=None, detail=MENTION_NON_ANCREE)
            )
            non_ancrees.append("recommandation_prix")

    # --- 5. Opportunités et risques ---------------------------------------- #
    for opportunite in resultat.opportunites:
        opportunite.fondements = _valider_fondements(
            opportunite.fondements, refs_valides, compteurs
        )
    for risque in resultat.risques:
        risque.fondements = _valider_fondements(risque.fondements, refs_valides, compteurs)
        risque.type = _normaliser(risque.type, TYPES_RISQUE, TYPES_RISQUE[0], compteurs)
        risque.gravite = _normaliser(risque.gravite, GRAVITES, GRAVITES[-1], compteurs)

    # --- 6. Faits clés : ref existante et valeur recopiée ------------------- #
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

    # --- 7. Verdict recalculé et `declenche_plc` --------------------------- #
    recalcule = appliquer_regle(
        resultat.verdict_potentiel.grille, dossier.qualite_donnees
    )
    if (
        recalcule.verdict != resultat.verdict_potentiel.verdict
        or recalcule.score_total != resultat.verdict_potentiel.score_total
        or recalcule.nb_criteres_evalues != resultat.verdict_potentiel.nb_criteres_evalues
    ):
        compteurs["verdict_corrige"] = compteurs.get("verdict_corrige", 0) + 1
        alertes.append(
            AlerteCoherence(
                type="verdict_recalcule",
                detail=(
                    f"le verdict publié divergeait de celui que la règle produit sur "
                    f"la grille notée : « {resultat.verdict_potentiel.verdict} » "
                    f"(score {resultat.verdict_potentiel.score_total}) corrigé en "
                    f"« {recalcule.verdict} » (score {recalcule.score_total}). "
                    f"Le code fait foi."
                ),
            )
        )
    conditions = resultat.verdict_potentiel.conditions_reexamen
    resultat.verdict_potentiel.verdict = recalcule.verdict
    resultat.verdict_potentiel.score_total = recalcule.score_total
    resultat.verdict_potentiel.nb_criteres_evalues = recalcule.nb_criteres_evalues
    resultat.verdict_potentiel.regle_appliquee = recalcule.regle_appliquee
    resultat.verdict_potentiel.statut_regle = recalcule.statut_regle
    resultat.verdict_potentiel.confiance = recalcule.confiance
    resultat.verdict_potentiel.conditions_reexamen = conditions

    attendu = resultat.verdict_potentiel.verdict == VERDICT_POSITIF
    if resultat.verdict_potentiel.declenche_plc != attendu:
        compteurs["plc_corrige"] = compteurs.get("plc_corrige", 0) + 1
    resultat.verdict_potentiel.declenche_plc = attendu

    # --- 8. Traçabilité ----------------------------------------------------- #
    statuts: list[StatutAnalyse] = []
    messages = {
        "faits_retires": (
            "fondement(s) déclaré(s) « fait » sans référence valide retiré(s)"
        ),
        "refs_nettoyees": "référence(s) inexistante(s) retirée(s) d'une hypothèse",
        "types_corriges": "type(s) de fondement inconnu(s) ramené(s) à « hypothese »",
        "fourchettes_hors_devise": (
            "fourchette(s) de prix libellée(s) dans une devise absente du benchmark, "
            "retirée(s) — aucune conversion n'est possible"
        ),
        "fourchettes_corrigees": (
            "fourchette(s) de prix ramenée(s) dans l'étendue du benchmark"
        ),
        "faits_cles_retires": "fait(s) clé(s) citant une référence inexistante retiré(s)",
        "valeurs_ecrasees": (
            "valeur(s) de fait clé réécrite(s) depuis le dossier de synthèse"
        ),
        "verdict_corrige": "verdict corrigé au profit du calcul du code",
        "plc_corrige": "`declenche_plc` réaligné sur le verdict calculé",
        "vocabulaires_corriges": (
            "valeur(s) hors vocabulaire contrôlé ramenée(s) à une valeur admise "
            "(domaine, priorité, horizon, effort, type ou gravité de risque)"
        ),
    }
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
                    f"vérifiable du dossier. Elles sont conservées et signalées, jamais "
                    f"supprimées en silence."
                ),
            )
        )
    if not statuts:
        statuts.append(
            StatutAnalyse(phase=PHASE_POST_VALIDATION, succes=True, nb_elements=0)
        )

    logger.debug("post-validation : %s", compteurs or "aucune correction")
    return resultat, statuts, alertes
