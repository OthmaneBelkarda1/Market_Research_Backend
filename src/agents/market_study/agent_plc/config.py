"""Phases, familles de signaux, grille de lecture, pondérations et plomberie LLM.

Aucune valeur magique ne doit exister ailleurs que dans ce module.

⚠️ **La grille de classification de phase n'est définie ni dans le cahier des
charges, ni dans la spécification fonctionnelle générale.** Ce qui suit reprend
la *grille de lecture indicative* de la note de structuration des agents
d'analyse (§6) comme **HYPOTHÈSE DE TRAVAIL** : le modèle oriente les signaux,
**le code agrège et décide**. Les constantes sont ajustables et le statut
d'hypothèse est publié dans chaque sortie via `statut_regle`.

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
NOM_LOGGER: str = "agent_plc"


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
# Les deux chaînes de cet agent relèvent du jugement (orienter des signaux,
# formuler des recommandations de phase) : aucune étape mécanique ne justifie un
# modèle d'extraction. Écart assumé, identique à celui de F5, vis-à-vis de la
# convention « haiku » des collecteurs.
#
# Identifiant vérifié le 06/08/2026 : disponible à l'API Anthropic.
# `claude-sonnet-4-5-20250929` est un modèle « legacy actif » ; la génération
# courante `claude-sonnet-5` rejette toute `temperature` non par défaut, ce qui
# est incompatible avec l'exigence de température 0.

MODELE_SYNTHESE: str = "claude-sonnet-4-5-20250929"
TEMPERATURE: float = 0.0
MAX_TOKENS_SYNTHESE: int = 8000

NOM_VARIABLE_CLE_API: str = "ANTHROPIC_API_KEY"

TARIFS_USD_PAR_MTOK: dict[str, tuple[float, float]] = {MODELE_SYNTHESE: (3.00, 15.00)}
"""Tarif public (entrée, sortie) par million de jetons, pour estimation seule.

