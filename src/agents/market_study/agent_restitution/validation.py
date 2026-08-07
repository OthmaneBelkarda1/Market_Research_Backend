"""Post-validation déterministe du rapport assemblé — **aucun appel LLM ici**.

Cinq garanties y sont produites :

1. **contrôle numérique** — chaque nombre du rapport est confronté à la liste
   blanche. Un nombre inconnu fait retirer la phrase qui le porte, et le fait
   est compté. Les tableaux, générés par le code, sont réputés conformes ; ils
   sont contrôlés quand même, et un écart y serait le signe d'un défaut de la
   liste blanche, pas d'une invention ;
2. **verdict et phase** — le mot du verdict figure tel quel dans le titre de sa
   section et correspond au JSON amont ; aucun substitut adoucissant n'y est
   toléré. Même contrôle pour la phase de cycle de vie ;
3. **bascules** — la section verdict n'affiche que des bascules issues de la
   simulation du code ;
4. **termes interdits et jargon interne** — détectés hors annexe, ils font
   retirer la phrase ;
5. **étude partielle** — chaque section dégradée ou absente porte sa mention.

Portée du contrôle des termes : il s'applique au **texte rédigé par cet agent**.
Les contenus recopiés verbatim des analyses amont (limites, énoncés de
recommandation, constats) ne sont pas réécrits — les réécrire trahirait la
source. Un terme interdit qui y figurerait est compté et signalé, jamais effacé.
"""

from __future__ import annotations

import re

from config import (
    NEGATIONS_TOLEREES,
    NUMEROS_SECTIONS,
    SECTIONS_HORS_CONTROLE_TERMES,
    SECTION_ANNEXE,
    SECTION_PLC,
    SECTION_VERDICT,
    SEUIL_REGENERATION_PCT,
    SUBSTITUTS_VERDICT_INTERDITS,
    TERMES_INTERDITS,
    TERMES_JARGON,
    VERDICT_LISIBLE,
    PHASE_LISIBLE,
    logger,
)
from preparation import ListeBlanche, extraire_nombres
from schemas import (
    ControlesRestitution,
    Injectables,
    SortieNarratif,
    StatutAnalyse,
)

PHASE_POST_VALIDATION: str = "post_validation"

MOTIF_PHRASE = re.compile(r"(?<=[.!?…])\s+")
MOTIF_TITRE_VERDICT = re.compile(r"^##\s+\d+\.\s+Verdict de potentiel\s*:\s*(.+)$", re.M)
MOTIF_TITRE_PLC = re.compile(
    r"^##\s+\d+\.\s+Phase de cycle de vie du marché\s*:\s*(.+)$", re.M
)


def enregistrer_contenu_code(liste: ListeBlanche, injectables: Injectables) -> None:
    """Ajoute à la liste blanche tous les nombres injectés par le code.

    Ces nombres proviennent des entrées, mais sous une autre écriture (format
    français, arrondis d'affichage, bornes de segments recomposées). Les
    enregistrer garantit qu'une phrase citant un chiffre effectivement présent
    dans le rapport n'est pas retirée à tort.

    Args:
        liste: Liste blanche à enrichir.
        injectables: Données injectables, entièrement produites par le code.
    """
    liste.ajouter_json(injectables.model_dump())


def _phrases(paragraphe: str) -> list[str]:
    """Découpe un paragraphe en phrases.

    Args:
        paragraphe: Texte source.

    Returns:
        Les phrases, ponctuation conservée.
    """
    return [p for p in MOTIF_PHRASE.split(paragraphe or "") if p.strip()]


TAILLE_CONTEXTE_NEGATION: int = 45
"""Nombre de caractères examinés avant un terme pour y chercher une négation."""


def _termes_presents(texte: str, termes: tuple[str, ...]) -> list[str]:
    """Repère les termes proscrits réellement affirmés dans un texte.

    Un terme précédé d'une négation n'est pas une affirmation interdite : « aucune
    part de marché ne peut en être déduite » est précisément l'avertissement que
    le rapport doit porter.

    Args:
        texte: Texte à contrôler.
        termes: Termes proscrits, en minuscules.

    Returns:
        Les termes affirmés, sans doublon.
    """
    minuscule = (texte or "").lower()
    trouves: list[str] = []
    for terme in termes:
        for occurrence in re.finditer(re.escape(terme), minuscule):
            debut = max(0, occurrence.start() - TAILLE_CONTEXTE_NEGATION)
            contexte = minuscule[debut : occurrence.start()]
            if any(negation in contexte for negation in NEGATIONS_TOLEREES):
                continue
            trouves.append(terme)
            break
    return trouves


