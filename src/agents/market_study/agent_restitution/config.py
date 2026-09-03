"""Gabarit du rapport, formats, badges, règles de formulation et tolérances.

Aucune valeur magique ne doit exister ailleurs que dans ce module.

Ce module porte trois choses qui engagent la sincérité du rapport :

1. le **gabarit** — l'ordre des sections, leurs sources et la longueur maximale
   de leur narratif ;
2. les **règles de formulation** — injectées dans tous les prompts **et**
   contrôlées par `validation.py` : ce qui est interdit au modèle est vérifié
   sur le texte produit, jamais seulement demandé ;
3. les **constantes de la règle de verdict de F5**, nécessaires à la simulation
   des bascules. Elles sont recopiées ici parce que la sortie F5 ne publie
   qu'un énoncé littéral, pas un bloc structuré — voir `preparation.py`.

Ce module ne dépend d'aucun autre module interne.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from dotenv import load_dotenv

# --------------------------------------------------------------------------- #
# Encodage — correctif obligatoire
# --------------------------------------------------------------------------- #
for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

load_dotenv()

FORMAT_LOG: str = "%(asctime)s [%(levelname)s] %(name)s : %(message)s"
NOM_LOGGER: str = "agent_restitution"


def configurer_logs(verbeux: bool) -> logging.Logger:
    """Configure la journalisation vers `stderr`.

    Args:
        verbeux: Si vrai, niveau DEBUG ; sinon WARNING.

    Returns:
        Le logger du module.
    """
    gestionnaire = logging.StreamHandler(stream=sys.stderr)
    gestionnaire.setFormatter(logging.Formatter(FORMAT_LOG))
    logger_local = logging.getLogger(NOM_LOGGER)
    logger_local.handlers.clear()
    logger_local.addHandler(gestionnaire)
    logger_local.setLevel(logging.DEBUG if verbeux else logging.WARNING)
    logger_local.propagate = False
    return logger_local


logger = logging.getLogger(NOM_LOGGER)

# --------------------------------------------------------------------------- #
# Modèle LLM — un seul niveau
# --------------------------------------------------------------------------- #
# `claude-sonnet-5` remplace `claude-sonnet-4-5-20250929` (02/09/2026) : même
# famille, génération postérieure, et un tarif inférieur d'un tiers dans les deux
# sens (2/10 $ contre 3/15 $ par million de jetons). Il refuse `temperature` ;
# `construire_modele` ne la transmet donc qu'aux modèles qui l'acceptent.

MODELE_SYNTHESE: str = "claude-sonnet-5"
TEMPERATURE: float = 0.0

MODELES_SANS_ECHANTILLONNAGE: frozenset[str] = frozenset(
    {"claude-sonnet-5", "claude-opus-5"}
)
"""Modèles de génération courante : ils refusent tout paramètre d'échantillonnage.

`temperature`, `top_p` et `top_k` y renvoient une erreur 400. Le déterminisme que
`TEMPERATURE = 0` visait n'était de toute façon pas atteint : deux exécutions du
même code sur les mêmes entrées produisent déjà des sorties différentes
(cf. `docs/baseline_jetons.md` §7). `TEMPERATURE` reste transmise aux modèles qui
l'acceptent encore, Haiku 4.5 en particulier.
"""

RAISONNEMENT_SYNTHESE: dict[str, str] = {"type": "disabled"}
"""Raisonnement adaptatif de la génération courante, désactivé par défaut.

Désactivé, le modèle se comporte comme Sonnet 4.5 — aucun jeton de raisonnement
facturé — donc la baisse de tarif 3/15 → 2/10 $ par million de jetons est acquise
sans contrepartie sur la sortie. Passer à `{"type": "adaptive"}`, éventuellement
avec `output_config={"effort": "low"}`, est le seul arbitrage qualité/coût de ce
module : il demande une campagne de mesure avant d'être adopté.
"""

MAX_TOKENS_SYNTHESE: int = 4000

NOM_VARIABLE_CLE_API: str = "ANTHROPIC_API_KEY"

TARIFS_USD_PAR_MTOK: dict[str, tuple[float, float]] = {MODELE_SYNTHESE: (2.00, 10.00)}
"""Tarif public (entrée, sortie) par million de jetons, pour estimation seule."""

NB_TENTATIVES_LLM: int = 2

# --------------------------------------------------------------------------- #
# Entrées consommées
# --------------------------------------------------------------------------- #

ENTREE_RECOMMANDATIONS: str = "recommandations"
ENTREE_INSIGHTS: str = "insights"
ENTREE_CONCURRENCE: str = "concurrence"
ENTREE_PLC: str = "plc"

ENTREES_CONNUES: tuple[str, ...] = (
    ENTREE_RECOMMANDATIONS,
    ENTREE_INSIGHTS,
    ENTREE_CONCURRENCE,
    ENTREE_PLC,
)

# --------------------------------------------------------------------------- #
# Gabarit du rapport
# --------------------------------------------------------------------------- #

SECTION_ENTETE: str = "entete"
SECTION_SYNTHESE: str = "synthese"
SECTION_VERDICT: str = "verdict"
SECTION_PLC: str = "plc"
SECTION_DEMANDE: str = "demande"
SECTION_CONSOMMATEURS: str = "consommateurs"
SECTION_CONCURRENCE: str = "concurrence"
SECTION_RECOMMANDATIONS: str = "recommandations"
SECTION_OPPORTUNITES_RISQUES: str = "opportunites_risques"
SECTION_ANNEXE: str = "annexe"

GABARIT_RAPPORT: tuple[dict[str, Any], ...] = (
    {
        "id": SECTION_ENTETE,
        "titre": "Étude de marché",
        "entrees_requises": (ENTREE_RECOMMANDATIONS,),
        "longueur_narrative_max_mots": 0,
    },
    {
        "id": SECTION_SYNTHESE,
        "titre": "Synthèse exécutive",
        "entrees_requises": (ENTREE_RECOMMANDATIONS,),
        "longueur_narrative_max_mots": 180,
    },
    {
        "id": SECTION_VERDICT,
        "titre": "Verdict de potentiel",
        "entrees_requises": (ENTREE_RECOMMANDATIONS,),
        "longueur_narrative_max_mots": 150,
    },
    {
        "id": SECTION_PLC,
        "titre": "Phase de cycle de vie du marché",
        "entrees_requises": (ENTREE_PLC,),
        "longueur_narrative_max_mots": 140,
    },
    {
        "id": SECTION_DEMANDE,
        "titre": "Demande observée",
        "entrees_requises": (ENTREE_RECOMMANDATIONS,),
        "longueur_narrative_max_mots": 140,
    },
    {
        "id": SECTION_CONSOMMATEURS,
        "titre": "Besoins et attentes exprimés",
        "entrees_requises": (ENTREE_INSIGHTS,),
        "longueur_narrative_max_mots": 200,
    },
    {
        "id": SECTION_CONCURRENCE,
        "titre": "Paysage concurrentiel",
        "entrees_requises": (ENTREE_CONCURRENCE,),
        "longueur_narrative_max_mots": 200,
    },
    {
        "id": SECTION_RECOMMANDATIONS,
        "titre": "Recommandations",
        "entrees_requises": (ENTREE_RECOMMANDATIONS,),
        "longueur_narrative_max_mots": 0,
    },
    {
        "id": SECTION_OPPORTUNITES_RISQUES,
        "titre": "Opportunités et risques",
        "entrees_requises": (ENTREE_RECOMMANDATIONS,),
        "longueur_narrative_max_mots": 0,
    },
    {
        "id": SECTION_ANNEXE,
        "titre": "Annexe — sources, méthode et limites",
        "entrees_requises": (ENTREE_RECOMMANDATIONS,),
        "longueur_narrative_max_mots": 0,
    },
)

SECTIONS_NARRATIVES: tuple[str, ...] = (
    SECTION_SYNTHESE,
    SECTION_VERDICT,
    SECTION_PLC,
    SECTION_DEMANDE,
    SECTION_CONSOMMATEURS,
    SECTION_CONCURRENCE,
)
"""Seules sections faisant l'objet d'un appel LLM."""

NUMEROS_SECTIONS: dict[str, int] = {
    section["id"]: rang
    for rang, section in enumerate(
        [s for s in GABARIT_RAPPORT if s["id"] != SECTION_ENTETE], start=1
    )
}
"""Numéro d'affichage de chaque section. L'en-tête n'est pas numéroté."""