Valeur saisie à la main, non interrogée en ligne : à vérifier avant tout usage
budgétaire.
"""

NB_TENTATIVES_LLM: int = 2

# --------------------------------------------------------------------------- #
# Entrées consommées
# --------------------------------------------------------------------------- #

ENTREE_RECOMMANDATIONS: str = "recommandations"
ENTREE_INSIGHTS: str = "insights"
ENTREE_CONCURRENCE: str = "concurrence"

SOURCE_CONCURRENCE: str = "concurrence"
SOURCE_ECHO_F5: str = "echo_f5"
SOURCE_INSIGHTS: str = "insights"

# --------------------------------------------------------------------------- #
# Phases du cycle de vie
# --------------------------------------------------------------------------- #

PHASE_INTRODUCTION: str = "introduction"
PHASE_CROISSANCE: str = "croissance"
PHASE_MATURITE: str = "maturite"
PHASE_DECLIN: str = "declin"

PHASES: tuple[str, ...] = (
    PHASE_INTRODUCTION,
    PHASE_CROISSANCE,
    PHASE_MATURITE,
    PHASE_DECLIN,
)
"""Une classification porte UNE SEULE de ces valeurs, ou `None`."""

ORIENTATION_NEUTRE: str = "neutre"
"""Orientation admise mais qui n'alimente le score d'aucune phase."""

# --------------------------------------------------------------------------- #
# Familles de signaux
# --------------------------------------------------------------------------- #

FAMILLE_DEMANDE: str = "demande"
FAMILLE_PUBLICITE: str = "dynamique_publicitaire"
FAMILLE_OFFRE: str = "structure_offre"
FAMILLE_CORPUS: str = "corpus_avis"

FAMILLES_SIGNAUX: tuple[dict[str, Any], ...] = (
    {
        "id": FAMILLE_DEMANDE,
        "intitule": "Trajectoire de la demande",
        "entrees_sources": (ENTREE_RECOMMANDATIONS,),
        "indicateurs_attendus": (
            "profil_courbe",
            "pente_annuelle_5ans",
            "momentum_90j",
            "indice_moyen_12m",
            "saisonnalite",
            "signal_effet_de_mode",
        ),
    },
    {
        "id": FAMILLE_PUBLICITE,
        "intitule": "Dynamique des campagnes Meta",
        "entrees_sources": (ENTREE_CONCURRENCE, ENTREE_RECOMMANDATIONS),
        "indicateurs_attendus": (
            "part_lancements_recents",
            "repartition_lancements_mensuels",
            "anciennete_mediane_actives_jours",
            "anciennete_max_actives_jours",
            "nb_arrets_recents",
            "part_annonces_actives",
        ),
    },
    {
        "id": FAMILLE_OFFRE,
        "intitule": "Structure et saturation de l'offre",
        "entrees_sources": (ENTREE_CONCURRENCE, ENTREE_RECOMMANDATIONS),
        "indicateurs_attendus": (
            "nb_concurrents",
            "nb_offres_coeur",
            "concentration_volumes_top3_pct",
            "part_offres_sans_marque",
            "nb_marques_etablies",
        ),
    },
    {
        "id": FAMILLE_CORPUS,
        "intitule": "Récence et densité du corpus d'avis",
        "entrees_sources": (ENTREE_INSIGHTS,),
        "indicateurs_attendus": (
            "periode_couverte",
            "nb_unites_par_source",
            "anciennete_corpus_jours",
        ),
    },
)

IDS_FAMILLES: tuple[str, ...] = tuple(f["id"] for f in FAMILLES_SIGNAUX)

TYPE_OFFRES_SANS_MARQUE: str = "offres_sans_marque"
TYPE_MARQUE_ETABLIE: str = "marque_etablie"
"""Valeurs de `concurrents[].concurrent.type` exploitées par la famille offre."""

# --------------------------------------------------------------------------- #
# Grille de lecture indicative — HYPOTHÈSE DE TRAVAIL
# --------------------------------------------------------------------------- #

GRILLE_LECTURE: str = (
    "GRILLE DE LECTURE INDICATIVE — CE SONT DES HYPOTHÈSES DE TRAVAIL, PAS DES "
    "RÈGLES VALIDÉES. Elles orientent la lecture, elles ne la décident pas.\n"
    "- Forte part de lancements publicitaires récents SANS campagnes anciennes → "
    "introduction ou croissance.\n"
    "- Coexistence de campagnes durables ET de lancements continus → croissance "
    "vers maturité.\n"
    "- Campagnes vétéranes dominantes, lancements raréfiés → maturité avancée.\n"
    "- Arrêts réels récents nombreux, annonceurs en raréfaction → signal de déclin.\n"
    "- Demande : pente positive soutenue et momentum positif → croissance ; plateau "
    "à haut niveau → maturité ; pente et momentum négatifs durables → déclin ; "
    "niveau faible mais émergent → introduction.\n"
    "- Offre : peu d'acteurs et faible densité → introduction ; prolifération "
    "d'offres sans marque et forte densité → maturité.\n"
    "- Corpus d'avis : ancien et dense → marché installé ; récent et croissant → "
    "croissance.\n"
    "- CES SIGNAUX SE CROISENT, ILS NE S'INTERPRÈTENT JAMAIS ISOLÉMENT."
)

PIEGES_OPPOSABLES: str = (
    "PIÈGES DE LECTURE HÉRITÉS DES COLLECTEURS — opposables à toute conclusion :\n"
    "- `date_fin` d'une annonce ACTIVE vaut la date du jour de collecte, jamais une "
    "date d'arrêt : seul le drapeau `active` fait foi. Aucun arrêt de campagne ne "
    "peut être déduit d'une date de fin.\n"
    "- La longévité publicitaire mesure une PERSISTANCE de diffusion, jamais une "
    "rentabilité : une campagne longue n'est pas une campagne qui gagne.\n"
    "- L'indice Google Trends est RELATIF (base 100 sur la période interrogée) : il "
    "ne porte aucun volume absolu de recherche, donc aucune taille de marché.\n"
    "- Les volumes de corpus mesurent une ACTIVITÉ DE COLLECTE, jamais un marché : "
    "213 messages collectés ne disent rien du nombre d'acheteurs.\n"
    "- Une absence dans le corpus est une absence d'observation, pas une absence "
    "de marché."
)

# --------------------------------------------------------------------------- #
# AGRÉGATION — HYPOTHÈSE DE TRAVAIL, à recalibrer sur cas réels
# --------------------------------------------------------------------------- #

POIDS_FAMILLES: dict[str, float] = {
    FAMILLE_DEMANDE: 0.35,
    FAMILLE_PUBLICITE: 0.30,
    FAMILLE_OFFRE: 0.20,
    FAMILLE_CORPUS: 0.15,
}
"""HYPOTHÈSE DE TRAVAIL — à recalibrer sur cas réels."""

VALEUR_FORCE: dict[str, int] = {"faible": 1, "moyenne": 2, "forte": 3}
"""HYPOTHÈSE DE TRAVAIL — à recalibrer sur cas réels."""

FORCES: tuple[str, ...] = ("faible", "moyenne", "forte")
FORCE_DEFAUT: str = "faible"

MIN_FAMILLES_EVALUEES: int = 2
"""En deçà, `phase_probable = None` et incertitude « elevee »."""

SEUIL_ECART_ELEVEE: float = 0.15
SEUIL_ECART_MOYENNE: float = 0.40
"""Écart relatif entre la 1re et la 2e phase — HYPOTHÈSES DE TRAVAIL."""

POIDS_FAMILLE_STRUCTURANTE: float = 0.30
"""Une famille de ce poids ou plus, non évaluable, impose une incertitude élevée."""

INCERTITUDE_FAIBLE: str = "faible"
INCERTITUDE_MOYENNE: str = "moyenne"
INCERTITUDE_ELEVEE: str = "elevee"

STATUT_REGLE: str = "hypothese_de_travail_a_valider"
"""Constante publiée dans chaque sortie : la grille n'est pas validée."""

