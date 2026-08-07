"""Construction des données injectables — **aucun appel LLM dans ce module**.

Tout ce qui est chiffré, tabulaire ou structurel dans le rapport naît ici :
tableaux Markdown, badges de confiance, verbatims sélectionnés, limites
consolidées, liste blanche numérique, et surtout **simulation des bascules de
verdict**.

⚠️ **Les bascules de verdict sont recalculées, jamais recopiées.** L'audit du
run n°1 a montré que le texte libre de l'analyse amont annonçait des bascules
incompatibles avec sa propre règle : trois critères y étaient présentés comme
faisant passer le verdict à « positif » alors que la règle l'interdit tant qu'un
critère est noté 0. Ce module rejoue la règle sur toutes les mutations
mono-critère et n'affiche que les bascules réelles.
"""

from __future__ import annotations

import bisect
import math
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from config import (
    BADGES_CONFIANCE,
    BADGE_INCONNU,
    BESOIN_PARAMETRES_REGLE,
    CRITERE_DEMANDE_F5,
    CRITERE_DIFFERENCIATION_F5,
    DECIMALES_MONTANT,
    DECIMALES_POURCENTAGE,
    ENCART_PLC_ABSENTE,
    ENCART_PLC_NON_DECLENCHEE,
    ENTREE_CONCURRENCE,
    ENTREE_INSIGHTS,
    ENTREE_PLC,
    ENTREE_RECOMMANDATIONS,
    FAMILLE_AUTRES,
    FORMAT_DATE,
    LIBELLES_ENTREES,
    LIBELLE_FAMILLE_AUTRES,
    DETAILS_ENTREES,
    LIMITES_FAMILLES,
    MAX_ANGLES_RAPPORT,
    MAX_CARACTERES_BADGE,
    MAX_CARACTERES_CELLULE,
    MAX_CARACTERES_JUSTIFICATION,
    MAX_DONNEES_A_COMPLETER,
    MAX_LIMITES_PAR_FAMILLE,
    MAX_NON_EVALUABLES_POSITIF_F5,
    MAX_NORMES_RAPPORT,
    MAX_OPPORTUNITES_RAPPORT,
    MAX_RISQUES_RAPPORT,
    MAX_VERBATIM_CARACTERES,
    MENTION_ETUDE_PARTIELLE,
    MIN_CRITERES_EVALUES_F5,
    MOTIFS_NETTOYAGE,
    MOTIF_PREFIXE_AGENT,
    MOTIF_REFERENCE_INTERNE,
    MOTIF_REFERENCE_TECHNIQUE,
    SUBSTITUTIONS_TEXTE,
    NB_CONCURRENTS_TABLEAU,
    NB_FAITS_CLES_SYNTHESE,
    NB_PAIN_POINTS_RAPPORT,
    NB_RECOMMANDATIONS_SYNTHESE,
    PHASE_LISIBLE,
    SCORE_MAX_CRITERE,
    SCORES_MUTATION,
    SECTION_CONCURRENCE,
    SECTION_CONSOMMATEURS,
    SECTION_DEMANDE,
    SECTION_PLC,
    SECTION_SYNTHESE,
    SECTION_VERDICT,
    SEPARATEUR_DECIMAL,
    SEPARATEUR_MILLIERS,
    SEUIL_NEGATIF_F5,
    SEUIL_POSITIF_F5,
    TOLERANCE_ARRONDI_PCT,
    VERDICT_INDETERMINE,
    VERDICT_LISIBLE,
    VERDICT_NEGATIF,
    VERDICT_POSITIF,
    logger,
)
from schemas import (
    Bascule,
    EntreesChargees,
    Injectables,
    StatutAnalyse,
    Verbatim,
)

PHASE_PREPARATION: str = "preparation"

REFS_RATIO_POURCENTAGE: frozenset[str] = frozenset(
    {"tendances.indicateurs.momentum_90j"}
)
"""Refs dont la valeur est un ratio et s'affiche en pourcentage."""

BOOLEENS_LISIBLES: dict[str, str] = {"True": "oui", "False": "non"}
"""Les drapeaux booléens des dossiers amont s'écrivent en clair dans le rapport."""

MOTS_BASCULE: tuple[str, ...] = (
    "bascul",
    "ferait passer",
    "ferait évoluer",
    "changeant le verdict",
    "changerait le verdict",
    "faisant passer",
    "passerait de",
    "passerait à",
    "ferait basculer",
)
"""Marqueurs d'une conclusion de bascule dans un texte libre amont."""

CRITERES_LISIBLES: dict[str, str] = {
    "demande": "Dynamique de la demande",
    "intensite": "Intensité concurrentielle soutenable",
    "differenciation": "Différenciation crédible",
    "adequation": "Adéquation aux besoins avérés",
    "viabilite_prix": "Viabilité prix",
}

MOTIFS_CRITERES: dict[str, tuple[str, ...]] = {
    "demande": ("demande",),
    "intensite": ("intensite", "intensité"),
    "differenciation": ("differenciation", "différenciation"),
    "adequation": ("adequation", "adéquation"),
    "viabilite_prix": ("viabilite_prix", "viabilité prix", "viabilite prix"),
}

MOTIF_NOMBRE = re.compile(
    r"[-+±~≈]?\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|[-+±~≈]?\d+(?:[.,]\d+)?"
)
"""Extraction tolérante : formats français et anglais, séparateurs de milliers."""


# =========================================================================== #
# Formats
# =========================================================================== #


def formater_nombre(valeur: float, decimales: int | None = None) -> str:
    """Formate un nombre à la française.

    Args:
        valeur: Valeur numérique.
        decimales: Nombre de décimales imposé, ou `None` pour l'ajuster.

    Returns:
        La valeur formatée, virgule décimale et séparateur de milliers.
    """
    if decimales is None:
        decimales = 0 if float(valeur).is_integer() else DECIMALES_MONTANT
    texte = f"{valeur:,.{decimales}f}"
    texte = texte.replace(",", "\x00").replace(".", SEPARATEUR_DECIMAL)
    texte = texte.replace("\x00", SEPARATEUR_MILLIERS)
    if decimales > 0 and SEPARATEUR_DECIMAL in texte:
        texte = texte.rstrip("0").rstrip(SEPARATEUR_DECIMAL)
    return texte


def formater_pourcentage(valeur: float) -> str:
    """Formate un pourcentage à une décimale.

    Args:
        valeur: Valeur en points de pourcentage.

    Returns:
        La valeur suivie du signe pour cent.
    """
    return f"{formater_nombre(valeur, DECIMALES_POURCENTAGE)} %"


def formater_montant(valeur: float, devise: str) -> str:
    """Formate un montant avec sa devise.

    Args:
        valeur: Montant.
        devise: Code devise, jamais converti.

    Returns:
        Le montant formaté suivi de sa devise.
    """
    return f"{formater_nombre(valeur, DECIMALES_MONTANT)} {devise}"


def formater_date(horodatage: str | None) -> str:
    """Formate un horodatage ISO 8601 en date lisible.

    Args:
        horodatage: Horodatage ISO 8601, ou `None`.

    Returns:
        La date au format `JJ/MM/AAAA`, ou « date inconnue ».
    """
    if not horodatage:
        return "date inconnue"
    try:
        date = datetime.fromisoformat(str(horodatage).replace("Z", "+00:00"))
    except ValueError:
        return "date inconnue"
    return date.strftime(FORMAT_DATE)