SECTIONS_HORS_CONTROLE_TERMES: tuple[str, ...] = (SECTION_ANNEXE,)
"""Sections où les termes interdits sont tolérés : les limites amont sont
recopiées verbatim, et elles emploient parfois le vocabulaire qu'on s'interdit
d'employer soi-même."""

# --------------------------------------------------------------------------- #
# Formats
# --------------------------------------------------------------------------- #

SEPARATEUR_DECIMAL: str = ","
SEPARATEUR_MILLIERS: str = " "
DECIMALES_POURCENTAGE: int = 1
DECIMALES_MONTANT: int = 2
FORMAT_DATE: str = "%d/%m/%Y"

TOLERANCE_ARRONDI_PCT: float = 0.1
"""Écart absolu toléré entre un nombre du rapport et une valeur de la liste
blanche, pour absorber les arrondis d'affichage (0,1 point)."""

MAX_CARACTERES_CELLULE: int = 90
MAX_CARACTERES_JUSTIFICATION: int = 160
MAX_CARACTERES_BADGE: int = 180
MAX_VERBATIM_CARACTERES: int = 200
NB_PAIN_POINTS_RAPPORT: int = 5
NB_CONCURRENTS_TABLEAU: int = 8
NB_FAITS_CLES_SYNTHESE: int = 5
MIN_FAITS_CLES_SYNTHESE: int = 3
NB_RECOMMANDATIONS_SYNTHESE: int = 3
MIN_RESERVES_SYNTHESE: int = 2
MAX_RESERVES_SYNTHESE: int = 4
MAX_ANGLES_RAPPORT: int = 6
MAX_NORMES_RAPPORT: int = 6
MAX_OPPORTUNITES_RAPPORT: int = 6
MAX_RISQUES_RAPPORT: int = 8
MAX_DONNEES_A_COMPLETER: int = 8
MAX_LIMITES_PAR_FAMILLE: int = 12
SEUIL_REGENERATION_PCT: float = 30.0
"""Au-delà de ce pourcentage de narratif retiré, une seule régénération LLM est
tentée ; ensuite la section est réduite à ses tableaux."""

# --------------------------------------------------------------------------- #
# Règles de formulation — injectées dans les prompts ET contrôlées
# --------------------------------------------------------------------------- #

REGLES_FORMULATION: str = (
    "RÈGLES DE FORMULATION — non négociables, elles sont vérifiées sur ton texte :\n"
    "- Parle toujours des « avis et discussions analysés », du « corpus collecté », "
    "des « annonces observées ». N'écris JAMAIS « les consommateurs espagnols », "
    "« les acheteurs », ni aucune généralisation à une population : le corpus n'est "
    "pas un échantillon représentatif.\n"
    "- Les absences restent relatives au corpus : « non observé dans les N annonces "
    "et M pages collectées », jamais « inexistant sur le marché ».\n"
    "- Réponse d'abord : ouvre par le constat principal, les chiffres viennent en "
    "appui, jamais l'inverse.\n"
    "- TOUT CHIFFRE que tu emploies doit être RECOPIÉ EXACTEMENT depuis les données "
    "fournies. Aucun calcul, aucune somme, aucun pourcentage déduit, aucun ordre de "
    "grandeur de mémoire. Un chiffre absent des données fournies fera retirer la "
    "phrase entière.\n"
    "- Les divergences entre sources s'EXPLIQUENT (biais de plateforme, langue, "
    "public) ; elles ne se moyennent jamais.\n"
    "- Aucun superlatif promotionnel, aucune promesse de résultat, aucun "
    "adoucissement d'un constat défavorable.\n"
    "- N'emploie aucun vocabulaire interne : ni nom d'agent, ni « référence », ni "
    "« LLM », ni identifiant technique. Tu écris pour un décideur, pas pour un "
    "ingénieur. En particulier, n'écris jamais « unité » ni « unités » pour "
    "désigner un élément du corpus : écris « contribution », « avis » ou "
    "« message ».\n"
    "- N'utilise AUCUNE connaissance extérieure aux données fournies : aucun fait de "
    "marché mémorisé, aucune statistique sectorielle, aucun nom de concurrent absent "
    "des données."
)

TERMES_INTERDITS: tuple[str, ...] = (
    "part de marché",
    "parts de marché",
    "volume de demande",
    "taille du marché",
    "taille de marché",
    "les consommateurs veulent",
    "les consommateurs recherchent",
    "les consommateurs attendent",
    "les acheteurs veulent",
)
"""Termes interdits hors annexe : chacun affirme une grandeur ou une intention
que le corpus ne permet pas d'établir."""

TERMES_JARGON: tuple[str, ...] = (
    "llm",
    "prompt",
    "dossier_synthese",
    "declenche_plc",
    "non_evaluable",
    "verdict_potentiel",
    "agent_",
    "json",
    "ref:",
    "(ref",
    "id_reco",
    "pain point",
    "pain points",
)
"""Jargon interne proscrit dans le corps du rapport. « pain point » en fait
partie : le rapport dit « point de friction », terme masculin qui préserve les
accords du texte amont."""

NEGATIONS_TOLEREES: tuple[str, ...] = (
    "aucun",
    "aucune",
    "ne peut",
    "ne peuvent",
    "pas de",
    "jamais",
    "n'en découle",
    "sans",
)
"""Marqueurs de négation : « aucune part de marché ne peut en être déduite » est
un avertissement, pas une affirmation interdite."""

MOTIF_REFERENCE_TECHNIQUE: str = (
    r"\s*\(\s*(?:cf\.?\s*)?refs?\s*[:=][^)]*\)|\s*\[\s*(?:cf\.?\s*)?refs?\s*[:=][^\]]*\]"
)
"""Références techniques formant à elles seules une parenthèse."""

