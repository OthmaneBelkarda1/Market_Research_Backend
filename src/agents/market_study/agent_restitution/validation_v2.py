"""Post-validation du rapport décisionnel — **aucun appel LLM ici**.

Elle CONSERVE les cinq garanties du gabarit v1, appliquées à des puces plutôt
qu'à des paragraphes : contrôle numérique, verdict et phase, bascules simulées,
termes proscrits, mentions d'étude partielle. Elle en ajoute cinq, propres au v2 :

6. **budgets de mots** — par écran et au total. Un dépassement déclenche une
   régénération avec consigne de réduction, puis la coupe des puces excédentaires
   **par la fin** : on retire des puces entières, jamais le contenu d'une puce ;
7. **libellé de décision** — Go / No-go / Go conditionnel doit correspondre au
   mapping, ET le verdict brut doit rester affiché sous lui ;
8. **absence de « … » en fin de cellule** — la troncature muette est le défaut
   que le v2 corrige : elle ne doit revenir par aucun chemin ;
9. **absence de jargon interne dans les écrans 0 à 3** — l'écran méthode reste
   hors contrôle : il recopie des limites amont verbatim ;
10. **complétude du tableau des cinq forces** — ses cinq lignes sont présentes,
    « non évalué » compris.
"""

from __future__ import annotations

import re

from config import (
    ECRANS_HORS_CONTROLE_TERMES,
    ECRAN_DECISION,
    ECRAN_METHODE,
    BUDGET_MOTS,
    BUDGET_MOTS_TOTAL,
    LIBELLES_CINQ_FORCES,
    LIBELLES_VERDICT,
    MARQUEUR_GABARIT_V2,
    NEGATIONS_TOLEREES,
    PHASE_LISIBLE,
    SEUIL_REGENERATION_PCT,
    SUBSTITUTS_VERDICT_INTERDITS,
    TERMES_INTERDITS,
    TERMES_JARGON,
    VERDICT_LISIBLE,
    logger,
)
from preparation import ListeBlanche, extraire_nombres
from validation import _termes_presents
from schemas import (
    ControlesRestitution,
    Injectables,
    SectionProduite,
    SortieEcran,
    StatutAnalyse,
)

PHASE_POST_VALIDATION_V2: str = "post_validation_v2"

MOTIF_TITRE_DECISION = re.compile(r"^##\s+Décision\s*:\s*(.+)$", re.M)
MOTIF_LIGNE_VERDICT = re.compile(r"^Verdict calculé\s*:\s*([^·]+)·", re.M)
MOTIF_CELLULE_TRONQUEE = re.compile(r"…\s*(?:\||$)", re.M)
"""Une ellipse en fin de cellule de tableau ou de ligne : la troncature muette."""


def nettoyer_ecrans(
    narratifs: dict[str, SortieEcran | None], liste: ListeBlanche
) -> tuple[dict[str, SortieEcran | None], dict[str, dict[str, int]]]:
    """Retire des puces les nombres hors liste blanche et les termes proscrits.

    Le grain du contrôle est la PUCE, non la phrase : une puce est déjà une
    phrase, et en amputer une moitié produirait un énoncé bancal. Une puce
    fautive disparaît entière.

    Args:
        narratifs: Puces produites par les chaînes de rédaction, par écran.
        liste: Liste blanche numérique.

    Returns:
        Le couple `(narratifs_nettoyés, compteurs_par_écran)`.
    """
    compteurs: dict[str, dict[str, int]] = {}
    for ecran, sortie in narratifs.items():
        if sortie is None or ecran in ECRANS_HORS_CONTROLE_TERMES:
            continue
        compte = {"nombres": 0, "termes": 0, "mots_avant": 0, "mots_apres": 0}
        propres: dict[str, list[str]] = {}
        for sous_bloc, puces in sortie.sous_blocs.items():
            gardees: list[str] = []
            for puce in puces:
                compte["mots_avant"] += len(puce.split())
                inconnus = [
                    ecriture
                    for ecriture, valeur in extraire_nombres(puce)
                    if not liste.contient(valeur)
                ]
                proscrits = _termes_presents(puce, TERMES_INTERDITS) + _termes_presents(
                    puce, TERMES_JARGON
                )
                if inconnus:
                    compte["nombres"] += len(inconnus)
                    logger.warning(
                        "[%s/%s] puce retirée — nombre(s) hors liste blanche : %s",
                        ecran,
                        sous_bloc,
                        ", ".join(inconnus[:5]),
                    )
                    continue
                if proscrits:
                    compte["termes"] += len(proscrits)
                    logger.warning(
                        "[%s/%s] puce retirée — terme(s) proscrit(s) : %s",
                        ecran,
                        sous_bloc,
                        ", ".join(proscrits[:5]),
                    )
                    continue
                compte["mots_apres"] += len(puce.split())
                gardees.append(puce)
            if gardees:
                propres[sous_bloc] = gardees
        narratifs[ecran] = SortieEcran(sous_blocs=propres)
        compteurs[ecran] = compte
    return narratifs, compteurs