def _valeur_lisible(ref: str, valeur: str) -> str:
    """Rend lisible une valeur brute recopiée d'un dossier amont.

    Args:
        ref: Référence de l'élément, qui décide du traitement en pourcentage.
        valeur: Valeur brute.

    Returns:
        La valeur formatée, ou la valeur d'origine si elle n'est pas numérique.
    """
    texte = (valeur or "").strip()
    if texte in BOOLEENS_LISIBLES:
        return BOOLEENS_LISIBLES[texte]
    try:
        nombre = float(texte)
    except ValueError:
        # Valeur composite : seules les décimales à rallonge sont ramenées à une
        # précision lisible, le reste du libellé est conservé tel quel.
        return re.sub(
            r"\d+\.\d{4,}",
            lambda trouve: formater_nombre(float(trouve.group()), 2),
            texte,
        )
    if ref in REFS_RATIO_POURCENTAGE:
        return formater_pourcentage(nombre * 100)
    return formater_nombre(nombre, None if float(nombre).is_integer() else 2)


def franciser(texte: str) -> str:
    """Remplace le point décimal par une virgule dans un texte chiffré.

    Args:
        texte: Texte contenant des nombres à l'anglaise.

    Returns:
        Le texte avec des décimales à la française.
    """
    return re.sub(r"(?<=\d)\.(?=\d)", SEPARATEUR_DECIMAL, texte or "")


def tronquer(texte: str, taille: int = MAX_CARACTERES_CELLULE) -> str:
    """Tronque proprement un texte à la limite d'un mot.

    Args:
        texte: Texte source.
        taille: Longueur maximale.

    Returns:
        Le texte, suivi d'une ellipse s'il a été coupé.
    """
    propre = " ".join((texte or "").split())
    if len(propre) <= taille:
        return propre
    coupe = propre[:taille].rsplit(" ", 1)[0]
    return f"{coupe}…"


def tableau(entetes: list[str], lignes: list[list[str]]) -> str:
    """Assemble un tableau Markdown.

    Args:
        entetes: Libellés de colonnes.
        lignes: Lignes de cellules, déjà formatées.

    Returns:
        Le tableau Markdown, ou une chaîne vide s'il n'y a aucune ligne.
    """
    if not lignes:
        return ""
    def cellule(valeur: str) -> str:
        return " ".join(str(valeur or "—").replace("|", "\\|").split())

    rendu = ["| " + " | ".join(cellule(e) for e in entetes) + " |"]
    rendu.append("|" + "|".join("---" for _ in entetes) + "|")
    for ligne in lignes:
        rendu.append("| " + " | ".join(cellule(c) for c in ligne) + " |")
    return "\n".join(rendu)


# =========================================================================== #
# Liste blanche numérique
# =========================================================================== #


class ListeBlanche:
    """Ensemble des valeurs numériques citables dans le rapport.

    Elle est alimentée par deux sources, et par elles seules :

    1. les **fichiers d'entrée**, parcourus récursivement — valeurs numériques
       comme nombres présents dans les textes (justifications, commentaires) ;
    2. les **blocs générés par le code** — tableaux, badges, encarts —, dont les
       nombres proviennent eux aussi des entrées mais sous une autre écriture.

    Un nombre du rapport qui n'y figure pas n'a pu être produit que par le
    modèle : la phrase qui le porte est retirée.
    """

    def __init__(self) -> None:
        """Initialise une liste blanche vide."""
        self._valeurs: set[float] = set()
        self._triees: list[float] = []
        self._a_jour: bool = False

    def ajouter(self, valeur: float) -> None:
        """Ajoute une valeur et toutes ses variantes admises.

        Args:
            valeur: Valeur numérique de référence.
        """
        if not math.isfinite(valeur):
            return
        variantes = {
            valeur,
            round(valeur, 1),
            round(valeur, 2),
            float(round(valeur)),
            abs(valeur),
            round(abs(valeur), 1),
            round(abs(valeur), 2),
        }
        if 0 < abs(valeur) <= 1:
            centieme = valeur * 100
            variantes.update(
                {
                    centieme,
                    round(centieme, 1),
                    round(centieme, 2),
                    float(round(centieme)),
                    abs(centieme),
                    round(abs(centieme), 1),
                    float(round(abs(centieme))),
                }
            )
        self._valeurs.update(variantes)
        self._a_jour = False

    def ajouter_texte(self, texte: str) -> None:
        """Ajoute tous les nombres présents dans un texte.

        Args:
            texte: Texte source, potentiellement vide.
        """
        for brut in MOTIF_NOMBRE.findall(texte or ""):
            valeur = convertir_nombre(brut)
            if valeur is not None:
                self.ajouter(valeur)

    def ajouter_json(self, objet: Any) -> None:
        """Parcourt récursivement un objet JSON et en collecte les nombres.

        Args:
            objet: Objet JSON décodé.
        """
        if isinstance(objet, bool):
            return
        if isinstance(objet, (int, float)):
            self.ajouter(float(objet))
        elif isinstance(objet, str):
            self.ajouter_texte(objet)
        elif isinstance(objet, dict):
            for cle, valeur in objet.items():
                self.ajouter_texte(str(cle))
                self.ajouter_json(valeur)
        elif isinstance(objet, list):
            for element in objet:
                self.ajouter_json(element)

    def contient(self, valeur: float) -> bool:
        """Indique si une valeur est admise, à la tolérance d'arrondi près.

        Args:
            valeur: Valeur extraite du rapport.

        Returns:
            Vrai si une valeur admise en est assez proche.
        """
        if not self._a_jour:
            self._triees = sorted(self._valeurs)
            self._a_jour = True
        if not self._triees:
            return False
        position = bisect.bisect_left(self._triees, valeur)
        for voisin in self._triees[max(0, position - 1) : position + 2]:
            if abs(voisin - valeur) <= max(1e-6, TOLERANCE_ARRONDI_PCT):
                return True
        return False

    def __len__(self) -> int:
        """Retourne le nombre de valeurs admises.

        Returns:
            Le cardinal de la liste blanche.
        """
        return len(self._valeurs)


def convertir_nombre(brut: str) -> float | None:
    """Convertit une écriture numérique française ou anglaise en flottant.

    Args:
        brut: Écriture brute, telle qu'extraite d'un texte.

    Returns:
        La valeur, ou `None` si la conversion échoue.
    """
    texte = (brut or "").strip().lstrip("±~≈")
    texte = texte.replace(" ", "").replace(" ", "").replace(" ", "")
    texte = texte.replace(",", ".")
    if texte.count(".") > 1:
        texte = texte.replace(".", "", texte.count(".") - 1)
    try:
        return float(texte)
    except ValueError:
        return None


def extraire_nombres(texte: str) -> list[tuple[str, float]]:
    """Extrait les nombres d'un texte avec leur écriture d'origine.

    Args:
        texte: Texte à analyser.

    Returns:
        La liste des couples `(écriture, valeur)`.
    """
    trouves: list[tuple[str, float]] = []
    for brut in MOTIF_NOMBRE.findall(texte or ""):
        valeur = convertir_nombre(brut)
        if valeur is not None:
            trouves.append((brut, valeur))
    return trouves


# =========================================================================== #
# Simulation de la règle de verdict
# =========================================================================== #


