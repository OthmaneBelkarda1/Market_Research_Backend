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
    CONTRAT_SOUS_BLOCS,
    LEXIQUE_ENUMERATIONS,
    ECRANS_HORS_CONTROLE_TERMES,
    ECRAN_DECISION,
    ECRAN_METHODE,
    BUDGET_MOTS,
    BUDGET_MOTS_TOTAL,
    LIBELLES_CINQ_FORCES,
    LIBELLES_VERDICT,
    MARQUEUR_GABARIT_V2,
    MOTS_OUTILS_FIN_INTERDITS,
    NEGATIONS_TOLEREES,
    PHASE_LISIBLE,
    PHRASES_STANDARD,
    REPLI_INTERDIT_V2,
    SEUIL_REGENERATION_PCT,
    SOURCES_LIGNE_SOURCES,
    SIGLES_INTERDITS,
    SOUS_BLOCS_REDIGES,
    SUBSTITUTS_VERDICT_INTERDITS,
    TERMES_INTERDITS,
    TERMES_JARGON,
    VERDICT_LISIBLE,
    logger,
    termes_interdits_presents,
)
from preparation import ListeBlanche, extraire_nombres
from preparation_v2 import LIBELLES_SOURCES
from redaction_v2 import ecarts_au_contrat, rangs_chiffrables
from validation import _termes_presents
from schemas import (
    ControlesRestitution,
    Injectables,
    SectionProduite,
    SortieEcran,
    StatutAnalyse,
)

PHASE_POST_VALIDATION_V2: str = "post_validation_v2"

# Le titre porte le libellé métier PUIS sa traduction en clair, séparés d'un
# tiret cadratin : « ## Décision : No-go — **ne pas lancer ce produit** ». Le
# contrôle ne lit que le libellé, celui que l'analyse amont a produit et sur
# lequel porte la traçabilité ; la traduction est un ajout d'affichage.
MOTIF_TITRE_DECISION = re.compile(
    r"^##\s+Décision\s*:\s*([^—]+?)\s*(?:—.*)?$", re.M
)
MOTIF_LIGNE_VERDICT = re.compile(r"^Verdict calculé\s*:\s*([^·]+)·", re.M)
MOTIF_CELLULE_TRONQUEE = re.compile(r"…\s*(?:\||$)", re.M)