def nettoyer_narratifs(
    narratifs: dict[str, SortieNarratif | None], liste: ListeBlanche
) -> tuple[dict[str, SortieNarratif | None], dict[str, dict[str, int]]]:
    """Retire des narratifs les phrases fautives et compte les retraits.

    Args:
        narratifs: Narratifs produits par les chaînes de rédaction.
        liste: Liste blanche numérique.

    Returns:
        Le couple `(narratifs_nettoyés, compteurs_par_section)`.
    """
    compteurs: dict[str, dict[str, int]] = {}
    for section, sortie in narratifs.items():
        if sortie is None:
            continue
        if section in SECTIONS_HORS_CONTROLE_TERMES:
            continue
        compte = {"nombres": 0, "termes": 0, "mots_avant": 0, "mots_apres": 0}
        paragraphes: list[str] = []
        for paragraphe in sortie.paragraphes:
            compte["mots_avant"] += len(paragraphe.split())
            gardees: list[str] = []
            for phrase in _phrases(paragraphe):
                inconnus = [
                    ecriture
                    for ecriture, valeur in extraire_nombres(phrase)
                    if not liste.contient(valeur)
                ]
                proscrits = _termes_presents(
                    phrase, TERMES_INTERDITS
                ) + _termes_presents(phrase, TERMES_JARGON)
                if inconnus:
                    compte["nombres"] += len(inconnus)
                    logger.warning(
                        "[%s] phrase retirée — nombre(s) hors liste blanche : %s",
                        section,
                        ", ".join(inconnus[:5]),
                    )
                    continue
                if proscrits:
                    compte["termes"] += len(proscrits)
                    logger.warning(
                        "[%s] phrase retirée — terme(s) proscrit(s) : %s",
                        section,
                        ", ".join(proscrits[:5]),
                    )
                    continue
                gardees.append(phrase)
            propre = " ".join(gardees).strip()
            compte["mots_apres"] += len(propre.split())
            if propre:
                paragraphes.append(propre)

        puces: list[str] = []
        for puce in sortie.puces:
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
                continue
            if proscrits:
                compte["termes"] += len(proscrits)
                continue
            puces.append(puce)

        narratifs[section] = SortieNarratif(paragraphes=paragraphes, puces=puces)
        compteurs[section] = compte
    return narratifs, compteurs


def sections_a_regenerer(compteurs: dict[str, dict[str, int]]) -> list[str]:
    """Détermine les sections dont le narratif a trop souffert.

    Args:
        compteurs: Compteurs de retrait par section.

    Returns:
        Les sections à régénérer une seule fois.
    """
    a_reprendre: list[str] = []
    for section, compte in compteurs.items():
        avant = compte["mots_avant"]
        if not avant:
            continue
        retire = 100.0 * (avant - compte["mots_apres"]) / avant
        if retire > SEUIL_REGENERATION_PCT:
            a_reprendre.append(section)
    return a_reprendre