class ParametresRegle:
    """Seuils effectifs de la règle de verdict de l'analyse amont."""

    def __init__(
        self,
        min_criteres: int,
        seuil_positif: int,
        seuil_negatif: int,
        max_non_evaluables: int,
        lus_dans_enonce: bool,
    ) -> None:
        """Initialise les seuils.

        Args:
            min_criteres: Nombre minimal de critères évalués.
            seuil_positif: Score total minimal pour un verdict positif.
            seuil_negatif: Score total maximal pour un verdict négatif.
            max_non_evaluables: Nombre maximal de critères non évaluables
                compatible avec un verdict positif.
            lus_dans_enonce: Vrai si les seuils ont été relus dans l'énoncé
                littéral publié par l'analyse amont.
        """
        self.min_criteres = min_criteres
        self.seuil_positif = seuil_positif
        self.seuil_negatif = seuil_negatif
        self.max_non_evaluables = max_non_evaluables
        self.lus_dans_enonce = lus_dans_enonce


def lire_parametres_regle(enonce: str) -> ParametresRegle:
    """Relit les seuils de la règle dans son énoncé littéral.

    L'analyse amont ne publie pas de bloc structuré : ses seuils ne sont
    disponibles que dans une phrase. Ils sont donc relus par expression
    régulière, et à défaut repris de constantes locales — ce qui est alors
    signalé en hypothèse.

    Args:
        enonce: Énoncé littéral de la règle appliquée.

    Returns:
        Les seuils effectifs et leur provenance.
    """
    texte = enonce or ""
    motifs = {
        "min_criteres": r"moins de\s+(\d+)\s+crit",
        "seuil_positif": r"score_total\s*≥\s*(\d+)",
        "max_non_evaluables": r"au plus\s+(\d+)\s+crit",
        "seuil_negatif": r"score_total\s*≤\s*(\d+)",
    }
    valeurs: dict[str, int] = {}
    for cle, motif in motifs.items():
        trouve = re.search(motif, texte)
        if trouve:
            valeurs[cle] = int(trouve.group(1))
    if len(valeurs) == len(motifs):
        return ParametresRegle(
            valeurs["min_criteres"],
            valeurs["seuil_positif"],
            valeurs["seuil_negatif"],
            valeurs["max_non_evaluables"],
            True,
        )
    logger.warning("seuils de la règle non relus dans l'énoncé — constantes locales")
    return ParametresRegle(
        MIN_CRITERES_EVALUES_F5,
        SEUIL_POSITIF_F5,
        SEUIL_NEGATIF_F5,
        MAX_NON_EVALUABLES_POSITIF_F5,
        False,
    )


def appliquer_regle(scores: dict[str, int | None], parametres: ParametresRegle) -> str:
    """Rejoue la règle de verdict de l'analyse amont.

    Fonction pure : à grille identique, verdict identique.

    Args:
        scores: Score de chaque critère, `None` pour non évaluable.
        parametres: Seuils effectifs.

    Returns:
        Le verdict obtenu.
    """
    evaluees = {cle: valeur for cle, valeur in scores.items() if valeur is not None}
    nb_non_evaluables = len(scores) - len(evaluees)
    total = sum(evaluees.values())

    if len(evaluees) < parametres.min_criteres:
        return VERDICT_INDETERMINE
    if (
        total >= parametres.seuil_positif
        and all(valeur > 0 for valeur in evaluees.values())
        and nb_non_evaluables <= parametres.max_non_evaluables
    ):
        return VERDICT_POSITIF
    if total <= parametres.seuil_negatif or (
        evaluees.get(CRITERE_DEMANDE_F5) == 0
        and evaluees.get(CRITERE_DIFFERENCIATION_F5) == 0
    ):
        return VERDICT_NEGATIF
    return VERDICT_INDETERMINE


def simuler_bascules(
    grille: list, parametres: ParametresRegle
) -> tuple[str, list[Bascule]]:
    """Simule toutes les mutations mono-critère et retient celles qui basculent.

    Chaque critère est successivement porté à chacun des scores possibles, les
    critères non évaluables étant rendus évaluables. Seules les mutations qui
    **changent réellement le verdict** sont retenues ; pour un même critère,
    seule la mutation la moins exigeante est publiée.

    Args:
        grille: Grille notée publiée par l'analyse amont.
        parametres: Seuils effectifs de la règle.

    Returns:
        Le couple `(verdict_simulé_sur_la_grille_actuelle, bascules)`.
    """
    scores: dict[str, int | None] = {
        note.critere: (None if note.non_evaluable else note.score) for note in grille
    }
    verdict_actuel = appliquer_regle(scores, parametres)
    retenues: dict[str, Bascule] = {}

    for critere, score_actuel in scores.items():
        for mutation in SCORES_MUTATION:
            if score_actuel == mutation:
                continue
            candidats = dict(scores)
            candidats[critere] = mutation
            verdict = appliquer_regle(candidats, parametres)
            if verdict == verdict_actuel or critere in retenues:
                continue
            lisible = CRITERES_LISIBLES.get(critere, critere)
            etat = (
                "non évaluable" if score_actuel is None else f"noté {score_actuel}"
            )
            retenues[critere] = Bascule(
                critere=critere,
                score_actuel=etat,
                score_requis=mutation,
                verdict_obtenu=verdict,
                enonce=(
                    f"Porter le critère « {lisible} » ({etat}) à {mutation} sur "
                    f"{SCORE_MAX_CRITERE} suffirait à faire passer le verdict de "
                    f"« {VERDICT_LISIBLE.get(verdict_actuel, verdict_actuel)} » à "
                    f"« {VERDICT_LISIBLE.get(verdict, verdict)} ». Aucune autre "
                    f"amélioration d'un seul critère n'y parvient."
                ),
            )
    return verdict_actuel, list(retenues.values())


def _nettoyer_bascules_texte(
    entrees: list[str], bascules: list[Bascule]
) -> tuple[list[str], int]:
    """Retire des textes libres amont les conclusions de bascule non simulées.

    Args:
        entrees: Textes libres de l'analyse amont.
        bascules: Bascules confirmées par la simulation.

    Returns:
        Le couple `(textes_nettoyés, nb_phrases_retirées)`.
    """
    confirmes = {bascule.critere for bascule in bascules}
    nettoyes: list[str] = []
    retirees = 0
    for texte in entrees:
        phrases = re.split(r"(?<=[.!?])\s+", texte)
        gardees: list[str] = []
        for phrase in phrases:
            minuscule = _sans_accent(phrase.lower())
            if not any(_sans_accent(mot) in minuscule for mot in MOTS_BASCULE):
                gardees.append(phrase)
                continue
            criteres_cites = {
                critere
                for critere, motifs in MOTIFS_CRITERES.items()
                if any(_sans_accent(motif) in minuscule for motif in motifs)
            }
            if criteres_cites and criteres_cites <= confirmes:
                gardees.append(phrase)
            else:
                retirees += 1
        propre = " ".join(p for p in gardees if p.strip()).strip()
        if propre:
            nettoyes.append(propre)
    return nettoyes, retirees


def _sans_accent(texte: str) -> str:
    """Retire les accents d'un texte pour une comparaison tolérante.

    Args:
        texte: Texte source.

    Returns:
        Le texte sans signes diacritiques.
    """
    decompose = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in decompose if not unicodedata.combining(c))


# =========================================================================== #
# Badges, verbatims et limites
# =========================================================================== #