def ecrans_a_regenerer(compteurs: dict[str, dict[str, int]]) -> list[str]:
    """Détermine les écrans dont le narratif a trop souffert du nettoyage.

    Args:
        compteurs: Compteurs de retrait par écran.

    Returns:
        Les écrans à régénérer une seule fois.
    """
    a_reprendre: list[str] = []
    for ecran, compte in compteurs.items():
        avant = compte["mots_avant"]
        if not avant:
            continue
        if 100.0 * (avant - compte["mots_apres"]) / avant > SEUIL_REGENERATION_PCT:
            a_reprendre.append(ecran)
    return a_reprendre


def ecrans_hors_budget(narratifs: dict[str, SortieEcran | None]) -> list[str]:
    """Repère les écrans dont les puces dépassent leur budget de mots.

    Args:
        narratifs: Puces produites, par écran.

    Returns:
        Les écrans en dépassement.
    """
    depassements: list[str] = []
    for ecran, sortie in narratifs.items():
        if sortie is None:
            continue
        mots = sum(
            len(puce.split()) for puces in sortie.sous_blocs.values() for puce in puces
        )
        if mots > BUDGET_MOTS.get(ecran, BUDGET_MOTS_TOTAL):
            depassements.append(ecran)
    return depassements


def couper_au_budget(
    sortie: SortieEcran, budget: int
) -> tuple[SortieEcran, int]:
    """Ramène un écran dans son budget en retirant des puces par la fin.

    On retire des puces ENTIÈRES, jamais le contenu d'une puce : une puce
    amputée serait une phrase fausse, alors qu'une puce en moins est seulement
    une idée en moins — et c'est la dernière, donc la moins prioritaire.

    Args:
        sortie: Puces de l'écran.
        budget: Budget de mots de l'écran.

    Returns:
        Le couple `(sortie_ramenée, nb_puces_retirées)`.
    """
    ordre = list(sortie.sous_blocs)
    conserve = {cle: list(puces) for cle, puces in sortie.sous_blocs.items()}
    retirees = 0

    def total() -> int:
        return sum(len(p.split()) for puces in conserve.values() for p in puces)

    while total() > budget:
        for cle in reversed(ordre):
            if len(conserve.get(cle, [])) > 1:
                conserve[cle].pop()
                retirees += 1
                break
        else:
            # Chaque sous-bloc est réduit à sa puce unique : on ne descend pas
            # plus bas, un sous-bloc vide serait pire qu'un léger dépassement.
            break
    return SortieEcran(sous_blocs={c: p for c, p in conserve.items() if p}), retirees


def retenir_compression(
    originaux: list[str], compresses: list[str] | None, max_mots: int
) -> tuple[list[str], int]:
    """Retient une compression rédactionnelle, ou retombe sur l'original coupé.

    Une compression est acceptée si elle n'introduit AUCUNE valeur numérique
    absente du texte d'origine correspondant. C'est un contrôle plus strict que
    la liste blanche globale : ici, le seul référentiel légitime est le texte que
    le modèle avait sous les yeux.

    Le repli n'est jamais une troncature à « … » : c'est une coupe au dernier mot
    entier, qui se voit et ne se fait pas passer pour une phrase complète.

    Args:
        originaux: Textes d'origine.
        compresses: Textes renvoyés par le modèle, ou `None` si la chaîne a échoué.
        max_mots: Budget de mots par texte.

    Returns:
        Le couple `(textes_retenus, nb_compressions_acceptées)`.
    """
    from preparation_v2 import couper_mots

    retenus: list[str] = []
    acceptees = 0
    for rang, original in enumerate(originaux):
        propose = compresses[rang] if compresses and rang < len(compresses) else ""
        if propose:
            connus = {valeur for _, valeur in extraire_nombres(original)}
            ajoutes = [
                ecriture
                for ecriture, valeur in extraire_nombres(propose)
                if valeur not in connus
            ]
            trop_long = len(propose.split()) > max_mots
            if not ajoutes and not trop_long:
                retenus.append(propose)
                acceptees += 1
                continue
            logger.warning(
                "compression rejetée (%s) : %s",
                "chiffre ajouté" if ajoutes else "budget dépassé",
                ", ".join(ajoutes[:3]) or f"{len(propose.split())} mots",
            )
        retenus.append(couper_mots(original, max_mots))
    return retenus, acceptees