def controler(
    rapport: str,
    resume: str,
    injectables: Injectables,
    liste: ListeBlanche,
    compteurs: dict[str, dict[str, int]],
) -> tuple[ControlesRestitution, list[StatutAnalyse]]:
    """Contrôle le rapport assemblé et publie le compte rendu.

    Args:
        rapport: Rapport Markdown complet.
        resume: Résumé exécutif Markdown.
        injectables: Données injectables.
        liste: Liste blanche numérique.
        compteurs: Compteurs de retrait accumulés lors du nettoyage.

    Returns:
        Le couple `(controles, statuts)`.
    """
    statuts: list[StatutAnalyse] = []
    controles = ControlesRestitution(bascules_recalculees=True)

    # --- 1. Contrôle numérique sur le document entier ----------------------- #
    nombres = extraire_nombres(rapport) + extraire_nombres(resume)
    controles.nb_nombres_verifies = len(nombres)
    inconnus_restants = [
        ecriture for ecriture, valeur in nombres if not liste.contient(valeur)
    ]
    controles.nb_nombres_retires = sum(c["nombres"] for c in compteurs.values())
    controles.termes_interdits_retires = sum(c["termes"] for c in compteurs.values())

    if controles.nb_nombres_retires:
        statuts.append(
            StatutAnalyse(
                phase=PHASE_POST_VALIDATION,
                succes=True,
                message_erreur=(
                    f"{controles.nb_nombres_retires} nombre(s) hors liste blanche : "
                    f"les phrases porteuses ont été retirées du narratif."
                ),
                nb_elements=controles.nb_nombres_retires,
            )
        )
    if controles.termes_interdits_retires:
        statuts.append(
            StatutAnalyse(
                phase=PHASE_POST_VALIDATION,
                succes=True,
                message_erreur=(
                    f"{controles.termes_interdits_retires} terme(s) interdit(s) ou "
                    f"jargon interne : les phrases porteuses ont été retirées."
                ),
                nb_elements=controles.termes_interdits_retires,
            )
        )
    if inconnus_restants:
        statuts.append(
            StatutAnalyse(
                phase=PHASE_POST_VALIDATION,
                succes=False,
                message_erreur=(
                    f"{len(inconnus_restants)} nombre(s) du document restent hors "
                    f"liste blanche après nettoyage — ils proviennent de blocs "
                    f"générés par le code, ce qui signale un défaut de la liste "
                    f"blanche et non une invention : "
                    f"{', '.join(sorted(set(inconnus_restants))[:10])}."
                ),
                nb_elements=len(inconnus_restants),
            )
        )

    # --- 2. Termes proscrits dans les contenus recopiés --------------------- #
    corps_hors_annexe = rapport.split(f"## {NUMEROS_SECTIONS[SECTION_ANNEXE]}.")[0]
    recopies = _termes_presents(corps_hors_annexe, TERMES_INTERDITS)
    if recopies:
        statuts.append(
            StatutAnalyse(
                phase=PHASE_POST_VALIDATION,
                succes=True,
                message_erreur=(
                    f"terme(s) proscrit(s) présent(s) dans des contenus recopiés "
                    f"verbatim des analyses amont, conservés tels quels et "
                    f"signalés : {', '.join(sorted(set(recopies)))}."
                ),
                nb_elements=len(recopies),
            )
        )

    # --- 3. Verdict et phase ------------------------------------------------ #
    attendu = VERDICT_LISIBLE.get(injectables.verdict_brut, injectables.verdict_brut)
    trouve = MOTIF_TITRE_VERDICT.search(rapport)
    titre_verdict = trouve.group(1).strip() if trouve else ""
    controles.verdict_conforme = titre_verdict == attendu and not _termes_presents(
        titre_verdict, SUBSTITUTS_VERDICT_INTERDITS
    )
    if not controles.verdict_conforme:
        statuts.append(
            StatutAnalyse(
                phase=PHASE_POST_VALIDATION,
                succes=False,
                message_erreur=(
                    f"le titre de la section verdict porte « {titre_verdict} » alors "
                    f"que l'analyse amont conclut « {attendu} »."
                ),
            )
        )

    if injectables.phase_brute:
        attendue = PHASE_LISIBLE.get(injectables.phase_brute, injectables.phase_brute)
        trouvee = MOTIF_TITRE_PLC.search(rapport)
        controles.phase_conforme = bool(trouvee) and trouvee.group(1).strip() == attendue
        if not controles.phase_conforme:
            statuts.append(
                StatutAnalyse(
                    phase=PHASE_POST_VALIDATION,
                    succes=False,
                    message_erreur=(
                        f"le titre de la section cycle de vie ne porte pas la phase "
                        f"« {attendue} » telle quelle."
                    ),
                )
            )
    else:
        controles.phase_conforme = None

    # --- 4. Bascules -------------------------------------------------------- #
    section_verdict = rapport.split(f"## {NUMEROS_SECTIONS[SECTION_VERDICT]}.")[-1].split(
        "\n---"
    )[0]
    enonces = {b.enonce for b in injectables.bascules}
    lignes_bascule = [
        ligne
        for ligne in section_verdict.splitlines()
        if ligne.strip().startswith("- ") and "faire passer le verdict" in ligne
    ]
    non_simulees = [
        ligne for ligne in lignes_bascule if ligne.strip()[2:].strip() not in enonces
    ]
    controles.bascules_recalculees = not non_simulees
    if non_simulees:
        statuts.append(
            StatutAnalyse(
                phase=PHASE_POST_VALIDATION,
                succes=False,
                message_erreur=(
                    f"{len(non_simulees)} bascule(s) affichée(s) ne proviennent pas "
                    f"de la simulation du code."
                ),
                nb_elements=len(non_simulees),
            )
        )

    # --- 5. Étude partielle ------------------------------------------------- #
    mentions: list[str] = []
    for section in injectables.sections_degradees + injectables.sections_absentes:
        if section == SECTION_PLC:
            present = "Phase de cycle de vie non déterminée" in rapport
        else:
            present = injectables.mentions_partielles.get(section, "§§") in rapport
        if present:
            mentions.append(section)
        else:
            statuts.append(
                StatutAnalyse(
                    phase=PHASE_POST_VALIDATION,
                    succes=False,
                    message_erreur=(
                        f"la section « {section} » est incomplète mais ne porte pas "
                        f"sa mention d'étude partielle."
                    ),
                )
            )
    controles.mentions_etude_partielle = mentions

    if not statuts:
        statuts.append(
            StatutAnalyse(phase=PHASE_POST_VALIDATION, succes=True, nb_elements=0)
        )
    logger.debug(
        "post-validation : %d nombre(s) vérifié(s), %d retiré(s)",
        controles.nb_nombres_verifies,
        controles.nb_nombres_retires,
    )
    return controles, statuts