def depouiller(texte: str) -> tuple[str, int]:
    """Retire d'un texte amont ce qui n'a pas sa place devant un décideur.

    Trois traitements, tous déterministes et sans perte de sens : suppression
    des références techniques entre parenthèses, suppression du préfixe de
    l'agent producteur, et substitution des généralisations à une population
    par ce que le corpus autorise réellement à dire.

    Ce traitement ne s'applique **jamais aux limites**, restituées verbatim.

    Args:
        texte: Texte amont.

    Returns:
        Le couple `(texte_dépouillé, nb_substitutions)`.
    """
    if not texte:
        return texte, 0
    propre = re.sub(MOTIF_REFERENCE_TECHNIQUE, "", texte)
    propre = re.sub(MOTIF_REFERENCE_INTERNE, "", propre)
    propre = re.sub(MOTIF_PREFIXE_AGENT, "", propre)
    for motif, remplacement in MOTIFS_NETTOYAGE:
        propre = re.sub(motif, remplacement, propre)
    substitutions = 0
    for motif, remplacement in SUBSTITUTIONS_TEXTE:
        propre, nombre = re.subn(motif, remplacement, propre)
        substitutions += nombre
    return propre, substitutions


CHAMPS_A_DEPOUILLER: tuple[str, ...] = (
    "tableau_grille",
    "tableau_demande",
    "tableau_besoins",
    "tableau_attentes",
    "tableau_sentiment",
    "tableau_intensite",
    "tableau_concurrents",
    "tableau_benchmark",
    "tableau_opportunites",
    "tableau_risques",
    "tableau_signaux_plc",
    "recommandations_phase",
    "recommandation_prix",
    "encart_plc",
    "risque_principal",
    "faits_cles",
    "recommandations_majeures",
    "donnees_a_completer",
    "divergences",
    "normes_marche",
    "angles_peu_exploites",
    "portee_regionale",
    "tableaux_recommandations",
    "pain_points",
)
"""Champs injectables soumis au dépouillement.

En sont exclus : l'énoncé littéral de la règle (auditable, donc intouchable),
les limites (verbatim), les verbatims (langue d'origine) et tout ce que le code
a lui-même rédigé.
"""


def depouiller_injectables(injectables: Injectables) -> int:
    """Applique le dépouillement à tous les champs injectables concernés.

    Args:
        injectables: Données injectables, modifiées sur place.

    Returns:
        Le nombre total de substitutions appliquées.
    """
    total = 0
    for champ in CHAMPS_A_DEPOUILLER:
        valeur = getattr(injectables, champ)
        if isinstance(valeur, str):
            propre, nombre = depouiller(valeur)
            setattr(injectables, champ, propre)
            total += nombre
        elif isinstance(valeur, list):
            nettoyes = []
            for element in valeur:
                if isinstance(element, str):
                    propre, nombre = depouiller(element)
                    total += nombre
                    nettoyes.append(propre)
                elif isinstance(element, dict):
                    propre_dict = {}
                    for cle, sous_valeur in element.items():
                        if isinstance(sous_valeur, str):
                            propre, nombre = depouiller(sous_valeur)
                            total += nombre
                            propre_dict[cle] = propre
                        else:
                            propre_dict[cle] = sous_valeur
                    nettoyes.append(propre_dict)
                else:
                    nettoyes.append(element)
            setattr(injectables, champ, nettoyes)
        elif isinstance(valeur, dict):
            propre_dict = {}
            for cle, sous_valeur in valeur.items():
                if isinstance(sous_valeur, str):
                    propre, nombre = depouiller(sous_valeur)
                    total += nombre
                    propre_dict[cle] = propre
                else:
                    propre_dict[cle] = sous_valeur
            setattr(injectables, champ, propre_dict)
    return total


def badge(niveau: str | None, justification: str) -> str:
    """Rend un badge de confiance en blockquote.

    Args:
        niveau: Niveau de confiance hérité.
        justification: Justification amont, tronquée.

    Returns:
        Le badge Markdown.
    """
    libelle = BADGES_CONFIANCE.get((niveau or "").strip().lower(), BADGE_INCONNU)
    detail = tronquer(justification, MAX_CARACTERES_BADGE)
    return f"> **{libelle}.**" + (f" {detail}" if detail else "")


def selectionner_verbatim(verbatims: list) -> Verbatim | None:
    """Sélectionne un extrait par des critères de code, jamais par un modèle.

    L'extrait retenu est le plus court qui tienne sous la limite ; à défaut, le
    plus court est tronqué proprement. La langue d'origine est conservée : un
    extrait traduit ne serait plus un verbatim.

    Args:
        verbatims: Extraits attachés à une difficulté rapportée.

    Returns:
        L'extrait retenu, ou `None` si aucun n'est exploitable.
    """
    candidats = [
        v for v in verbatims if (v.extrait or "").strip()
    ]
    if not candidats:
        return None
    propres = [
        (" ".join(v.extrait.split()), v) for v in candidats
    ]
    sous_limite = [c for c in propres if len(c[0]) <= MAX_VERBATIM_CARACTERES]
    texte, source = min(sous_limite or propres, key=lambda c: len(c[0]))
    tronque = len(texte) > MAX_VERBATIM_CARACTERES
    if tronque:
        texte = texte[:MAX_VERBATIM_CARACTERES].rsplit(" ", 1)[0] + "…"
    return Verbatim(
        texte=texte,
        id_unite=source.id_unite,
        source=source.source,
        tronque=tronque,
    )


def consolider_limites(limites: list[str]) -> list[tuple[str, list[str]]]:
    """Déduplique les limites et les regroupe par famille, sans les réécrire.

    Args:
        limites: Limites héritées, éventuellement préfixées de leur source.

    Returns:
        La liste des couples `(libellé de famille, limites)`, familles vides
        exclues.
    """
    vues: set[str] = set()
    par_famille: dict[str, list[str]] = {}
    for brute in limites:
        texte = re.sub(r"^(?:\[[^\]]+\]\s*)+", "", brute or "").strip()
        if not texte:
            continue
        cle = _sans_accent(texte.lower())
        if cle in vues:
            continue
        vues.add(cle)
        famille = FAMILLE_AUTRES
        for identifiant, _, motifs in LIMITES_FAMILLES:
            if any(_sans_accent(motif) in cle for motif in motifs):
                famille = identifiant
                break
        par_famille.setdefault(famille, []).append(texte)

    ordonnees: list[tuple[str, list[str]]] = []
    for identifiant, libelle, _ in LIMITES_FAMILLES:
        if par_famille.get(identifiant):
            ordonnees.append((libelle, par_famille[identifiant][:MAX_LIMITES_PAR_FAMILLE]))
    if par_famille.get(FAMILLE_AUTRES):
        ordonnees.append(
            (LIBELLE_FAMILLE_AUTRES, par_famille[FAMILLE_AUTRES][:MAX_LIMITES_PAR_FAMILLE])
        )
    return ordonnees


# =========================================================================== #
# Tableaux par section
# =========================================================================== #


def _tableau_grille(grille: list) -> str:
    """Construit le tableau de la grille de potentiel.

    Args:
        grille: Grille notée publiée par l'analyse amont.

    Returns:
        Le tableau Markdown.
    """
    lignes = []
    for note in grille:
        score = (
            "non évaluable"
            if note.non_evaluable or note.score is None
            else f"{note.score}/{SCORE_MAX_CRITERE}"
        )
        if note.plafonnement_applique:
            score += f" (plafonné : {note.plafonnement_applique})"
        lignes.append(
            [
                CRITERES_LISIBLES.get(note.critere, note.critere),
                score,
                tronquer(note.justification, MAX_CARACTERES_JUSTIFICATION),
            ]
        )
    return tableau(["Critère", "Score", "Lecture"], lignes)