MOTIF_REFERENCE_INTERNE: str = r"\s*[,;]?\s*refs?\s*[:=]\s*[A-Za-z0-9_.\[\]\-]+"
"""Références techniques glissées au milieu d'une parenthèse chiffrée.

Elles servent l'audit, pas le lecteur : elles sont retirées du corps du rapport,
où la traçabilité passe par les commentaires HTML de fin de section.
"""

MOTIFS_NETTOYAGE: tuple[tuple[str, str], ...] = (
    (r"\(\s*[,;]?\s*\)", ""),
    (r"\s+([,.])", r"\1"),
    (r"[ \t]{2,}", " "),
)
"""Nettoyage typographique après retrait : parenthèses vidées, espaces doublés."""

MOTIF_PREFIXE_AGENT: str = r"(?im)^\s*agents?\s+F\d+\s*(?:\([^\)]*\))?\s*:\s*"
"""Préfixe « Agent F3 (Insights consommateur) : » des textes amont."""

SUBSTITUTIONS_TEXTE: tuple[tuple[str, str], ...] = (
    # --- Généralisations à une population ---------------------------------- #
    # Un avis analysé n'autorise à parler que de la personne qui l'a écrit.
    (r"\bLes consommateurs\b", "Les personnes dont les avis ont été analysés"),
    (r"\bles consommateurs\b", "les personnes dont les avis ont été analysés"),
    (r"\bDes consommateurs\b", "Des personnes dont les avis ont été analysés"),
    (r"\bdes consommateurs\b", "des personnes dont les avis ont été analysés"),
    (r"\bLes acheteurs\b", "Les personnes dont les avis ont été analysés"),
    (r"\bles acheteurs\b", "les personnes dont les avis ont été analysés"),
    # --- Vocabulaire interne ------------------------------------------------ #
    # « point de friction » est MASCULIN, comme « pain point » : le remplacement
    # laisse les accords du texte amont intacts. « difficulté rapportée », féminin,
    # produisait « une difficulté rapportée majeur » et « la difficulté rapportée le
    # plus fréquent » — c'est le défaut relevé par le métier.
    (r"\bCe pain point\b", "Ce point de friction"),
    (r"\bce pain point\b", "ce point de friction"),
    (r"\bLe pain point\b", "Le point de friction"),
    (r"\ble pain point\b", "le point de friction"),
    (r"\bdu pain point\b", "du point de friction"),
    (r"\bUn pain point\b", "Un point de friction"),
    (r"\bun pain point\b", "un point de friction"),
    (r"\bPain points\b", "Points de friction"),
    (r"\bpain points\b", "points de friction"),
    (r"\bPain point\b", "Point de friction"),
    (r"\bpain point\b", "point de friction"),
    # Rattrapage du vocabulaire de la version 1, encore présent dans des textes
    # amont produits avant la bascule.
    (r"\bdifficultés rapportées\b", "points de friction"),
    (r"\bDifficultés rapportées\b", "Points de friction"),
    (r"\bune difficulté rapportée\b", "un point de friction"),
    (r"\bla difficulté rapportée\b", "le point de friction"),
    (r"\bdifficulté rapportée\b", "point de friction"),
    (r"\bDifficulté rapportée\b", "Point de friction"),
    # « unités » n'est traduit que lorsqu'il désigne le corpus : « 3000 unités »
    # vendues doit rester intact.
    (r"\bd'unités\b", "de contributions"),
    (r"\bunités analysées\b", "contributions analysées"),
    (r"\bunités (positives|négatives|neutres|mixtes)\b", r"contributions \1"),
    (r"\bverbatims\b", "extraits"),
    (r"\bverbatim\b", "extrait"),
    (r"\bcorpus F3\b", "corpus des avis et discussions"),
    (r"\bcorpus F4\b", "corpus concurrentiel"),
)
"""Substitutions déterministes appliquées aux textes amont injectés dans le
corps du rapport, dans cet ordre.

Elles préservent le sens : elles suppriment une généralisation que le corpus ne
permet pas, ou traduisent un terme interne. Elles sont comptées et publiées en
statut, et ne s'appliquent **jamais aux limites**, restituées verbatim en
annexe, ni aux extraits, conservés dans leur langue d'origine.
"""

SUBSTITUTS_VERDICT_INTERDITS: tuple[str, ...] = (
    "mitigé",
    "mitige",
    "prometteur",
    "encourageant",
    "nuancé",
    "contrasté",
    "favorable",
    "défavorable",
)
"""Substituts interdits dans le titre de la section verdict : le mot du verdict
s'écrit tel quel, jamais adouci."""

# --------------------------------------------------------------------------- #
# Verdicts et badges
# --------------------------------------------------------------------------- #

VERDICT_POSITIF: str = "positif"
VERDICT_NEGATIF: str = "negatif"
VERDICT_INDETERMINE: str = "indetermine"

VERDICT_LISIBLE: dict[str, str] = {
    VERDICT_POSITIF: "positif",
    VERDICT_NEGATIF: "négatif",
    VERDICT_INDETERMINE: "indéterminé",
}

PHASE_LISIBLE: dict[str, str] = {
    "introduction": "introduction",
    "croissance": "croissance",
    "maturite": "maturité",
    "declin": "déclin",
}

CONFIANCE_ELEVEE: str = "elevee"
CONFIANCE_MOYENNE: str = "moyenne"
CONFIANCE_FAIBLE: str = "faible"

BADGES_CONFIANCE: dict[str, str] = {
    CONFIANCE_ELEVEE: "Fiabilité élevée",
    CONFIANCE_MOYENNE: "Fiabilité moyenne",
    CONFIANCE_FAIBLE: "Fiabilité faible — à lire comme indicatif",
}

BADGE_INCONNU: str = "Fiabilité non qualifiée"

# --------------------------------------------------------------------------- #
# RÈGLE DE VERDICT DE F5 — recopiée pour la simulation des bascules
# --------------------------------------------------------------------------- #
# La sortie F5 ne publie qu'un ÉNONCÉ LITTÉRAL de sa règle, pas un bloc
# structuré. `preparation.py` tente d'en relire les seuils par expression
# régulière ; en cas d'échec, ces constantes prennent le relais et le fait est
# publié en hypothèse. Un champ `verdict_potentiel.parametres_regle` structuré
# dans la sortie F5 supprimerait cette fragilité.

MIN_CRITERES_EVALUES_F5: int = 4
SEUIL_POSITIF_F5: int = 6
SEUIL_NEGATIF_F5: int = 3
MAX_NON_EVALUABLES_POSITIF_F5: int = 1
CRITERE_DEMANDE_F5: str = "demande"
CRITERE_DIFFERENCIATION_F5: str = "differenciation"
SCORES_MUTATION: tuple[int, ...] = (1, 2)
SCORE_MAX_CRITERE: int = 2

BESOIN_PARAMETRES_REGLE: str = (
    "La règle de verdict n'est publiée par l'analyse amont que sous forme d'énoncé "
    "littéral : ses seuils ont dû être relus par expression régulière, à défaut "
    "repris de constantes locales. Un bloc structuré `parametres_regle` dans la "
    "sortie de l'analyse amont supprimerait cette fragilité."
)

