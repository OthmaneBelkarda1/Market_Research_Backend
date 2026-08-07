"""Constantes, grille de potentiel, règle de verdict et plomberie LLM.

Aucune valeur magique ne doit exister ailleurs que dans ce module.

⚠️ **La règle de « potentiel commercial » n'est définie ni dans le cahier des
charges, ni dans la spécification fonctionnelle générale.** Ce qui suit est une
HYPOTHÈSE DE TRAVAIL conservatrice et auditable : le modèle note des critères,
**le code applique la règle**. Les constantes sont ajustables et l'hypothèse est
signalée dans chaque sortie via `statut_regle`.

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
NOM_LOGGER: str = "agent_recommandations_strategiques"


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
# Toutes les chaînes de cet agent relèvent du jugement : il n'existe ici aucune
# étape mécanique justifiant un modèle d'extraction. Écart assumé et documenté
# vis-à-vis de la convention « haiku » des collecteurs.
#
# Identifiant vérifié le 05/08/2026 : disponible à l'API Anthropic.
# `claude-sonnet-4-5-20250929` est un modèle « legacy actif » ; la génération
# courante `claude-sonnet-5` rejette toute `temperature` non par défaut, ce qui
# est incompatible avec l'exigence de température 0.

MODELE_SYNTHESE: str = "claude-sonnet-4-5-20250929"
TEMPERATURE: float = 0.0
MAX_TOKENS_SYNTHESE: int = 16000

NOM_VARIABLE_CLE_API: str = "ANTHROPIC_API_KEY"

TARIFS_USD_PAR_MTOK: dict[str, tuple[float, float]] = {MODELE_SYNTHESE: (3.00, 15.00)}
"""Tarif public (entrée, sortie) par million de jetons, pour estimation seule.

Valeur saisie à la main, non interrogée en ligne : à vérifier avant tout usage
budgétaire.
"""

NB_TENTATIVES_LLM: int = 2

# --------------------------------------------------------------------------- #
# Grille de potentiel
# --------------------------------------------------------------------------- #

CRITERE_DEMANDE: str = "demande"
CRITERE_INTENSITE: str = "intensite"
CRITERE_DIFFERENCIATION: str = "differenciation"
CRITERE_ADEQUATION: str = "adequation"
CRITERE_VIABILITE_PRIX: str = "viabilite_prix"

ENTREE_TENDANCES: str = "tendances"
ENTREE_INSIGHTS: str = "insights"
ENTREE_CONCURRENCE: str = "concurrence"

GRILLE_CRITERES: tuple[dict[str, Any], ...] = (
    {
        "id": CRITERE_DEMANDE,
        "intitule": "Dynamique de la demande",
        "question": (
            "La demande est-elle établie ou en croissance, sans dépendre d'un pic "
            "éphémère ?"
        ),
        "sources_attendues": (ENTREE_TENDANCES,),
        "fonde_sur": "profil_courbe, momentum_90j, pente_annuelle_5ans, saisonnalité",
    },
    {
        "id": CRITERE_INTENSITE,
        "intitule": "Intensité concurrentielle soutenable",
        "question": "Le niveau de concurrence laisse-t-il une place à un entrant ?",
        "sources_attendues": (ENTREE_CONCURRENCE,),
        "fonde_sur": (
            "intensité concurrentielle, concentration des volumes, nombre "
            "d'annonceurs"
        ),
    },
    {
        "id": CRITERE_DIFFERENCIATION,
        "intitule": "Différenciation crédible",
        "question": "Le produit dispose-t-il d'attributs distinctifs défendables ?",
        "sources_attendues": (ENTREE_CONCURRENCE,),
        "fonde_sur": "différenciation, angles peu exploités",
    },
    {
        "id": CRITERE_ADEQUATION,
        "intitule": "Adéquation aux besoins avérés",
        "question": (
            "Le produit répond-il à des pain points ou besoins documentés du corpus "
            "consommateur ?"
        ),
        "sources_attendues": (ENTREE_INSIGHTS,),
        "fonde_sur": "pain points, besoins ⨯ fiche produit",
    },
    {
        "id": CRITERE_VIABILITE_PRIX,
        "intitule": "Viabilité prix",
        "question": (
            "Un positionnement prix cohérent avec le benchmark observé est-il "
            "possible ?"
        ),
        "sources_attendues": (ENTREE_CONCURRENCE,),
        "fonde_sur": "benchmark par devise, position prix envisagé",
    },
)

IDS_CRITERES: tuple[str, ...] = tuple(c["id"] for c in GRILLE_CRITERES)

SCORES_POSSIBLES: frozenset[int] = frozenset({0, 1, 2})
SCORE_NON_EVALUABLE: str = "non_evaluable"

DEFINITION_SCORES: str = (
    "0 = signal défavorable net ; 1 = signal mitigé ou contrasté ; "
    "2 = signal favorable net"
)

# --------------------------------------------------------------------------- #
# RÈGLE DE VERDICT — HYPOTHÈSE DE TRAVAIL
# --------------------------------------------------------------------------- #
# Arbitrage CDC/SFG non tranché, à recalibrer sur cas réels. Toutes ces
# constantes sont volontairement conservatrices : en l'absence de règle validée,
# mieux vaut un « indeterminé » de trop qu'un « positif » infondé.

MIN_CRITERES_EVALUES: int = 4
"""En deçà de 4 critères évalués, le verdict est `indetermine` d'office."""