def _tableau_indicateurs(
    elements: list, entetes: tuple[str, str, str] = ("Indicateur", "Valeur", "Comment le lire")
) -> str:
    """Construit un tableau d'indicateurs depuis des éléments de dossier.

    Args:
        elements: Éléments porteurs de `libelle`, `valeur` et `detail`.
        entetes: Libellés des trois colonnes, adaptés au contenu restitué.

    Returns:
        Le tableau Markdown.
    """
    lignes = [
        [
            element.libelle,
            _valeur_lisible(element.ref, element.valeur),
            tronquer(element.detail, MAX_CARACTERES_JUSTIFICATION),
        ]
        for element in elements
    ]
    return tableau(list(entetes), lignes)


def _tableau_sentiment(insights) -> str:
    """Construit le tableau de répartition du sentiment par source.

    Args:
        insights: Sortie F3 validée.

    Returns:
        Le tableau Markdown.
    """
    if insights is None or insights.sentiment is None:
        return ""
    lignes = []
    if insights.sentiment.global_ is not None:
        bloc = insights.sentiment.global_
        lignes.append(
            [
                "Ensemble du corpus",
                str(bloc.positif),
                str(bloc.negatif),
                str(bloc.neutre),
                str(bloc.mixte),
                str(bloc.base_nb),
            ]
        )
    for source, bloc in sorted(insights.sentiment.par_source.items()):
        lignes.append(
            [
                source,
                str(bloc.positif),
                str(bloc.negatif),
                str(bloc.neutre),
                str(bloc.mixte),
                str(bloc.base_nb),
            ]
        )
    return tableau(
        [
            "Source",
            "Positifs",
            "Négatifs",
            "Neutres",
            "Mixtes",
            "Contributions analysées",
        ],
        lignes,
    )


def _tableau_concurrents(concurrence) -> str:
    """Construit le comparatif des principaux concurrents.

    Args:
        concurrence: Sortie F4 validée.

    Returns:
        Le tableau Markdown.
    """
    if concurrence is None or not concurrence.tableau_comparatif:
        return ""
    lignes = []
    for ligne in concurrence.tableau_comparatif[:NB_CONCURRENTS_TABLEAU]:
        prix = " ; ".join(
            f"{franciser(fourchette)} {devise}"
            for devise, fourchette in sorted(ligne.fourchette_prix_par_devise.items())
        )
        lignes.append(
            [
                tronquer(ligne.concurrent, 40),
                ", ".join(ligne.presence_sources),
                prix,
                formater_nombre(ligne.note_moyenne, 1) if ligne.note_moyenne else "—",
                formater_nombre(float(ligne.volume_ventes_cumule), 0)
                if ligne.volume_ventes_cumule
                else "—",
                tronquer(ligne.force_principale, 70),
                tronquer(ligne.faiblesse_principale, 70),
            ]
        )
    return tableau(
        [
            "Concurrent",
            "Observé sur",
            "Fourchette de prix",
            "Note",
            "Ventes cumulées",
            "Force",
            "Faiblesse",
        ],
        lignes,
    )


def _tableau_benchmark(concurrence) -> str:
    """Construit le benchmark de prix par source et par devise.

    Aucune conversion n'est effectuée : deux devises font deux lignes, jamais
    une moyenne.

    Args:
        concurrence: Sortie F4 validée.

    Returns:
        Le tableau Markdown.
    """
    if concurrence is None or not concurrence.benchmark_prix:
        return ""
    lignes = []
    for bloc in concurrence.benchmark_prix:
        coeur = next((s for s in bloc.segments if s.nom == "coeur"), None)
        lignes.append(
            [
                bloc.source,
                bloc.devise,
                str(bloc.nb_offres_avec_prix),
                formater_montant(bloc.prix_min, bloc.devise),
                formater_montant(bloc.prix_mediane, bloc.devise),
                formater_montant(bloc.prix_max, bloc.devise),
                (
                    f"{formater_nombre(coeur.borne_basse, DECIMALES_MONTANT)}–"
                    f"{formater_montant(coeur.borne_haute, bloc.devise)}"
                    if coeur
                    else "—"
                ),
            ]
        )
    return tableau(
        ["Source", "Devise", "Offres", "Minimum", "Médiane", "Maximum", "Cœur de marché"],
        lignes,
    )


def _tableau_recommandations(recommandations: list) -> str:
    """Construit le tableau d'un groupe de recommandations.

    Args:
        recommandations: Recommandations d'une même priorité.

    Returns:
        Le tableau Markdown.
    """
    lignes = [
        [
            recommandation.enonce,
            recommandation.domaine,
            recommandation.horizon.replace("_", " "),
            recommandation.effort_estime,
            " ; ".join(recommandation.indicateurs_suivi[:3]),
        ]
        for recommandation in recommandations
    ]
    return tableau(
        ["Recommandation", "Domaine", "Horizon", "Effort", "Indicateurs de suivi"],
        lignes,
    )


def _tableau_signaux_plc(plc) -> str:
    """Construit le tableau des signaux de cycle de vie.

    Args:
        plc: Sortie F6 validée.

    Returns:
        Le tableau Markdown.
    """
    if plc is None or not plc.signaux:
        return ""
    lignes = []
    for signal in plc.signaux:
        lignes.append(
            [
                signal.famille.replace("_", " "),
                "non évaluable"
                if signal.non_evaluable
                else PHASE_LISIBLE.get(
                    signal.orientation_phase or "", signal.orientation_phase or "—"
                ),
                signal.force or "—",
                tronquer(signal.justification, MAX_CARACTERES_JUSTIFICATION),
            ]
        )
    return tableau(["Famille de signaux", "Oriente vers", "Force", "Lecture"], lignes)


# =========================================================================== #
# Préparation complète
# =========================================================================== #