# --------------------------------------------------------------------------- #
# Familles de limites
# --------------------------------------------------------------------------- #

FAMILLE_AUTRES: str = "autres"

LIMITES_FAMILLES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "regles_internes",
        "Règles d'analyse en hypothèse de travail",
        (
            "hypothèse de travail",
            "hypothese de travail",
            "règle de verdict",
            "recalibr",
            "grille",
            "non validée",
            "non validé",
            "arbitraire",
            "seuil",
            "heuristique",
        ),
    ),
    (
        "publicite",
        "Signaux publicitaires",
        (
            "annonce",
            "publicit",
            "date_fin",
            "longévité",
            "longevite",
            "meta",
            "diffusion",
            "annonceur",
            "créatif",
        ),
    ),
    (
        "tendances",
        "Indicateurs de tendance",
        (
            "trends",
            "tendance",
            "indice",
            "momentum",
            "saisonnal",
            "relatif",
            "mot-clé",
            "mot clé",
        ),
    ),
    (
        "prix_devises",
        "Prix et devises",
        (
            "devise",
            "prix",
            "conversion",
            "taux de change",
            "livraison",
            "marge",
            "coût",
            "cout",
            "rentabilité",
        ),
    ),
    (
        "heuristiques_llm",
        "Classifications automatiques non validées",
        (
            "classification",
            "pertinence",
            "échantillonnage",
            "echantillonnage",
            "modèle",
            "modele",
            "extraction",
            "rapprochement",
            "sentiment",
        ),
    ),
    (
        "couverture_plateformes",
        "Couverture des plateformes",
        (
            "couverture",
            "plateforme",
            "marketplace",
            "api",
            "quota",
            "dropshipping",
            "indexation",
            "blocage",
            "collecte",
            "amazon",
            "aliexpress",
            "reddit",
        ),
    ),
    (
        "representativite",
        "Représentativité des corpus",
        (
            "représentat",
            "representat",
            "échantillon",
            "echantillon",
            "population",
            "biais",
            "exhaust",
            "langue",
            "corpus",
        ),
    ),
)
"""Familles de limites, testées dans l'ordre : la première correspondance gagne.
L'ordre va du plus spécifique au plus général."""

LIBELLE_FAMILLE_AUTRES: str = "Autres limites"

# --------------------------------------------------------------------------- #
# Encarts et blocs constants
# --------------------------------------------------------------------------- #

AVERTISSEMENT_METHODE: tuple[str, ...] = (
    "Ce rapport restitue des analyses produites en amont ; il ne les revalide pas. "
    "Leurs limites, consolidées en annexe, s'appliquent intégralement.",
    "Les corpus exploités ne sont pas des échantillons représentatifs : ils "
    "décrivent ce qui a été collecté, jamais un marché dans son ensemble. Aucun "
    "volume, aucune part de marché et aucune projection de vente n'en découlent.",
    "Aucune donnée financière interne n'est disponible : les prix cités sont des "
    "positionnements observés chez des concurrents, jamais des calculs de "
    "rentabilité.",
)

ENCART_PLC_NON_DECLENCHEE: str = (
    "> **Phase de cycle de vie non déterminée.** La classification de phase n'a pas "
    "été déclenchée. Motif repris de l'analyse de cycle de vie : {motif}\n>\n"
    "> Aucune phase n'est donc proposée dans ce rapport, et aucune recommandation "
    "spécifique à une phase n'y figure."
)

ENCART_PLC_ABSENTE: str = (
    "> **Phase de cycle de vie non déterminée.** L'analyse de cycle de vie n'a pas "
    "été fournie pour cette étude : cette section reste vide et aucune phase n'est "
    "proposée. Étude partielle."
)

MENTION_ETUDE_PARTIELLE: str = (
    "> **Étude partielle — {libelle} non disponible.** Cette section est construite "
    "à partir du rappel qu'en fait l'analyse de synthèse, et non de l'analyse "
    "détaillée. Le détail — {detail} — n'est pas disponible dans cette étude."
)

ENCART_NARRATIF_INDISPONIBLE: str = (
    "> **Lecture narrative indisponible.** La rédaction de cette section n'a pas "
    "abouti. Les données ci-dessous sont exactes et complètes ; seule leur mise en "
    "récit manque."
)

LIBELLES_ENTREES: dict[str, str] = {
    ENTREE_RECOMMANDATIONS: "analyse de synthèse",
    ENTREE_INSIGHTS: "analyse des avis et discussions",
    ENTREE_CONCURRENCE: "analyse concurrentielle",
    ENTREE_PLC: "analyse de cycle de vie",
}

DETAILS_ENTREES: dict[str, str] = {
    ENTREE_INSIGHTS: (
        "verbatims, répartition du sentiment par source, hiérarchie complète des "
        "points de friction"
    ),
    ENTREE_CONCURRENCE: (
        "comparatif détaillé des concurrents, benchmark de prix par source et par "
        "devise, standards observés"
    ),
    ENTREE_PLC: "phase de cycle de vie et recommandations propres à cette phase",
}

METHODOLOGIE: tuple[str, ...] = (
    "1. Six collectes indépendantes alimentent l'étude : discussions publiques, "
    "avis de marketplace, pages web éditoriales et marchandes, catalogues de "
    "fournisseurs, annonces publicitaires et indicateurs de recherche.",
    "2. Chaque collecte est régionalisée et menée dans la langue du marché étudié. "
    "Un marché bilingue donne lieu à une étude par langue, jamais à une moyenne.",
    "3. Les avis et discussions collectés sont découpés en contributions "
    "élémentaires, échantillonnés de façon stratifiée, puis cartographiés : "
    "sentiment, thèmes, points de friction, besoins et attentes.",
    "4. Les offres, annonces et pages sont consolidées en concurrents, avec un "
    "niveau de certitude explicite sur chaque rapprochement.",
    "5. Le benchmark de prix est établi SOURCE PAR SOURCE ET DEVISE PAR DEVISE. "
    "Aucune conversion de devise n'est effectuée à aucune étape : deux prix "
    "libellés dans deux devises décrivent deux marchés, pas le même montant.",
    "6. Une grille de potentiel à cinq critères est notée, puis un verdict est "
    "calculé par une règle déterministe. La notation est assistée ; la décision "
    "est du code, rejouable à l'identique.",
    "7. Tous les nombres publiés sont calculés par du code déterministe, puis "
    "réécrits lors d'une post-validation. Aucun nombre produit par un modèle "
    "n'atteint ce rapport.",
    "8. Toute affirmation étayée cite une donnée vérifiable de l'analyse ; une "
    "citation introuvable est retirée et tracée, jamais laissée en place.",
    "9. Ce rapport est assemblé à partir des seules analyses fournies. Aucune "
    "connaissance extérieure, aucune statistique sectorielle et aucun nom de "
    "concurrent absent des analyses n'y ont été ajoutés.",
    "10. Les limites de chaque étape sont propagées jusqu'ici sans être réécrites : "
    "elles figurent en annexe dans leur formulation d'origine.",
)