SEUIL_POSITIF: int = 6
"""Score total minimal pour un verdict positif, toutes conditions réunies."""

MAX_NON_EVALUABLES_POSITIF: int = 1
"""Au-delà d'un critère non évaluable, aucun verdict positif n'est possible."""

SEUIL_NEGATIF: int = 3
"""Score total à partir duquel (et en dessous) le verdict est négatif."""

PLAFOND_DEMANDE_SI_EFFET_DE_MODE: int = 1
"""Plafond appliqué PAR LE CODE au critère `demande` en cas d'effet de mode."""

VERDICT_POSITIF: str = "positif"
VERDICT_NEGATIF: str = "negatif"
VERDICT_INDETERMINE: str = "indetermine"

STATUT_REGLE: str = "hypothese_de_travail_a_valider"
"""Constante publiée dans chaque sortie : la règle n'est pas validée."""

MOTIF_PLAFONNEMENT_MODE: str = "effet_de_mode"

PROFIL_EFFET_DE_MODE: str = "effet_de_mode"
"""Valeur de `indicateurs.profil_courbe` déclenchant le plafonnement."""

# --------------------------------------------------------------------------- #
# Dossier de synthèse — bornes
# --------------------------------------------------------------------------- #

MAX_PAIN_POINTS_DOSSIER: int = 8
MAX_BESOINS_DOSSIER: int = 6
MAX_ANGLES_DOSSIER: int = 6
MAX_CONCURRENTS_DOSSIER: int = 8
MAX_ATTENTES_DOSSIER: int = 6
MAX_FCS_DOSSIER: int = 6
MAX_REQUETES_EMERGENTES_DOSSIER: int = 6
MAX_LIMITES_HERITEES: int = 12
"""Bornes du dossier de synthèse : c'est le SEUL contenu qui atteint les chaînes
LLM, donc le seul vocabulaire de citation possible."""

SEUIL_FRAICHEUR_JOURS: int = 30
"""Au-delà, une entrée horodatée est signalée comme potentiellement périmée."""

MAX_FAITS_CLES: int = 10
MIN_FAITS_CLES: int = 5
MAX_RECOMMANDATIONS_PAR_DOMAINE: int = 5
MAX_OPPORTUNITES: int = 6
MAX_RISQUES: int = 8

# --------------------------------------------------------------------------- #
# Vocabulaire
# --------------------------------------------------------------------------- #

TYPE_FAIT: str = "fait"
TYPE_HYPOTHESE: str = "hypothese"