def preparer(
    entrees: EntreesChargees,
    bruts: list[dict],
    degradees: list[str],
    absentes: list[str],
) -> tuple[Injectables, ListeBlanche, list[StatutAnalyse], list[str]]:
    """Construit toutes les données injectables du rapport.

    Args:
        entrees: Fichiers d'entrée validés.
        bruts: Dictionnaires JSON d'origine, matière de la liste blanche.
        degradees: Sections construites depuis l'écho de synthèse.
        absentes: Sections remplacées par un encart standard.

    Returns:
        Le quadruplet `(injectables, liste_blanche, statuts, hypotheses)`.

    Raises:
        ValueError: Si la sortie de l'analyse de synthèse est absente.
    """
    recommandations = entrees.recommandations
    if recommandations is None:
        raise ValueError("la préparation exige la sortie de l'analyse de synthèse")

    statuts: list[StatutAnalyse] = []
    hypotheses: list[str] = []
    liste = ListeBlanche()
    for brut in bruts:
        liste.ajouter_json(brut)
    logger.debug("liste blanche : %d valeur(s) admises", len(liste))

    injectables = Injectables()
    injectables.sections_degradees = list(degradees)
    injectables.sections_absentes = list(absentes)

    dossier = recommandations.dossier_synthese
    verdict = recommandations.verdict_potentiel

    # --- En-tête ------------------------------------------------------------ #
    produit = entrees.produit
    marche = entrees.marche
    portee = [LIBELLES_ENTREES[nom] for nom in entrees.blocs_disponibles if nom in LIBELLES_ENTREES]
    injectables.entete = {
        "produit": produit.nom if produit else "produit inconnu",
        "description": produit.description if produit else "",
        "categorie": (produit.categorie if produit else None) or "non renseignée",
        "marche": marche.geo if marche else "??",
        "langue": marche.langue if marche else "",
        "date_run": formater_date(recommandations.horodatage_utc),
        "portee": ", ".join(portee) if portee else "analyse de synthèse seule",
    }

    # --- Verdict et bascules ------------------------------------------------ #
    parametres = lire_parametres_regle(verdict.regle_appliquee)
    verdict_simule, bascules = simuler_bascules(verdict.grille, parametres)
    injectables.verdict_brut = verdict.verdict
    injectables.verdict_lisible = VERDICT_LISIBLE.get(verdict.verdict, verdict.verdict)
    injectables.confiance_verdict = verdict.confiance
    injectables.tableau_grille = _tableau_grille(verdict.grille)
    injectables.regle_litterale = verdict.regle_appliquee
    injectables.bascules = bascules

    if not parametres.lus_dans_enonce:
        hypotheses.append(BESOIN_PARAMETRES_REGLE)
        statuts.append(
            StatutAnalyse(
                phase=PHASE_PREPARATION,
                succes=True,
                message_erreur=(
                    "seuils de la règle de verdict non relisibles dans l'énoncé "
                    "littéral : constantes locales appliquées et signalées en "
                    "hypothèse."
                ),
            )
        )
    if verdict_simule != verdict.verdict:
        statuts.append(
            StatutAnalyse(
                phase=PHASE_PREPARATION,
                succes=False,
                message_erreur=(
                    f"la simulation de la règle sur la grille publiée donne "
                    f"« {verdict_simule} » alors que l'analyse amont publie "
                    f"« {verdict.verdict} ». Le verdict publié est restitué tel quel ; "
                    f"l'écart est signalé."
                ),
            )
        )

    donnees_propres, retirees = _nettoyer_bascules_texte(
        list(recommandations.donnees_a_completer)[:MAX_DONNEES_A_COMPLETER], bascules
    )
    injectables.donnees_a_completer = donnees_propres
    if retirees:
        statuts.append(
            StatutAnalyse(
                phase=PHASE_PREPARATION,
                succes=True,
                message_erreur=(
                    f"{retirees} conclusion(s) de bascule du texte libre amont "
                    f"retirée(s) : incompatibles avec la simulation de la règle. "
                    f"Seules les bascules simulées sont publiées."
                ),
                nb_elements=retirees,
            )
        )

    # --- Synthèse ----------------------------------------------------------- #
    # Les faits clés sont restitués tels que l'analyse amont les énonce : leurs
    # chiffres y figurent déjà, et les valeurs exactes sont publiées dans les
    # tableaux des sections concernées. Y accoler la valeur brute de la référence
    # produirait des doublons trompeurs (« … top 3 à 42,6 % (75) »).
    injectables.faits_cles = [
        fait.enonce for fait in recommandations.faits_cles[:NB_FAITS_CLES_SYNTHESE]
    ]
    majeures = [
        r
        for r in (
            list(recommandations.recommandations_produit)
            + ([recommandations.recommandation_positionnement]
               if recommandations.recommandation_positionnement
               else [])
            + list(recommandations.recommandations_marketing)
        )
        if r is not None
    ]
    majeures.sort(key=lambda r: (r.priorite or "P3"))
    injectables.recommandations_majeures = [
        r.enonce for r in majeures[:NB_RECOMMANDATIONS_SYNTHESE]
    ]
    risques_tries = sorted(
        recommandations.risques,
        key=lambda r: {"elevee": 0, "moyenne": 1, "faible": 2}.get(r.gravite, 3),
    )
    injectables.risque_principal = (
        f"{risques_tries[0].libelle} — {tronquer(risques_tries[0].attenuation, 200)}"
        if risques_tries
        else ""
    )

    # --- Cycle de vie ------------------------------------------------------- #
    plc = entrees.plc
    if plc is None:
        injectables.encart_plc = ENCART_PLC_ABSENTE
    elif plc.classification is None or plc.classification.phase_probable is None:
        motif = plc.declenchement.motif or (
            "aucune phase n'a pu être retenue à partir des signaux disponibles."
        )
        injectables.encart_plc = ENCART_PLC_NON_DECLENCHEE.format(motif=motif)
    else:
        classification = plc.classification
        injectables.phase_brute = classification.phase_probable
        injectables.phase_lisible = PHASE_LISIBLE.get(
            classification.phase_probable, classification.phase_probable
        )
        injectables.incertitude_phase = classification.incertitude
        injectables.tableau_signaux_plc = _tableau_signaux_plc(plc)
        injectables.recommandations_phase = _tableau_recommandations(
            plc.recommandations_phase
        )

    # --- Demande ------------------------------------------------------------ #
    if dossier is not None and dossier.demande is not None:
        injectables.tableau_demande = _tableau_indicateurs(dossier.demande.indicateurs)

    # --- Consommateurs ------------------------------------------------------ #
    insights = entrees.insights
    if insights is not None:
        injectables.tableau_besoins = tableau(
            ["Besoin exprimé", "Nature", "Ce que dit le corpus"],
            [
                [b.libelle, b.type, tronquer(b.description, MAX_CARACTERES_JUSTIFICATION)]
                for b in insights.besoins[:MAX_NORMES_RAPPORT]
            ],
        )
        injectables.tableau_attentes = tableau(
            ["Attente", "Niveau d'exigence", "Ce que dit le corpus"],
            [
                [
                    a.libelle,
                    a.niveau_exigence,
                    tronquer(a.description, MAX_CARACTERES_JUSTIFICATION),
                ]
                for a in insights.attentes[:MAX_NORMES_RAPPORT]
            ],
        )
        for rang, point in enumerate(insights.pain_points[:NB_PAIN_POINTS_RAPPORT]):
            cle = f"irritant-{rang + 1}"
            injectables.pain_points.append(
                {
                    "cle": cle,
                    "libelle": point.libelle,
                    "frequence": formater_pourcentage(point.frequence_pct),
                    "intensite": formater_nombre(point.intensite_moyenne, 2),
                    "description": tronquer(point.description, 260),
                }
            )
            extrait = selectionner_verbatim(point.verbatims)
            if extrait is not None:
                injectables.verbatims[cle] = extrait
        injectables.tableau_sentiment = _tableau_sentiment(insights)
        injectables.divergences = list(insights.divergences_sources)
    elif dossier is not None and dossier.consommateur is not None:
        echo = dossier.consommateur
        injectables.tableau_besoins = _tableau_indicateurs(
            echo.besoins, ("Besoin exprimé", "Nature", "Ce que dit le corpus")
        )
        injectables.tableau_attentes = _tableau_indicateurs(
            echo.attentes, ("Attente", "Niveau d'exigence", "Ce que dit le corpus")
        )
        for rang, element in enumerate(echo.pain_points[:NB_PAIN_POINTS_RAPPORT]):
            injectables.pain_points.append(
                {
                    "cle": f"irritant-{rang + 1}",
                    "libelle": element.libelle,
                    "frequence": element.valeur,
                    "intensite": "",
                    "description": tronquer(element.detail, 260),
                }
            )
        if echo.sentiment is not None:
            injectables.tableau_sentiment = tableau(
                ["Indicateur", "Valeur"],
                [[echo.sentiment.libelle, echo.sentiment.valeur]],
            )
        injectables.divergences = list(echo.divergences_sources)

    # --- Concurrence -------------------------------------------------------- #
    concurrence = entrees.concurrence
    if concurrence is not None:
        intensite = concurrence.intensite_concurrentielle
        if intensite is not None:
            injectables.tableau_intensite = tableau(
                ["Indicateur", "Valeur"],
                [
                    ["Concurrents identifiés", str(intensite.nb_concurrents_identifies)],
                    ["Offres au cœur du benchmark", str(intensite.nb_offres_coeur)],
                    ["Annonceurs actifs", str(intensite.nb_annonceurs)],
                    ["Annonces actives", str(intensite.nb_annonces_actives)],
                    [
                        "Longévité publicitaire médiane (jours)",
                        formater_nombre(intensite.duree_diffusion_mediane_jours, 0)
                        if intensite.duree_diffusion_mediane_jours
                        else "—",
                    ],
                    [
                        "Concentration des volumes du top 3",
                        formater_pourcentage(intensite.concentration_volumes_top3_pct)
                        if intensite.concentration_volumes_top3_pct
                        else "—",
                    ],
                ],
            )
        injectables.tableau_concurrents = _tableau_concurrents(concurrence)
        injectables.tableau_benchmark = _tableau_benchmark(concurrence)
        injectables.portee_regionale = [
            f"{v.source} ({v.portee}) : {v.commentaire}"
            for v in concurrence.validite_regionale
        ]
        if concurrence.positionnement is not None:
            injectables.normes_marche = [
                p.point for p in concurrence.positionnement.normes_marche[:MAX_NORMES_RAPPORT]
            ]
            injectables.angles_peu_exploites = [
                p.point
                for p in concurrence.positionnement.angles_peu_exploites[:MAX_ANGLES_RAPPORT]
            ]
    elif dossier is not None and dossier.concurrence is not None:
        echo = dossier.concurrence
        injectables.tableau_intensite = _tableau_indicateurs(
            echo.intensite, ("Indicateur", "Valeur", "Comment le lire")
        )
        injectables.tableau_benchmark = _tableau_indicateurs(
            echo.benchmark, ("Repère de prix", "Valeur", "Base de calcul")
        )
        injectables.portee_regionale = list(echo.validite_regionale)
        injectables.angles_peu_exploites = [
            e.libelle for e in echo.angles_peu_exploites[:MAX_ANGLES_RAPPORT]
        ]
        injectables.normes_marche = [
            e.libelle for e in echo.facteurs_cles_succes[:MAX_NORMES_RAPPORT]
        ]

    # --- Recommandations, opportunités et risques --------------------------- #
    par_priorite: dict[str, list] = {}
    for recommandation in majeures:
        par_priorite.setdefault(recommandation.priorite or "P3", []).append(
            recommandation
        )
    injectables.tableaux_recommandations = {
        priorite: _tableau_recommandations(par_priorite[priorite])
        for priorite in sorted(par_priorite)
    }
    if recommandations.recommandation_prix is not None:
        prix = recommandations.recommandation_prix
        fourchettes = " ; ".join(
            f"{formater_nombre(f.min, DECIMALES_MONTANT)}–{formater_montant(f.max, f.devise)}"
            for f in prix.fourchettes
        )
        injectables.recommandation_prix = "\n\n".join(
            partie
            for partie in (
                prix.strategie,
                f"**Fourchette proposée : {fourchettes}.**" if fourchettes else "",
                "\n".join(f"- {condition}" for condition in prix.conditions),
            )
            if partie
        )
    injectables.tableau_opportunites = tableau(
        ["Opportunité", "Ce que dit le corpus", "Conditions pour la saisir"],
        [
            [
                o.libelle,
                tronquer(o.description, 200),
                " ; ".join(o.conditions_de_capture[:2]),
            ]
            for o in recommandations.opportunites[:MAX_OPPORTUNITES_RAPPORT]
        ],
    )
    injectables.tableau_risques = tableau(
        ["Risque", "Gravité", "Atténuation proposée"],
        [
            [r.libelle, r.gravite, tronquer(r.attenuation, 200)]
            for r in recommandations.risques[:MAX_RISQUES_RAPPORT]
        ],
    )

    # --- Annexe ------------------------------------------------------------- #
    injectables.annexe_sources = _annexe_sources(entrees)
    injectables.annexe_periode = _annexe_periode(entrees)
    injectables.limites_par_famille = consolider_limites(entrees.limites_amont)
    injectables.hypotheses = list(entrees.hypotheses_amont)

    # --- Badges, mentions et refs ------------------------------------------- #
    injectables.badges = _badges(entrees)
    injectables.mentions_partielles = _mentions(degradees, absentes)
    injectables.refs_par_section = _refs(entrees, injectables)

    # --- Dépouillement du vocabulaire interne ------------------------------- #
    substitutions = depouiller_injectables(injectables)
    if substitutions:
        statuts.append(
            StatutAnalyse(
                phase=PHASE_PREPARATION,
                succes=True,
                message_erreur=(
                    f"{substitutions} substitution(s) déterministe(s) appliquée(s) aux "
                    f"textes amont injectés dans le corps du rapport : références "
                    f"techniques et préfixes d'agent retirés, généralisations à une "
                    f"population ramenées à ce que le corpus autorise. Les limites, "
                    f"elles, restent verbatim."
                ),
                nb_elements=substitutions,
            )
        )

    return injectables, liste, statuts, hypotheses