GLOSSAIRE: tuple[tuple[str, str], ...] = (
    (
        "Indice de recherche",
        "Valeur RELATIVE à la période interrogée, normalisée sur 100 au maximum de "
        "la série. Elle ne porte aucun volume absolu de recherche, et donc aucune "
        "taille de marché.",
    ),
    (
        "Momentum",
        "Variation de l'indice de recherche sur les 90 derniers jours par rapport à "
        "la période précédente. Un momentum négatif décrit un recul relatif, pas une "
        "disparition de la demande.",
    ),
    (
        "Pente annuelle",
        "Progression moyenne de l'indice de recherche par an sur cinq ans, exprimée "
        "en points d'indice. Elle décrit une tendance de fond, pas une prévision.",
    ),
    (
        "Longévité publicitaire",
        "Durée pendant laquelle une annonce est restée diffusée. Elle mesure une "
        "PERSISTANCE, jamais une rentabilité : une campagne longue n'est pas une "
        "campagne qui gagne.",
    ),
    (
        "Annonce active",
        "Annonce encore diffusée au jour de la collecte. La date de fin affichée par "
        "la régie sur une annonce active vaut la date du jour : ce n'est pas une "
        "date d'arrêt.",
    ),
    (
        "Contribution analysée",
        "Un avis, un message ou un extrait de page retenu dans le corpus. Le nombre "
        "de contributions mesure une activité de collecte, jamais un nombre "
        "d'acheteurs.",
    ),
    (
        "Segment de prix",
        "Découpage d'une distribution de prix observée sur UNE source et UNE devise, "
        "en entrée de gamme, cœur de marché et premium.",
    ),
    (
        "Score de la grille de potentiel",
        "0 = signal défavorable net, 1 = signal mitigé ou contrasté, 2 = signal "
        "favorable net. « Non évaluable » signifie que la donnée manque, pas que le "
        "signal est mauvais.",
    ),
)

# --------------------------------------------------------------------------- #
# Codes de sortie du CLI
# --------------------------------------------------------------------------- #

CODE_SUCCES: int = 0
CODE_ERREUR_IMPREVUE: int = 1
CODE_ENTREE_INEXPLOITABLE: int = 2

CHEMIN_RAPPORT_DEFAUT: str = "rapport_etude.md"
CHEMIN_RESUME_DEFAUT: str = "resume_executif.md"

# --------------------------------------------------------------------------- #
# Limites et hypothèses systématiques
# --------------------------------------------------------------------------- #

LIMITES_SYSTEMATIQUES: tuple[str, ...] = (
    "Ce rapport restitue des analyses amont sans les revalider : leurs limites "
    "s'appliquent intégralement et sont consolidées en annexe.",
    "Les corpus exploités ne sont pas des échantillons représentatifs des marchés "
    "étudiés : aucune part de marché, aucun volume de demande et aucune projection "
    "de vente ne peuvent en être déduits.",
    "Aucune donnée financière interne (coûts d'achat, marges, frais logistiques, "
    "budget publicitaire) n'est disponible : les prix cités sont des "
    "POSITIONNEMENTS DE MARCHÉ observés, jamais des calculs de rentabilité.",
    "La mise en récit peut lisser des nuances présentes dans les analyses "
    "détaillées : en cas de doute, les sorties structurées des agents d'analyse "
    "font foi.",
)

HYPOTHESES_SYSTEMATIQUES: tuple[str, ...] = (
    f"Gabarit du rapport : {len(GABARIT_RAPPORT)} sections dans un ordre fixe, dont "
    f"{len(SECTIONS_NARRATIVES)} comportent un narratif rédigé, borné en nombre de "
    f"mots (de 140 à 200 mots selon la section).",
    f"Contrôle numérique : un nombre du rapport est accepté s'il correspond à une "
    f"valeur des analyses fournies ou à une variante admise (arrondi à 1 ou 2 "
    f"décimales, écriture française, ratio exprimé en pourcentage), à "
    f"{TOLERANCE_ARRONDI_PCT} point près.",
    f"Regroupement des limites : {len(LIMITES_FAMILLES)} familles thématiques, "
    f"attribuées par mots-clés dans l'ordre du plus spécifique au plus général. "
    f"Le texte des limites n'est jamais réécrit.",
    "Simulation des bascules de verdict : la règle de l'analyse amont est relue "
    "depuis son énoncé littéral ; à défaut, des constantes locales prennent le "
    "relais, ce qui est alors signalé.",
)

# --------------------------------------------------------------------------- #
# Plomberie LLM partagée
# --------------------------------------------------------------------------- #


def verifier_cle_api() -> None:
    """Vérifie la présence de la clé API Anthropic.

    Raises:
        RuntimeError: Si la variable d'environnement est absente ou vide.
    """
    if not os.environ.get(NOM_VARIABLE_CLE_API, "").strip():
        raise RuntimeError(
            f"{NOM_VARIABLE_CLE_API} est absente. Renseigne-la dans un fichier .env "
            f"(voir .env.example) ou dans l'environnement."
        )


def construire_modele() -> Any:
    """Instancie le modèle de rédaction à température nulle.

    Returns:
        Une instance de `ChatAnthropic`.
    """
    from langchain_anthropic import ChatAnthropic

    options: dict[str, Any] = {}
    if MODELE_SYNTHESE in MODELES_SANS_ECHANTILLONNAGE:
        options["thinking"] = RAISONNEMENT_SYNTHESE
    else:
        options["temperature"] = TEMPERATURE

    return ChatAnthropic(
        model=MODELE_SYNTHESE,
        max_tokens=MAX_TOKENS_SYNTHESE,
        timeout=None,
        stop=None,
        **options,
    )


def invoquer_structure(
    chaine: Any, entree: dict[str, Any], libelle: str
) -> tuple[Any | None, int, str | None]:
    """Invoque une chaîne LCEL à sortie structurée, avec une reprise sur échec.

    Args:
        chaine: Chaîne LCEL dotée de `with_structured_output`.
        entree: Variables du gabarit. La clé `erreur_precedente` est réservée.
        libelle: Libellé de l'étape, pour la journalisation.

    Returns:
        Le triplet `(resultat_ou_None, nb_tentatives, message_erreur)`.
    """
    derniere_erreur: str | None = None
    for tentative in range(1, NB_TENTATIVES_LLM + 1):
        variables = dict(entree)
        variables.setdefault("erreur_precedente", "")
        if derniere_erreur is not None:
            variables["erreur_precedente"] = (
                "\n\nATTENTION — ta réponse précédente a été rejetée pour cette "
                f"raison :\n{derniere_erreur}\nCorrige-la et respecte strictement "
                "le schéma demandé."
            )
        try:
            resultat = chaine.invoke(variables)
        except Exception as erreur:  # noqa: BLE001 — toute erreur doit dégrader
            derniere_erreur = f"{type(erreur).__name__} : {erreur}"
            logger.warning(
                "%s — tentative %d/%d en échec : %s",
                libelle,
                tentative,
                NB_TENTATIVES_LLM,
                derniere_erreur,
            )
            continue
        if resultat is None:
            derniere_erreur = "le modèle n'a retourné aucune sortie structurée"
            continue
        logger.debug("%s — succès en %d tentative(s)", libelle, tentative)
        return resultat, tentative, None
    return None, NB_TENTATIVES_LLM, derniere_erreur