# --------------------------------------------------------------------------- #
# Déclenchement
# --------------------------------------------------------------------------- #

MODE_NORMAL: str = "normal"
MODE_FORCE: str = "force"
MODE_NON_DECLENCHE: str = "non_declenche"

VERDICT_POSITIF: str = "positif"

LIMITE_EXECUTION_FORCEE: str = (
    "Exécution forcée à des fins d'étude : le verdict amont n'est pas positif, "
    "cette classification ne doit pas être utilisée en décision. Le drapeau "
    "`--forcer` est interdit à l'orchestrateur en production."
)

# --------------------------------------------------------------------------- #
# Recommandations de phase
# --------------------------------------------------------------------------- #

DOMAINE_PLC: str = "plc"

MIN_RECOMMANDATIONS_PHASE: int = 3
MAX_RECOMMANDATIONS_PHASE: int = 6

ANGLES_PAR_PHASE: dict[str, tuple[str, ...]] = {
    PHASE_INTRODUCTION: (
        "éducation du marché et pédagogie du bénéfice",
        "construction de la preuve d'efficacité",
        "ciblage des early adopters identifiables dans le corpus",
        "arbitrage pénétration contre écrémage, à instruire et non à trancher",
    ),
    PHASE_CROISSANCE: (
        "sécurisation de la capacité d'approvisionnement",
        "différenciation construite avant la saturation",
        "acquisition scalable sur les canaux observés",
        "construction de la preuve sociale",
    ),
    PHASE_MATURITE: (
        "différenciation fine ou repli sur une niche défendable",
        "défense du prix face à la banalisation",
        "fidélisation et rétention plutôt que conquête",
        "optimisation des coûts d'acquisition",
    ),
    PHASE_DECLIN: (
        "limitation des investissements et critère d'arrêt chiffré",
        "exploitation des niches résiduelles",
        "écoulement des stocks",
        "pivot produit ou sortie ordonnée",
    ),
}

PRIORITES: tuple[str, ...] = ("P1", "P2", "P3")
HORIZONS: tuple[str, ...] = ("immediat", "court_terme", "moyen_terme")
EFFORTS: tuple[str, ...] = ("faible", "moyen", "eleve")

TYPE_FAIT: str = "fait"
TYPE_HYPOTHESE: str = "hypothese"

MENTION_NON_ANCREE: str = (
    "Recommandation non ancrée : aucun fondement factuel vérifiable du dossier PLC "
    "ne la soutient. Elle relève du jugement de l'analyste et doit être traitée "
    "comme telle."
)

# --------------------------------------------------------------------------- #
# Confiance et fraîcheur
# --------------------------------------------------------------------------- #

CONFIANCE_ELEVEE: str = "elevee"
CONFIANCE_MOYENNE: str = "moyenne"
CONFIANCE_FAIBLE: str = "faible"
NIVEAUX_CONFIANCE: tuple[str, ...] = (
    CONFIANCE_FAIBLE,
    CONFIANCE_MOYENNE,
    CONFIANCE_ELEVEE,
)

SEUIL_FRAICHEUR_JOURS: int = 30
"""Au-delà, une entrée horodatée est signalée comme potentiellement périmée."""

MAX_CONDITIONS_REEXAMEN: int = 6
MAX_FAITS_CLES: int = 8
MAX_INDICATEURS_PAR_FAIT_CLE: int = 2
"""Bornes de la restitution : les faits clés sont construits par le code."""

# --------------------------------------------------------------------------- #
# Codes de sortie du CLI
# --------------------------------------------------------------------------- #

CODE_SUCCES: int = 0
CODE_ERREUR_IMPREVUE: int = 1
CODE_ENTREE_INEXPLOITABLE: int = 2

# --------------------------------------------------------------------------- #
# Limites et hypothèses systématiques
# --------------------------------------------------------------------------- #