def _annexe_sources(entrees: EntreesChargees) -> str:
    """Construit le tableau des sources et volumes exploités.

    Args:
        entrees: Fichiers d'entrée validés.

    Returns:
        Le tableau Markdown.
    """
    lignes: list[list[str]] = []
    insights = entrees.insights
    if insights is not None and insights.stats_corpus is not None:
        for source, nombre in sorted(insights.stats_corpus.nb_unites_par_source.items()):
            lignes.append([source, "avis et discussions", str(nombre), "contributions"])
        lignes.append(
            [
                "recherche web",
                "pages éditoriales",
                str(insights.stats_corpus.nb_documents_analyses),
                "documents",
            ]
        )
    concurrence = entrees.concurrence
    if concurrence is not None and concurrence.referentiel_stats is not None:
        stats = concurrence.referentiel_stats
        for source, nombre in sorted(stats.nb_offres_par_source.items()):
            lignes.append([source, "offres marchandes", str(nombre), "offres"])
        lignes.append(["publicité", "annonces", str(stats.nb_annonces), "annonces"])
        lignes.append(["avis indexés", "avis clients", str(stats.nb_avis_indexes), "avis"])
    return tableau(["Source", "Nature", "Volume", "Unité"], lignes)


def _annexe_periode(entrees: EntreesChargees) -> str:
    """Décrit la période couverte par les corpus.

    Args:
        entrees: Fichiers d'entrée validés.

    Returns:
        Une phrase, ou une mention d'indisponibilité.
    """
    insights = entrees.insights
    if (
        insights is None
        or insights.stats_corpus is None
        or insights.stats_corpus.periode_couverte is None
    ):
        return (
            "La période couverte par les contenus collectés n'est pas disponible "
            "dans les analyses fournies."
        )
    periode = insights.stats_corpus.periode_couverte
    return (
        f"Les contenus collectés s'échelonnent du {formater_date(periode.min)} au "
        f"{formater_date(periode.max)}. L'étude elle-même a été produite le "
        f"{formater_date(entrees.recommandations.horodatage_utc if entrees.recommandations else None)}."
    )