# --------------------------------------------------------------------------- #
# Comptabilité des jetons
# --------------------------------------------------------------------------- #
# Multiplicateurs appliqués au tarif d'entrée de base pour les jetons de cache.
# Ces rapports sont les mêmes sur tous les modèles Claude : seule la base varie.
MULT_CACHE_LECTURE: float = 0.10
MULT_CACHE_ECRITURE_5MIN: float = 1.25
MULT_CACHE_ECRITURE_1H: float = 2.00


def resumer_consommation(usage: dict[str, Any]) -> str:
    """Résume la consommation de jetons d'une exécution et son coût estimé.

    POURQUOI LE CACHE EST VENTILÉ — `langchain_anthropic` rajoute les jetons de
    cache dans `input_tokens` (l'`input_tokens` d'Anthropic, lui, les exclut).
    Les tarifer au tarif d'entrée plein surfacturerait une lecture de cache d'un
    facteur dix et, surtout, afficherait un coût identique avec et sans mise en
    cache : le rapport ne montrerait aucune économie là où elle serait pourtant
    réelle. Le détail est donc repris de `input_token_details` et tarifé à part.

    Args:
        usage: Dictionnaire `modèle → métadonnées d'usage`, tel que produit par
            `langchain_core.callbacks.get_usage_metadata_callback`.

    Returns:
        Une ligne de récapitulatif, vide si aucun appel n'a été passé.
    """
    if not usage:
        return ""
    morceaux: list[str] = []
    total = 0.0
    tarif_manquant = False
    for modele, metriques in sorted(usage.items()):
        details = metriques.get("input_token_details") or {}
        lecture = int(details.get("cache_read", 0) or 0)
        # `cache_creation` et la ventilation par TTL s'excluent mutuellement :
        # `langchain_anthropic` remet la première à zéro dès que la seconde est
        # renseignée. Les additionner ne double donc jamais le compte.
        ecriture_5min = int(details.get("ephemeral_5m_input_tokens", 0) or 0)
        ecriture_1h = int(details.get("ephemeral_1h_input_tokens", 0) or 0)
        # Écriture dont le TTL n'est pas ventilé : tarifée au TTL par défaut.
        ecriture_indistincte = int(details.get("cache_creation", 0) or 0)
        ecriture = ecriture_5min + ecriture_1h + ecriture_indistincte

        entree_totale = int(metriques.get("input_tokens", 0) or 0)
        # Le solde est ce qui n'a été ni lu ni écrit en cache : plein tarif.
        entree_neuve = max(entree_totale - lecture - ecriture, 0)
        sortie = int(metriques.get("output_tokens", 0) or 0)

        tarifs = TARIFS_USD_PAR_MTOK.get(modele)
        if tarifs is None:
            # Un modèle absent de la table valait auparavant 0 $ sans le dire :
            # une migration de modèle rendait le rapport faux en silence.
            tarif_manquant = True
            tarif_entree, tarif_sortie = 0.0, 0.0
        else:
            tarif_entree, tarif_sortie = tarifs

        cout = (
            entree_neuve * tarif_entree
            + lecture * tarif_entree * MULT_CACHE_LECTURE
            + (ecriture_5min + ecriture_indistincte) * tarif_entree * MULT_CACHE_ECRITURE_5MIN
            + ecriture_1h * tarif_entree * MULT_CACHE_ECRITURE_1H
            + sortie * tarif_sortie
        ) / 1_000_000
        total += cout

        ligne = f"{modele} : {entree_neuve} jetons entrée / {sortie} sortie"
        if lecture or ecriture:
            part_lue = 100.0 * lecture / entree_totale if entree_totale else 0.0
            ligne += (
                f" / {lecture} cache lu ({part_lue:.0f} % de l'entrée)"
                f" / {ecriture} cache écrit"
            )
        ligne += " (tarif inconnu)" if tarifs is None else f" (~{cout:.4f} $)"
        morceaux.append(ligne)

    recapitulatif = " | ".join(morceaux) + f" | total estimé ~{total:.4f} $"
    if tarif_manquant:
        recapitulatif += (
            " | ATTENTION : un modèle absent de TARIFS_USD_PAR_MTOK a été compté "
            "pour 0 $ — le total est sous-estimé"
        )
    return recapitulatif


# --------------------------------------------------------------------------- #
# GABARIT V2 — « rapport décisionnel »
# --------------------------------------------------------------------------- #
# Décision métier du 03/09/2026 : le rapport v1 est jugé trop long, redondant et
# organisé par blocs de données. Le v2 le réorganise en CINQ ÉCRANS répondant à
# des questions métier, tout narratif en puces courtes, budget total ≈ 1 200 mots
# contre ≈ 7 000. Les invariants de sûreté sont inchangés et partagés entre les
# deux gabarits : liste blanche numérique, recopie du verdict, simulation des
# bascules, limites verbatim, dégradation gracieuse.
#
# Le v1 reste sélectionnable (`--gabarit v1`) le temps de la transition : il
# n'est pas dupliqué, seules les fonctions de rendu diffèrent.

GABARIT_V1: str = "v1"
GABARIT_V2: str = "v2"
GABARITS_DISPONIBLES: tuple[str, ...] = (GABARIT_V1, GABARIT_V2)
GABARIT_PAR_DEFAUT: str = GABARIT_V2

MARQUEUR_GABARIT_V2: str = "<!-- f7:v2 -->"
"""Marqueur de tête permettant au frontend de reconnaître le gabarit v2.

Sans lui, le frontend applique son rendu historique — bandes d'extraits en bas de
page. Sa présence est contrôlée par la post-validation.
"""

GABARIT_WIDGET_EXTRAITS: str = '<!-- widget:extraits source="{source}" -->'
SOURCES_WIDGETS: tuple[str, ...] = ("amazon", "aliexpress", "meta_ads", "recherche_web")
"""Sources pour lesquelles le frontend sait rendre une bande d'extraits."""

ECRAN_DECISION: str = "decision"
ECRAN_CONSOMMATEUR: str = "consommateur"
ECRAN_CONCURRENCE: str = "concurrence_v2"
ECRAN_RECOMMANDATIONS: str = "recommandations_v2"
ECRAN_METHODE: str = "methode"

# --- Sous-blocs : identifiant et question métier affichée ------------------- #
# Le sous-titre EST la question à laquelle les puces répondent. C'est le cœur du
# v2 : un décideur ne cherche pas « les besoins », il cherche « pourquoi
# achètent-ils ? ».

SB_POURQUOI: str = "pourquoi"
SB_RISQUE_PRINCIPAL: str = "risque_principal"
SB_CHANGER_DECISION: str = "changer_decision"
SB_MANQUE_TRANCHER: str = "manque_trancher"

SB_POURQUOI_ACHAT: str = "pourquoi_achat"
SB_APPRECIENT: str = "apprecient"
SB_DERANGE: str = "derange"
SB_AIMERAIENT: str = "aimeraient"