def controler_v2(
    rapport: str,
    resume: str,
    injectables: Injectables,
    liste: ListeBlanche,
    compteurs: dict[str, dict[str, int]],
    sections: list[SectionProduite],
) -> tuple[ControlesRestitution, list[StatutAnalyse]]:
    """Contrôle le rapport décisionnel assemblé et publie le compte rendu.

    Args:
        rapport: Rapport Markdown complet.
        resume: Résumé exécutif Markdown.
        injectables: Données injectables.
        liste: Liste blanche numérique.
        compteurs: Compteurs de retrait accumulés au nettoyage.
        sections: Écrans produits, avec leur décompte de mots.

    Returns:
        Le couple `(controles, statuts)`.
    """
    statuts: list[StatutAnalyse] = []
    controles = ControlesRestitution(bascules_recalculees=True)
    controles.cinq_forces_source = injectables.cinq_forces_source
    controles.libelle_verdict = injectables.decision_libelle

    def echec(message: str, nb: int = 0) -> None:
        statuts.append(
            StatutAnalyse(
                phase=PHASE_POST_VALIDATION_V2,
                succes=False,
                message_erreur=message,
                nb_elements=nb,
            )
        )

    # --- 1. Contrôle numérique --------------------------------------------- #
    nombres = extraire_nombres(rapport) + extraire_nombres(resume)
    controles.nb_nombres_verifies = len(nombres)
    controles.nb_nombres_retires = sum(c["nombres"] for c in compteurs.values())
    controles.termes_interdits_retires = sum(c["termes"] for c in compteurs.values())
    inconnus = [e for e, valeur in nombres if not liste.contient(valeur)]
    if inconnus:
        echec(
            f"{len(inconnus)} nombre(s) du document restent hors liste blanche après "
            f"nettoyage — ils proviennent de blocs générés par le code, ce qui signale "
            f"un défaut de la liste blanche et non une invention : "
            f"{', '.join(sorted(set(inconnus))[:10])}.",
            len(inconnus),
        )

    # --- 2. Marqueur de gabarit -------------------------------------------- #
    if MARQUEUR_GABARIT_V2 not in rapport:
        echec(
            "le marqueur de gabarit est absent : le frontend appliquera son rendu "
            "historique et affichera les bandes d'extraits en bas de page."
        )

    # --- 3. Décision : libellé ET verdict brut ------------------------------ #
    attendu_libelle = LIBELLES_VERDICT.get(
        injectables.verdict_brut, injectables.verdict_brut
    )
    attendu_verdict = VERDICT_LISIBLE.get(
        injectables.verdict_brut, injectables.verdict_brut
    )
    trouve_libelle = MOTIF_TITRE_DECISION.search(rapport)
    libelle = trouve_libelle.group(1).strip() if trouve_libelle else ""
    trouve_verdict = MOTIF_LIGNE_VERDICT.search(rapport)
    verdict_affiche = trouve_verdict.group(1).strip() if trouve_verdict else ""

    controles.verdict_conforme = (
        libelle == attendu_libelle
        and verdict_affiche == attendu_verdict
        and not _termes_presents(libelle, SUBSTITUTS_VERDICT_INTERDITS)
    )
    if not controles.verdict_conforme:
        echec(
            f"le titre de décision porte « {libelle} » et la ligne de verdict "
            f"« {verdict_affiche} », alors que l'analyse amont conclut "
            f"« {attendu_verdict} », soit « {attendu_libelle} »."
        )

    # Un « Go conditionnel » sans condition affichée n'oriente aucune décision.
    if injectables.decision_libelle == LIBELLES_VERDICT.get("indetermine") and not (
        injectables.puces_changer_decision or injectables.puces_manque_trancher
    ):
        echec(
            "décision « Go conditionnel » sans condition ni manque affiché : le "
            "lecteur ne sait pas ce qui la lèverait."
        )

    # --- 4. Phase ----------------------------------------------------------- #
    if injectables.phase_brute:
        attendue = PHASE_LISIBLE.get(injectables.phase_brute, injectables.phase_brute)
        controles.phase_conforme = attendue.lower() in rapport.lower()
        if not controles.phase_conforme:
            echec(f"la phase « {attendue} » n'est pas affichée telle quelle.")
    else:
        controles.phase_conforme = None

    # --- 5. Bascules -------------------------------------------------------- #
    enonces = {b.enonce for b in injectables.bascules}
    affichees = [
        ligne.strip()[2:].strip()
        for ligne in rapport.splitlines()
        if ligne.strip().startswith("- ") and "faire passer le verdict" in ligne
    ]
    non_simulees = [ligne for ligne in affichees if ligne not in enonces]
    controles.bascules_recalculees = not non_simulees
    if non_simulees:
        echec(
            f"{len(non_simulees)} bascule(s) affichée(s) ne proviennent pas de la "
            f"simulation du code.",
            len(non_simulees),
        )

    # --- 6. Budgets de mots ------------------------------------------------- #
    total = 0
    hors_budget: list[str] = []
    for section in sections:
        if not section.nb_mots_budget:
            continue
        total += section.nb_mots_narratif
        if section.nb_mots_narratif > section.nb_mots_budget:
            hors_budget.append(
                f"{section.titre} ({section.nb_mots_narratif} > {section.nb_mots_budget})"
            )
    if total > BUDGET_MOTS_TOTAL:
        hors_budget.append(f"total ({total} > {BUDGET_MOTS_TOTAL})")
    controles.budgets_respectes = not hors_budget
    if hors_budget:
        echec(
            f"budget de mots dépassé : {', '.join(hors_budget)}.", len(hors_budget)
        )

    # --- 7. Troncature muette ----------------------------------------------- #
    tronquees = MOTIF_CELLULE_TRONQUEE.findall(rapport)
    if tronquees:
        echec(
            f"{len(tronquees)} cellule(s) ou ligne(s) se terminent par « … » : la "
            f"troncature muette perd l'argument qu'elle coupe.",
            len(tronquees),
        )

    # --- 8. Jargon interne hors écran méthode -------------------------------- #
    corps = rapport.split("## Méthode et limites")[0]
    jargon = _termes_presents(corps, TERMES_JARGON)
    recopies = _termes_presents(corps, TERMES_INTERDITS)
    if jargon:
        echec(
            f"jargon interne présent dans les écrans de lecture : "
            f"{', '.join(sorted(set(jargon)))}.",
            len(jargon),
        )
    if recopies:
        statuts.append(
            StatutAnalyse(
                phase=PHASE_POST_VALIDATION_V2,
                succes=True,
                message_erreur=(
                    f"terme(s) proscrit(s) dans des contenus recopiés verbatim des "
                    f"analyses amont, conservés tels quels et signalés : "
                    f"{', '.join(sorted(set(recopies)))}."
                ),
                nb_elements=len(recopies),
            )
        )

    # --- 9. Les cinq forces, toutes les cinq -------------------------------- #
    manquantes = [
        libelle for _, libelle in LIBELLES_CINQ_FORCES if libelle not in rapport
    ]
    if manquantes:
        echec(
            f"{len(manquantes)} des cinq forces ne sont pas affichées : "
            f"{', '.join(manquantes)}. Une force absente se lit comme une force "
            f"jugée sans intérêt.",
            len(manquantes),
        )

    # --- 10. Étude partielle ------------------------------------------------ #
    mentions: list[str] = []
    for ecran in (ECRAN_DECISION,):
        if injectables.encart_partielle_v2 and injectables.encart_partielle_v2 in rapport:
            mentions.append(ecran)
    for section_id, mention in injectables.mentions_partielles.items():
        if mention and mention in rapport and section_id not in mentions:
            mentions.append(section_id)
    controles.mentions_etude_partielle = mentions
    attendues = set(injectables.sections_degradees) | set(injectables.sections_absentes)
    if attendues and not mentions:
        echec(
            "l'étude est partielle mais aucune mention ne le dit dans le rapport."
        )

    # --- 11. Un seul résumé -------------------------------------------------- #
    if rapport.count("## Décision :") > 1:
        echec(
            "le titre de décision apparaît plusieurs fois : le résumé exécutif ne "
            "doit exister qu'à un seul endroit du document."
        )

    if not statuts:
        statuts.append(
            StatutAnalyse(phase=PHASE_POST_VALIDATION_V2, succes=True, nb_elements=0)
        )
    logger.debug(
        "post-validation v2 : %d nombre(s) vérifié(s), %d retiré(s), budgets %s",
        controles.nb_nombres_verifies,
        controles.nb_nombres_retires,
        "respectés" if controles.budgets_respectes else "dépassés",
    )
    _ = ECRAN_METHODE
    return controles, statuts