def _badges(entrees: EntreesChargees) -> dict[str, str]:
    """Associe à chaque section le badge de confiance de son entrée nourricière.

    Args:
        entrees: Fichiers d'entrée validés.

    Returns:
        Le dictionnaire `section → badge Markdown`.
    """
    badges: dict[str, str] = {}
    recommandations = entrees.recommandations
    if recommandations is not None and recommandations.confiance_globale is not None:
        rendu = badge(
            recommandations.confiance_globale.niveau,
            recommandations.confiance_globale.justification,
        )
        badges[SECTION_SYNTHESE] = rendu
        badges[SECTION_VERDICT] = rendu
        badges[SECTION_DEMANDE] = rendu
    source_consommateurs = entrees.insights
    if source_consommateurs is not None and source_consommateurs.confiance_globale:
        badges[SECTION_CONSOMMATEURS] = badge(
            source_consommateurs.confiance_globale.niveau,
            source_consommateurs.confiance_globale.justification,
        )
    elif recommandations is not None and recommandations.dossier_synthese is not None:
        echo = recommandations.dossier_synthese.consommateur
        if echo is not None:
            badges[SECTION_CONSOMMATEURS] = badge(
                echo.confiance_f3,
                "Niveau hérité du rappel de l'analyse de synthèse, l'analyse "
                "détaillée n'étant pas disponible.",
            )
    if entrees.concurrence is not None and entrees.concurrence.confiance_globale:
        badges[SECTION_CONCURRENCE] = badge(
            entrees.concurrence.confiance_globale.niveau,
            entrees.concurrence.confiance_globale.justification,
        )
    elif recommandations is not None and recommandations.dossier_synthese is not None:
        echo = recommandations.dossier_synthese.concurrence
        if echo is not None:
            badges[SECTION_CONCURRENCE] = badge(
                echo.confiance_f4,
                "Niveau hérité du rappel de l'analyse de synthèse, l'analyse "
                "détaillée n'étant pas disponible.",
            )
    if entrees.plc is not None and entrees.plc.classification is not None:
        badges[SECTION_PLC] = badge(
            entrees.plc.classification.confiance,
            "Classification de phase fondée sur une grille de lecture en hypothèse "
            "de travail, non validée.",
        )
    return badges


def _mentions(degradees: list[str], absentes: list[str]) -> dict[str, str]:
    """Rédige les mentions d'étude partielle exigées par F7.3.

    Args:
        degradees: Sections construites depuis l'écho de synthèse.
        absentes: Sections remplacées par un encart standard.

    Returns:
        Le dictionnaire `section → mention Markdown`.
    """
    correspondance = {
        SECTION_CONSOMMATEURS: ENTREE_INSIGHTS,
        SECTION_CONCURRENCE: ENTREE_CONCURRENCE,
        SECTION_PLC: ENTREE_PLC,
    }
    mentions: dict[str, str] = {}
    for section in list(degradees) + list(absentes):
        entree = correspondance.get(section, ENTREE_RECOMMANDATIONS)
        if entree == ENTREE_PLC:
            continue
        mentions[section] = MENTION_ETUDE_PARTIELLE.format(
            libelle=LIBELLES_ENTREES.get(entree, entree),
            detail=DETAILS_ENTREES.get(entree, "le détail de cette analyse"),
        )
    return mentions


def _refs(entrees: EntreesChargees, injectables: Injectables) -> dict[str, list[str]]:
    """Associe à chaque section les références de ses sources.

    Args:
        entrees: Fichiers d'entrée validés.
        injectables: Données injectables déjà construites.

    Returns:
        Le dictionnaire `section → refs`, destiné aux commentaires HTML.
    """
    refs: dict[str, list[str]] = {
        SECTION_SYNTHESE: [
            f"{ENTREE_RECOMMANDATIONS}.synthese_executive",
            f"{ENTREE_RECOMMANDATIONS}.faits_cles",
            f"{ENTREE_RECOMMANDATIONS}.risques",
        ],
        SECTION_VERDICT: [
            f"{ENTREE_RECOMMANDATIONS}.verdict_potentiel.grille",
            f"{ENTREE_RECOMMANDATIONS}.verdict_potentiel.regle_appliquee",
            f"{ENTREE_RECOMMANDATIONS}.donnees_a_completer",
            "simulation_bascules (code)",
        ],
        SECTION_DEMANDE: [f"{ENTREE_RECOMMANDATIONS}.dossier_synthese.demande"],
    }
    if entrees.insights is not None:
        refs[SECTION_CONSOMMATEURS] = [
            f"{ENTREE_INSIGHTS}.pain_points",
            f"{ENTREE_INSIGHTS}.besoins",
            f"{ENTREE_INSIGHTS}.attentes",
            f"{ENTREE_INSIGHTS}.sentiment",
        ]
    else:
        refs[SECTION_CONSOMMATEURS] = [
            f"{ENTREE_RECOMMANDATIONS}.dossier_synthese.consommateur (écho)"
        ]
    if entrees.concurrence is not None:
        refs[SECTION_CONCURRENCE] = [
            f"{ENTREE_CONCURRENCE}.intensite_concurrentielle",
            f"{ENTREE_CONCURRENCE}.tableau_comparatif",
            f"{ENTREE_CONCURRENCE}.benchmark_prix",
            f"{ENTREE_CONCURRENCE}.positionnement",
        ]
    else:
        refs[SECTION_CONCURRENCE] = [
            f"{ENTREE_RECOMMANDATIONS}.dossier_synthese.concurrence (écho)"
        ]
    if entrees.plc is not None:
        refs[SECTION_PLC] = [
            f"{ENTREE_PLC}.classification",
            f"{ENTREE_PLC}.signaux",
            f"{ENTREE_PLC}.recommandations_phase",
        ]
    else:
        refs[SECTION_PLC] = ["aucune — analyse de cycle de vie non fournie"]
    refs["recommandations"] = [
        f"{ENTREE_RECOMMANDATIONS}.recommandations_produit",
        f"{ENTREE_RECOMMANDATIONS}.recommandation_positionnement",
        f"{ENTREE_RECOMMANDATIONS}.recommandations_marketing",
        f"{ENTREE_RECOMMANDATIONS}.recommandation_prix",
    ]
    refs["opportunites_risques"] = [
        f"{ENTREE_RECOMMANDATIONS}.opportunites",
        f"{ENTREE_RECOMMANDATIONS}.risques",
    ]
    refs["annexe"] = [
        f"{nom}.limites" for nom in entrees.blocs_disponibles
    ] + [f"{ENTREE_RECOMMANDATIONS}.hypotheses"]
    refs["entete"] = [f"{ENTREE_RECOMMANDATIONS}.produit", f"{ENTREE_RECOMMANDATIONS}.marche"]
    _ = injectables
    return refs


def horodatage() -> str:
    """Retourne l'horodatage courant en ISO 8601 UTC.

    Returns:
        Un horodatage à la seconde, suffixé « Z ».
    """
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