LIMITE_D4_PUBLICITE: str = (
    "La famille de signaux « dynamique publicitaire » est non évaluable : "
    "`intensite_concurrentielle.dynamique_publicitaire` est absente des entrées "
    "(exigence D4 non encore implémentée en amont dans F4). Elle n'a PAS été "
    "reconstituée localement : les seules durées de diffusion disponibles ne "
    "permettent aucun calcul valide, la date de fin d'une annonce active valant la "
    "date de collecte. Cette famille pèse "
    f"{POIDS_FAMILLES[FAMILLE_PUBLICITE]:.2f} dans l'agrégation : son absence "
    "dégrade mécaniquement la classification."
)

LIMITES_SYSTEMATIQUES: tuple[str, ...] = (
    "La grille de lecture des phases et les pondérations d'agrégation sont des "
    "HYPOTHÈSES DE TRAVAIL, non des arbitrages validés : ni le cahier des charges "
    "ni la spécification fonctionnelle ne définissent de grille de cycle de vie. "
    "Elles doivent être recalibrées sur des cas réels avant tout usage décisionnel.",
    "La classification décrit LE MARCHÉ DE LA CATÉGORIE TEL QU'OBSERVÉ DANS LE "
    "CORPUS collecté en amont — ni le produit lui-même, ni un marché exhaustif. Un "
    "corpus partiel produit une phase partielle.",
    "La qualité de cette classification est bornée par celle des analyses amont : "
    "un signal mal caractérisé par F3, F4 ou le collecteur Tendances se propage ici "
    "sans être détecté.",
    "Les signaux publicitaires mesurent une ACTIVITÉ DE DIFFUSION, jamais une "
    "rentabilité : une campagne longue n'est pas une campagne rentable.",
    "Aucun re-scoring du potentiel commercial n'est effectué ici : le verdict de "
    "F5 fait foi et n'est ni recalculé, ni commenté, ni contredit.",
)

HYPOTHESES_SYSTEMATIQUES: tuple[str, ...] = (
    "Pondérations d'agrégation : "
    + ", ".join(f"{cle} = {valeur:.2f}" for cle, valeur in POIDS_FAMILLES.items())
    + " ; valeurs de force : "
    + ", ".join(f"{cle} = {valeur}" for cle, valeur in VALEUR_FORCE.items())
    + " (heuristiques non validées).",
    f"Seuils d'incertitude : « elevee » si l'écart relatif entre les deux premières "
    f"phases est inférieur à {SEUIL_ECART_ELEVEE:.2f} ou si une famille de poids "
    f"≥ {POIDS_FAMILLE_STRUCTURANTE:.2f} est non évaluable ; « moyenne » si cet "
    f"écart est inférieur à {SEUIL_ECART_MOYENNE:.2f} ; « faible » au-delà. "
    f"Minimum de {MIN_FAMILLES_EVALUEES} familles évaluées pour classer.",
    "Fenêtres de récence publicitaire héritées telles quelles de F4, sans "
    "recalcul : leur définition appartient à l'agent amont.",
    f"Une entrée horodatée de plus de {SEUIL_FRAICHEUR_JOURS} jours est signalée "
    f"comme potentiellement périmée ; les entrées sans horodatage ne peuvent pas "
    f"être qualifiées.",
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
    """Instancie le modèle de synthèse à température nulle.

    Returns:
        Une instance de `ChatAnthropic`.
    """
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=MODELE_SYNTHESE,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS_SYNTHESE,
        timeout=None,
        stop=None,
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
        variables["erreur_precedente"] = (
            ""
            if derniere_erreur is None
            else (
                "\n\nATTENTION — ta réponse précédente a été rejetée pour cette "
                f"raison :\n{derniere_erreur}\nCorrige-la et respecte strictement "
                "le schéma demandé."
            )
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


def resumer_consommation(usage: dict[str, Any]) -> str:
    """Résume la consommation de jetons et son coût estimé.

    Args:
        usage: Dictionnaire `modèle → métadonnées d'usage`.

    Returns:
        Une ligne de récapitulatif, vide si aucun appel n'a été passé.
    """
    if not usage:
        return ""
    morceaux: list[str] = []
    total = 0.0
    for modele, metriques in sorted(usage.items()):
        entree = int(metriques.get("input_tokens", 0))
        sortie = int(metriques.get("output_tokens", 0))
        tarif_entree, tarif_sortie = TARIFS_USD_PAR_MTOK.get(modele, (0.0, 0.0))
        cout = (entree * tarif_entree + sortie * tarif_sortie) / 1_000_000
        total += cout
        morceaux.append(
            f"{modele} : {entree} jetons entrée / {sortie} sortie (~{cout:.4f} $)"
        )
    return " | ".join(morceaux) + f" | total estimé ~{total:.4f} $"