SB_DYNAMIQUE: str = "dynamique_demande"
SB_QUE_FONT: str = "que_font_concurrents"
SB_EXEMPLES: str = "exemples_observes"
SB_PRIX_PRATIQUES: str = "prix_pratiques"
SB_CINQ_FORCES: str = "cinq_forces"
SB_PERSONNE_NE_FAIT: str = "personne_ne_fait"

SB_PHASE: str = "phase_vie"
SB_ACTIONS: str = "actions_prioritaires"
SB_PRIX: str = "prix"
SB_ENTREE_MARCHE: str = "entree_marche"
SB_OPPORTUNITES_RISQUES: str = "opportunites_risques"

GABARIT_RAPPORT_V2: tuple[dict[str, Any], ...] = (
    {
        "id": ECRAN_DECISION,
        "titre": "Décision",
        "entrees_requises": (ENTREE_RECOMMANDATIONS,),
        "budget_mots": 200,
        "narratif": True,
        "sous_blocs": (
            (SB_POURQUOI, "Pourquoi"),
            (SB_RISQUE_PRINCIPAL, "Le risque principal"),
            (SB_CHANGER_DECISION, "Ce qui ferait changer la décision"),
            (SB_MANQUE_TRANCHER, "Ce qu'il manque pour trancher"),
        ),
    },
    {
        "id": ECRAN_CONSOMMATEUR,
        "titre": "Le consommateur",
        "entrees_requises": (ENTREE_INSIGHTS,),
        "budget_mots": 350,
        "narratif": True,
        "sous_blocs": (
            (SB_POURQUOI_ACHAT, "Pourquoi ils achètent — ou non"),
            (SB_APPRECIENT, "Ce qu'ils apprécient"),
            (SB_DERANGE, "Ce qui les dérange"),
            (SB_AIMERAIENT, "Ce qu'ils aimeraient trouver"),
        ),
    },
    {
        "id": ECRAN_CONCURRENCE,
        "titre": "Le marché et les concurrents",
        "entrees_requises": (ENTREE_CONCURRENCE,),
        "budget_mots": 350,
        "narratif": True,
        "sous_blocs": (
            (SB_DYNAMIQUE, "Dynamique de la demande"),
            (SB_QUE_FONT, "Que font les concurrents ?"),
            (SB_EXEMPLES, "Exemples observés"),
            (SB_PRIX_PRATIQUES, "Prix pratiqués"),
            (SB_CINQ_FORCES, "Les 5 forces"),
            (SB_PERSONNE_NE_FAIT, "Ce que personne ne fait"),
        ),
    },
    {
        "id": ECRAN_RECOMMANDATIONS,
        "titre": "Ce que nous recommandons",
        "entrees_requises": (ENTREE_RECOMMANDATIONS,),
        "budget_mots": 300,
        "narratif": True,
        "sous_blocs": (
            (SB_PHASE, "Phase de vie du marché"),
            (SB_ACTIONS, "Actions prioritaires"),
            (SB_PRIX, "Prix"),
            (SB_ENTREE_MARCHE, "Entrée sur le marché"),
            (SB_OPPORTUNITES_RISQUES, "Opportunités et risques"),
        ),
    },
    {
        "id": ECRAN_METHODE,
        "titre": "Méthode et limites",
        "entrees_requises": (ENTREE_RECOMMANDATIONS,),
        "budget_mots": 0,
        "narratif": False,
        "sous_blocs": (),
    },
)

ECRANS_NARRATIFS: tuple[str, ...] = tuple(
    ecran["id"] for ecran in GABARIT_RAPPORT_V2 if ecran["narratif"]
)

ECRANS_HORS_CONTROLE_TERMES: tuple[str, ...] = (ECRAN_METHODE,)
"""L'écran méthode recopie les limites amont verbatim : leur vocabulaire n'est
pas le nôtre et ne se corrige pas."""

SOUS_BLOCS_REDIGES: dict[str, tuple[str, ...]] = {
    ECRAN_DECISION: (SB_POURQUOI, SB_RISQUE_PRINCIPAL),
    ECRAN_CONSOMMATEUR: (SB_POURQUOI_ACHAT, SB_APPRECIENT, SB_DERANGE, SB_AIMERAIENT),
    ECRAN_CONCURRENCE: (SB_DYNAMIQUE, SB_QUE_FONT, SB_PRIX_PRATIQUES),
    ECRAN_RECOMMANDATIONS: (SB_PRIX, SB_ENTREE_MARCHE),
}
"""Sous-blocs confiés au modèle. Tous les autres sont produits par le code :
tableaux, lignes standard, sélections.

Deux sous-blocs en sont volontairement absents alors qu'ils portent du texte :

- « Ce que personne ne fait » recopie les angles peu exploités de l'analyse
  concurrentielle. Leur formulation — « non observé dans le corpus » — est une
  précaution de méthode : la faire reformuler, c'est risquer de la perdre.
- « Phase de vie du marché » recopie les recommandations de phase de l'analyse de
  cycle de vie, pour la même raison."""

BUDGET_MOTS: dict[str, int] = {
    ecran["id"]: ecran["budget_mots"] for ecran in GABARIT_RAPPORT_V2
}
BUDGET_MOTS_TOTAL: int = 1200
"""Budget total du narratif, hors tableaux et hors blocs repliés."""

# --------------------------------------------------------------------------- #
# Libellés de décision
# --------------------------------------------------------------------------- #
# Le métier décide en Go / No-go ; l'analyse conclut en positif / négatif /
# indéterminé. Les DEUX sont affichés : le libellé en titre, le verdict brut sur
# la ligne suivante. Aucun ne remplace l'autre — traduire sans montrer l'original
# serait exactement l'adoucissement que ce module s'interdit.

LIBELLES_VERDICT: dict[str, str] = {
    VERDICT_POSITIF: "Go",
    VERDICT_NEGATIF: "No-go",
    VERDICT_INDETERMINE: "Go conditionnel",
}

PHRASE_GO_CONDITIONNEL_SANS_CONDITION: str = (
    "Conditions non déterminables à partir des analyses disponibles."
)
"""Repli imposé : un « Go conditionnel » sans condition affichée ne dit rien au
lecteur."""

# --------------------------------------------------------------------------- #
# Bornes de forme du gabarit v2
# --------------------------------------------------------------------------- #

NB_FAITS_CLES_DECISION: int = 3
NB_POINTS_FRICTION: int = 5
NB_VERBATIMS: int = 2
NB_CONCURRENTS_TABLEAU_V2: int = 6
NB_ACTIONS_P1: int = 5
NB_OPPORTUNITES: int = 3
NB_RISQUES: int = 3
NB_MANQUES_DECISION: int = 3
MAX_MOTS_PUCE: int = 30
MAX_MOTS_PUCE_CONCURRENTS: int = 35
MAX_MOTS_CELLULE_COURTE: int = 12
MAX_MOTS_ACTION: int = 40