# Ce qui doit être inspecté pour une troncature : une puce, une cellule de
# tableau, un titre. Le texte d'une cellule est isolé du tuyautage Markdown, et
# les lignes de séparation (`| --- |`) sont écartées.
MOTIF_PUCE = re.compile(r"^\s*[-*]\s+(.*\S)\s*$", re.M)
MOTIF_TITRE = re.compile(r"^#{2,4}\s+(.*\S)\s*$", re.M)
MOTIF_SEPARATEUR_TABLEAU = re.compile(r"^[\s|:-]+$")
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

    def plancher(cle: str) -> int:
        """Nombre de puces au-dessous duquel le sous-bloc sort du gabarit."""
        contrat = CONTRAT_SOUS_BLOCS.get(cle)
        return contrat.nb_puces_min if contrat else 1

    while total() > budget:
        for cle in reversed(ordre):
            if len(conserve.get(cle, [])) > plancher(cle):
                conserve[cle].pop()
                retirees += 1
                break
        else:
            # Chaque sous-bloc est descendu à son plancher de gabarit : on ne va
            # pas plus bas. Un « Pourquoi » à deux puces n'est plus le gabarit,
            # et un léger dépassement de budget vaut mieux qu'un écran hors
            # contrat — que `gabarit_conforme` refuserait de toute façon.
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

    Le repli n'est PAS une coupe. Une compression rejetée rend le texte INTÉGRAL :
    le run 8609db9e a livré « … acquise ou », « … kit complet plug and » et des
    titres d'opportunité amputés à douze mots, tous produits par ce repli quand
    il coupait encore. Un texte trop long dépasse un budget de forme ; un texte
    coupé dit autre chose que ce que l'analyse a conclu. Le second est un défaut,
    le premier une gêne.

    Args:
        originaux: Textes d'origine.
        compresses: Textes renvoyés par le modèle, ou `None` si la chaîne a échoué.
        max_mots: Budget de mots par texte.

    Returns:
        Le couple `(textes_retenus, nb_compressions_acceptées)`.
    """
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
        retenus.append(original)
    return retenus, acceptees


def _textes_inspectables(rapport: str) -> list[tuple[str, str]]:
    """Découpe le rapport en fragments où une troncature se verrait.

    Puces, titres et cellules de tableau : les trois formes qui portent un texte
    autonome. Le corps des paragraphes en est absent — le v2 n'en produit pas —
    et les lignes de séparation de tableau sont écartées.

    Args:
        rapport: Rapport Markdown complet.

    Returns:
        Les couples `(nature, texte)` à inspecter.
    """
    fragments: list[tuple[str, str]] = [
        ("puce", texte) for texte in MOTIF_PUCE.findall(rapport)
    ]
    fragments += [("titre", texte) for texte in MOTIF_TITRE.findall(rapport)]
    for ligne in rapport.splitlines():
        depouillee = ligne.strip()
        if not depouillee.startswith("|") or MOTIF_SEPARATEUR_TABLEAU.match(depouillee):
            continue
        for cellule in depouillee.strip("|").split("|"):
            if cellule.strip():
                fragments.append(("cellule", cellule.strip()))
    return fragments


def _est_tronque(texte: str) -> str:
    """Dit si un texte porte la signature d'une coupe machine.

    Trois signatures, du plus au moins évident : l'ellipse, la virgule finale, et
    le mot-outil final — « … acquise ou », « … plug and ». Aucune phrase française
    rédigée ne se termine ainsi ; un texte qui le fait a été coupé.

    Le gras et la ponctuation de fin sont retirés avant l'examen pour que
    « **Leurs prix** » ne compte pas son astérisque comme dernier caractère.

    Args:
        texte: Fragment à examiner.

    Returns:
        Le motif de troncature constaté, ou une chaîne vide.
    """
    nettoye = texte.replace("**", "").replace("`", "").strip()
    if not nettoye:
        return ""
    if nettoye.endswith("…") or nettoye.endswith("..."):
        return "se termine par une ellipse"
    if nettoye.endswith(","):
        return "se termine par une virgule"
    dernier = nettoye.rstrip(".;:!?)\u00a0").split()
    if dernier and dernier[-1].lower() in MOTS_OUTILS_FIN_INTERDITS:
        return f"se termine par le mot-outil « {dernier[-1]} »"
    return ""


MOTIF_PAGES_LIGNE_SOURCES = re.compile(r"Web\s*\((\d[\d\s\u202f\u00a0]*)\s*pages?\)")
MOTIF_PAGES_METHODE = re.compile(
    r"^\|\s*[Rr]echerche web\s*\|\s*(\d[\d\s\u202f\u00a0]*)\s*\|", re.M
)


def _compteurs_pages_coherents(rapport: str) -> bool:
    """Vérifie que les pages web sont comptées une seule fois dans le document.

    Le run 8609db9e annonçait « Web (5 pages) » à l'écran 0 et deux documents au
    tableau de méthode : deux chiffres pour une même collecte, dont le lecteur ne
    peut arbitrer aucun. Deux compteurs valent zéro compteur.

    Args:
        rapport: Rapport Markdown complet.

    Returns:
        `False` seulement si les deux chiffres existent ET diffèrent. Un chiffre
        absent n'est pas une incohérence : d'autres contrôles s'en chargent.
    """
    ligne = MOTIF_PAGES_LIGNE_SOURCES.search(rapport)
    methode = MOTIF_PAGES_METHODE.search(rapport)
    if not ligne or not methode:
        return True

    def entier(texte: str) -> int:
        return int("".join(c for c in texte if c.isdigit()))

    return entier(ligne.group(1)) == entier(methode.group(1))


def controler_gabarit(
    ecrans: dict[str, SortieEcran | None],
    injectables: Injectables,
    *,
    structurels_seuls: bool = False,
) -> list[str]:
    """Confronte les écrans rendus au contrat de chaque sous-bloc.

    C'est le contrôle qui manquait. Jusqu'ici le gabarit n'était tenu que par les
    consignes envoyées au modèle : rien ne vérifiait qu'un « Pourquoi » sortait
    bien avec trois puces chiffrées, ni que les cinq puces des concurrents
    portaient leurs libellés. Le run 8609db9e n'en portait aucune, et tous les
    contrôles étaient au vert.

    Un sous-bloc qui affiche sa phrase standard est hors contrat : il n'a pas été
    rédigé, il n'a rien à respecter.

    Args:
        ecrans: Sorties narratives par écran.
        injectables: Données injectables, pour les sous-blocs standards.
        structurels_seuls: Ne relever que les écarts sans recours. C'est ce que
            regarde le verdict bloquant ; le compte rendu, lui, publie tous les
            écarts, dépassements de mots compris.

    Returns:
        Les écarts constatés, vide si le gabarit est tenu.
    """
    ecarts: list[str] = []
    for ecran, sous_blocs in SOUS_BLOCS_REDIGES.items():
        sortie = ecrans.get(ecran)
        attendus = [
            sous_bloc
            for sous_bloc in sous_blocs
            if sous_bloc not in injectables.sous_blocs_standards
        ]
        if not attendus:
            continue
        if sortie is None:
            ecarts.append(f"écran « {ecran} » : aucun narratif produit")
            continue
        ecarts.extend(
            ecarts_au_contrat(
                sortie,
                attendus,
                rangs_chiffrables(injectables),
                structurels_seuls=structurels_seuls,
            )
        )
    return ecarts


MOTIF_CITATION = re.compile(r"^\s*>.*$", re.M)
MOTIF_COMMENTAIRE = re.compile(r"<!--.*?-->", re.S)
MOTIF_IDENTIFIANT_CODE = re.compile(r"\b[a-zà-ÿ]+(?:_[a-zà-ÿ0-9]+)+\b")


def corps_lisible(rapport: str) -> str:
    """Isole ce qui doit être écrit en français courant, dans les écrans 0 à 3.

    Trois retraits, et ils ne sont pas négociables :

    - **l'écran 4**, où le vocabulaire technique reste admis et où le glossaire
      le définit ;
    - **les citations clients**, dans leur langue d'origine, qu'aucune règle de
      vocabulaire ne doit atteindre ;
    - **les commentaires HTML** de traçabilité, invisibles au lecteur.

    Les noms de marques et de produits ne sont pas retirés : ils ne contiennent
    pas de terme du lexique, et les retirer supposerait une liste que personne ne
    tient à jour.

    Args:
        rapport: Rapport Markdown complet.

    Returns:
        Le texte soumis aux contrôles de vocabulaire.
    """
    corps = rapport.split("## Méthode et limites")[0]
    corps = MOTIF_COMMENTAIRE.sub(" ", corps)
    return MOTIF_CITATION.sub(" ", corps)


def valeurs_techniques(texte: str) -> list[str]:
    """Relève les identifiants de code et sigles internes d'un texte.

    Un identifiant se reconnaît à son tiret bas — `effet_de_mode`,
    `marketplace_pays`, `court_terme` — et un sigle interne à sa présence dans
    `SIGLES_INTERDITS`. Les deux ont en commun de n'avoir jamais été écrits pour
    un lecteur.

    Args:
        texte: Texte à contrôler.

    Returns:
        Les valeurs relevées, sans doublon.
    """
    trouves = list(MOTIF_IDENTIFIANT_CODE.findall(texte))
    trouves += [
        sigle
        for sigle in SIGLES_INTERDITS
        if re.search(rf"\b{re.escape(sigle)}\b", texte)
    ]
    return list(dict.fromkeys(trouves))


def incoherences_inter_ecrans(rapport: str, injectables: Injectables) -> list[str]:
    """Relève les affirmations d'absence contredites par un autre écran.

    Le run de référence publiait « Aucun avis client n'est présent dans les
    données fournies » à l'écran 2, alors que l'écran 1 analysait vingt-six avis
    Amazon. Pour le lecteur, l'un des deux écrans ment, et il n'a aucun moyen de
    savoir lequel — c'est le genre de contradiction qui disqualifie tout le
    rapport, pas seulement la phrase.

    Args:
        rapport: Rapport Markdown complet.
        injectables: Données injectables, qui disent ce qui a réellement été
            collecté.

    Returns:
        Les contradictions constatées.
    """
    corps = corps_lisible(rapport)
    a_des_avis = bool(injectables.pain_points or injectables.tableau_sentiment)
    incoherences: list[str] = []
    if a_des_avis:
        for motif in (
            r"[Aa]ucun avis client n'est présent",
            r"[Aa]ucun avis n'est disponible",
            r"[Pp]as d'avis client",
        ):
            if re.search(motif, corps):
                incoherences.append(
                    "un écran affirme qu'aucun avis client n'est disponible, alors "
                    "que l'écran consommateur en analyse"
                )
                break
    return incoherences


def controler_v2(
    rapport: str,
    resume: str,
    injectables: Injectables,
    liste: ListeBlanche,
    compteurs: dict[str, dict[str, int]],
    sections: list[SectionProduite],
    ecrans: dict[str, SortieEcran | None] | None = None,
) -> tuple[ControlesRestitution, list[StatutAnalyse]]:
    """Contrôle le rapport décisionnel assemblé et publie le compte rendu.

    Quatre des contrôles sont BLOQUANTS : `gabarit_conforme`,
    `aucun_repli_interdit`, `aucune_troncature` et `ligne_sources_complete`. Ils
    ne dégradent pas le rapport, ils l'empêchent — `agent.restituer` lève
    `RedactionImpossible` et le module sort sans écrire un fichier. C'est la
    leçon du run 8609db9e : un rapport livré est un rapport lu, et un rapport lu
    qui ne dit rien coûte plus cher qu'une étude visiblement échouée.

    Args:
        rapport: Rapport Markdown complet.
        resume: Résumé exécutif Markdown.
        injectables: Données injectables.
        liste: Liste blanche numérique.
        compteurs: Compteurs de retrait accumulés au nettoyage.
        sections: Écrans produits, avec leur décompte de mots.
        ecrans: Sorties narratives par écran, pour le contrôle de gabarit.
            `None` saute ce contrôle — réservé aux appels qui n'ont pas les
            écrans sous la main.

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

    # --- 12. Conformité au gabarit, sous-bloc par sous-bloc (BLOQUANT) ------ #
    if ecrans is not None:
        # `sous_blocs_non_conformes` publie TOUT — c'est le compte rendu, et un
        # dépassement de mots doit s'y voir. `gabarit_conforme`, lui, ne retient
        # que les écarts sans recours : c'est le drapeau que `agent.restituer`
        # transforme en refus, et refuser un rapport entier parce qu'une puce
        # fait 34 mots au lieu de 30 rendrait le module inutilisable. Le
        # dépassement reste compté par `budgets_respectes`, qui n'a jamais
        # bloqué. Voir l'amendement A7.
        controles.sous_blocs_non_conformes = controler_gabarit(ecrans, injectables)
        bloquants = controler_gabarit(ecrans, injectables, structurels_seuls=True)
        controles.gabarit_conforme = not bloquants
        if controles.sous_blocs_non_conformes:
            echec(
                f"{len(controles.sous_blocs_non_conformes)} sous-bloc(s) "
                f"s'écartent du gabarit "
                f"({len(bloquants)} sans recours) : "
                f"{' ; '.join(controles.sous_blocs_non_conformes[:6])}.",
                len(controles.sous_blocs_non_conformes),
            )

    # --- 13. Aucun repli hors phrases standard (BLOQUANT) ------------------- #
    replis = [motif for motif in REPLI_INTERDIT_V2 if motif in rapport or motif in resume]
    controles.aucun_repli_interdit = not replis
    if replis:
        echec(
            f"texte(s) de repli interdits dans le rapport : "
            f"{', '.join(replis)}. Les seuls replis admis sont les phrases "
            f"standard d'injectable vide ({len(PHRASES_STANDARD)} formulations), "
            f"et un échec de rédaction n'en est pas une.",
            len(replis),
        )

    # --- 14. Aucune troncature (BLOQUANT) ----------------------------------- #
    coupes = [
        f"{nature} « {texte[:60]} » : {motif}"
        for nature, texte in _textes_inspectables(rapport)
        if (motif := _est_tronque(texte))
    ]
    controles.aucune_troncature = not coupes
    if coupes:
        echec(
            f"{len(coupes)} fragment(s) portent la signature d'une coupe machine : "
            f"{' ; '.join(coupes[:5])}.",
            len(coupes),
        )

    # --- 15. Ligne « Sources analysées » : les six, toujours (BLOQUANT) ----- #
    if injectables.ligne_sources:
        absentes_ligne = [
            source
            for source in SOURCES_LIGNE_SOURCES
            if LIBELLES_SOURCES.get(source, source) not in injectables.ligne_sources
        ]
        controles.ligne_sources_complete = not absentes_ligne
        if absentes_ligne:
            echec(
                f"{len(absentes_ligne)} source(s) manquent à la ligne « Sources "
                f"analysées » : {', '.join(absentes_ligne)}. Une source omise est "
                f"une source que le lecteur ne peut pas savoir vide.",
                len(absentes_ligne),
            )

    # --- 16. Un seul compteur de pages web ---------------------------------- #
    controles.compteurs_coherents = _compteurs_pages_coherents(rapport)
    if not controles.compteurs_coherents:
        echec(
            "le nombre de pages web de la ligne « Sources analysées » diffère de "
            "celui du tableau de méthode : deux compteurs pour une même collecte."
        )

    # --- 17. Lexique : le rapport se lit sans dictionnaire ------------------ #
    corps_a_lire = corps_lisible(rapport)
    termes = termes_interdits_presents(corps_a_lire)
    controles.lexique_conforme = not termes
    if termes:
        echec(
            f"{len(termes)} terme(s) d'analyste subsistent dans les écrans 0 à 3 : "
            f"{', '.join(termes[:8])}. Le lecteur visé n'a jamais fait d'étude de "
            f"marché.",
            len(termes),
        )

    # --- 18. Aucun identifiant de code ni sigle interne --------------------- #
    techniques = [
        valeur
        for valeur in valeurs_techniques(corps_a_lire)
        # Une valeur déjà traduite par le code n'a pas à être relevée deux fois :
        # si elle subsiste, c'est que la traduction n'a pas été appliquée là.
        if valeur in LEXIQUE_ENUMERATIONS or "_" in valeur or valeur in SIGLES_INTERDITS
    ]
    controles.valeurs_techniques_absentes = not techniques
    if techniques:
        echec(
            f"{len(techniques)} valeur(s) techniques atteignent le lecteur : "
            f"{', '.join(techniques[:8])}. Ce sont des identifiants de code.",
            len(techniques),
        )

    # --- 19. Cohérence entre écrans ----------------------------------------- #
    contradictions = incoherences_inter_ecrans(rapport, injectables)
    controles.coherence_inter_ecrans = not contradictions
    if contradictions:
        echec(
            "contradiction entre écrans : " + " ; ".join(contradictions) + ".",
            len(contradictions),
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