DOMAINE_PRODUIT: str = "produit"
DOMAINE_PRIX: str = "prix"
DOMAINE_POSITIONNEMENT: str = "positionnement"
DOMAINE_MARKETING: str = "marketing"
DOMAINES: tuple[str, ...] = (
    DOMAINE_PRODUIT,
    DOMAINE_PRIX,
    DOMAINE_POSITIONNEMENT,
    DOMAINE_MARKETING,
)

PRIORITES: tuple[str, ...] = ("P1", "P2", "P3")
HORIZONS: tuple[str, ...] = ("immediat", "court_terme", "moyen_terme")
EFFORTS: tuple[str, ...] = ("faible", "moyen", "eleve")

TYPE_RISQUE_MARCHE: str = "marche"
TYPE_RISQUE_CONCURRENTIEL: str = "concurrentiel"
TYPE_RISQUE_PRODUIT: str = "produit"
TYPE_RISQUE_OPERATIONNEL: str = "operationnel"
TYPE_RISQUE_EFFET_DE_MODE: str = "effet_de_mode"
TYPE_RISQUE_DONNEES: str = "donnees"
TYPES_RISQUE: tuple[str, ...] = (
    TYPE_RISQUE_MARCHE,
    TYPE_RISQUE_CONCURRENTIEL,
    TYPE_RISQUE_PRODUIT,
    TYPE_RISQUE_OPERATIONNEL,
    TYPE_RISQUE_EFFET_DE_MODE,
    TYPE_RISQUE_DONNEES,
)
GRAVITES: tuple[str, ...] = ("faible", "moyenne", "elevee")

CONFIANCE_ELEVEE: str = "elevee"
CONFIANCE_MOYENNE: str = "moyenne"
CONFIANCE_FAIBLE: str = "faible"

# --------------------------------------------------------------------------- #
# Codes de sortie du CLI
# --------------------------------------------------------------------------- #

CODE_SUCCES: int = 0
CODE_ERREUR_IMPREVUE: int = 1
CODE_ENTREE_INEXPLOITABLE: int = 2

# --------------------------------------------------------------------------- #
# Limites et hypothèses systématiques
# --------------------------------------------------------------------------- #

LIMITES_SYSTEMATIQUES: tuple[str, ...] = (
    "La règle de verdict appliquée est une HYPOTHÈSE DE TRAVAIL, non un arbitrage "
    "validé : ni le cahier des charges ni la spécification fonctionnelle ne "
    "définissent le « potentiel commercial ». Ses seuils doivent être recalibrés "
    "sur des cas réels avant tout usage décisionnel.",
    "La qualité de ces recommandations est bornée par celle des analyses amont : "
    "un pain point mal caractérisé ou un benchmark biaisé se propage ici sans "
    "être détecté.",
    "Les recommandations reposent sur un corpus non exhaustif, hérité des biais de "
    "collecte des agents amont. Aucune part de marché, aucun volume de demande et "
    "aucune projection de vente n'en découlent.",
    "Aucune donnée financière interne (coûts d'achat, marges, frais logistiques, "
    "budget publicitaire) n'est disponible : les recommandations de prix sont des "
    "POSITIONNEMENTS DE MARCHÉ, jamais des calculs de rentabilité.",
)

HYPOTHESES_SYSTEMATIQUES: tuple[str, ...] = (
    f"Seuils de la règle de verdict : positif si score ≥ {SEUIL_POSITIF}, négatif "
    f"si score ≤ {SEUIL_NEGATIF}, minimum de {MIN_CRITERES_EVALUES} critères "
    f"évalués (heuristiques non validées).",
    f"Bornes du dossier de synthèse : {MAX_PAIN_POINTS_DOSSIER} pain points, "
    f"{MAX_BESOINS_DOSSIER} besoins, {MAX_ANGLES_DOSSIER} angles. Au-delà, les "
    f"éléments les moins prioritaires n'atteignent pas les chaînes d'analyse.",
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