# --------------------------------------------------------------------------- #
# Les 5 forces
# --------------------------------------------------------------------------- #
# L'analyse de synthèse ne publie pas encore de bloc `cinq_forces`. Trois des
# cinq forces sont donc ESTIMÉES par règle déterministe à partir des chiffres
# disponibles, et les deux autres affichées « non évalué ». Les seuils ci-dessous
# sont des HYPOTHÈSES DE TRAVAIL, publiées comme telles en annexe : ils ordonnent
# des observations, ils ne mesurent rien.

FORCE_RIVALITE: str = "rivalite"
FORCE_ENTREE: str = "entree"
FORCE_CLIENTS: str = "clients"
FORCE_FOURNISSEURS: str = "fournisseurs"
FORCE_SUBSTITUTS: str = "substituts"

LIBELLES_CINQ_FORCES: tuple[tuple[str, str], ...] = (
    (FORCE_RIVALITE, "Rivalité actuelle"),
    (FORCE_ENTREE, "Facilité d'entrée de nouveaux concurrents"),
    (FORCE_CLIENTS, "Pouvoir des clients"),
    (FORCE_FOURNISSEURS, "Pouvoir des fournisseurs"),
    (FORCE_SUBSTITUTS, "Menace des substituts"),
)

NIVEAU_FAIBLE: str = "faible"
NIVEAU_MOYEN: str = "moyen"
NIVEAU_ELEVE: str = "élevé"
NIVEAU_NON_EVALUE: str = "non évalué"

SEUILS_CINQ_FORCES: dict[str, int] = {
    "rivalite_concurrents_eleve": 30,
    "rivalite_offres_eleve": 100,
    "rivalite_concurrents_moyen": 10,
    "rivalite_offres_moyen": 30,
    "clients_offres_coeur": 30,
}
SEUIL_PRIX_ENTREE_FACILE: float = 15.0
"""Médiane du canal le moins cher sous laquelle l'entrée est réputée facile, dans
la devise du benchmark — aucune conversion n'est faite, aucune n'est suggérée."""

MENTION_ESTIMATION_REGLE: str = "estimation par règle"
LECTURE_FORCE_NON_EVALUEE: str = "Non couvert par les analyses de cette version."

CINQ_FORCES_SOURCE_F5: str = "f5"
CINQ_FORCES_SOURCE_REGLES: str = "regles_locales"
CINQ_FORCES_SOURCE_ABSENTE: str = "non_evalue"

# --------------------------------------------------------------------------- #
# Phases du cycle de vie — ligne d'affichage
# --------------------------------------------------------------------------- #

PHASES_PLC: tuple[tuple[str, str], ...] = (
    ("introduction", "Introduction"),
    ("croissance", "Croissance"),
    ("maturite", "Maturité"),
    ("declin", "Déclin"),
)

PHRASE_PLC_NON_EVALUEE: str = (
    "Phase non évaluée : le cycle de vie n'est estimé que lorsque la décision est Go."
)

# --------------------------------------------------------------------------- #
# Phrases standard des sous-blocs sans donnée
# --------------------------------------------------------------------------- #
# Un sous-bloc sans donnée affiche sa phrase — jamais une page vide, jamais une
# invention. Le cas est tracé dans `statuts_analyse`.

PHRASE_NON_DOCUMENTE: str = "Non documenté dans le corpus analysé."
PHRASE_TENDANCES_ABSENTES: str = (
    "Tendances de recherche non collectées pour cette étude : la dynamique de la "
    "demande n'est pas évaluée."
)
PHRASE_CLIENTELE_NON_CARACTERISEE: str = (
    "Clientèle visée : non caractérisée dans cette version de l'analyse."
)
PHRASE_AUCUNE_BASCULE: str = (
    "Aucune amélioration d'un seul critère de la grille ne suffirait à changer la "
    "décision."
)
PHRASE_RAPPEL_PRIX: str = (
    "Positionnement de marché observé, jamais un calcul de rentabilité : aucune "
    "donnée de coût n'est disponible."
)
ENCART_ETUDE_PARTIELLE_V2: str = "> **Étude partielle : {blocs} {accord}.**"
"""Une ligne, jamais davantage. Le v1 consacrait une page à dire « indisponible ».

L'accord est calculé plutôt que parenthésé : « non disponible(s) » est le même
défaut de forme que « difficulté rapportée majeur » dans le corps du rapport."""

# --------------------------------------------------------------------------- #
# Unités de volume de ventes
# --------------------------------------------------------------------------- #
# L'analyse concurrentielle amont publie un unique `volume_ventes_cumule`, alors
# que les canaux ne mesurent pas la même chose : Amazon publie un volume d'achats
# MENSUEL, AliExpress un nombre de commandes CUMULÉ depuis la mise en ligne. Les
# afficher sur une même colonne sans unité est trompeur. F7 rétablit l'unité
# quand la ligne ne porte qu'une source ; quand elle en porte plusieurs, l'unité
# est déclarée indéterminée plutôt que devinée.

UNITES_VOLUME: dict[str, str] = {
    "amazon": "/mois",
    "aliexpress": "cumulé",
}
UNITE_VOLUME_INDETERMINEE: str = "unité indéterminée"

BESOIN_UNITES_VOLUME: str = (
    "Les volumes de ventes des canaux sont publiés par l'analyse concurrentielle "
    "sur un champ unique, alors qu'ils ne mesurent pas la même chose : volume "
    "mensuel d'un côté, cumul depuis la mise en ligne de l'autre. L'unité est "
    "rétablie à la restitution quand la ligne ne porte qu'un canal ; elle reste "
    "indéterminée quand elle en porte plusieurs."
)

BESOIN_CINQ_FORCES_AMONT: str = (
    "Les cinq forces ne sont pas publiées par l'analyse de synthèse : trois d'entre "
    "elles sont estimées à la restitution par une règle déterministe appliquée aux "
    "chiffres disponibles, les deux autres sont déclarées non évaluées. Ces seuils "
    "sont une hypothèse de travail, pas une mesure."
)

BESOIN_CLIENTELE_CIBLE: str = (
    "La clientèle visée par chaque concurrent n'est pas caractérisée par l'analyse "
    "concurrentielle : le sous-bloc correspondant affiche sa phrase standard."
)

# --------------------------------------------------------------------------- #
# Règles de formulation propres au v2
# --------------------------------------------------------------------------- #

REGLES_FORMULATION_V2: str = (
    "FORME IMPOSÉE — elle est vérifiée sur ta sortie :\n"
    "- Tu écris des PUCES, jamais des paragraphes. Une puce est une phrase, au plus "
    "{max_mots} mots. Aucune prose continue.\n"
    "- Chaque sous-bloc porte une QUESTION en titre. Tes puces y répondent dès le "
    "premier mot : pas d'introduction, pas de transition, pas de reformulation de la "
    "question.\n"
    "- Tu écris pour un décideur qui accorde deux minutes au rapport. Ce qui ne "
    "l'aide pas à décider n'a pas à être écrit.\n"
    "- Le terme admis pour ce qui gêne les personnes dont les avis ont été analysés "
    "est « point de friction ». N'écris jamais « pain point » ni « difficulté "
    "rapportée »."
)

TERME_FRICTION_SINGULIER: str = "point de friction"
TERME_FRICTION_PLURIEL: str = "points de friction"
