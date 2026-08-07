"""Constantes, seuils et plomberie LLM de l'agent Insights Consommateurs.

Aucune valeur magique ne doit exister ailleurs que dans ce module. Les seuils
sont des **heuristiques non validées empiriquement** : ils sont commentés un à
un et destinés à être recalibrés sur des cas réels.

Ce module ne dépend d'aucun autre module interne.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel

# --------------------------------------------------------------------------- #
# Encodage — correctif obligatoire
# --------------------------------------------------------------------------- #
# Sous Windows, la console est en cp1252 : sans ce correctif, tout accent écrit
# sur stdout/stderr casse la sortie JSON ou lève une UnicodeEncodeError.
for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # flux redirigé sans reconfigure
        pass

load_dotenv()

# --------------------------------------------------------------------------- #
# Journalisation — stderr exclusivement (stdout est réservé au JSON)
# --------------------------------------------------------------------------- #

FORMAT_LOG: str = "%(asctime)s [%(levelname)s] %(name)s : %(message)s"
NOM_LOGGER: str = "agent_insights_consommateurs"


def configurer_logs(verbeux: bool) -> logging.Logger:
    """Configure la journalisation vers `stderr` et retourne le logger racine.

    Args:
        verbeux: Si vrai, niveau DEBUG ; sinon WARNING.

    Returns:
        Le logger du module, prêt à l'emploi.
    """
    gestionnaire = logging.StreamHandler(stream=sys.stderr)
    gestionnaire.setFormatter(logging.Formatter(FORMAT_LOG))
    logger = logging.getLogger(NOM_LOGGER)
    logger.handlers.clear()
    logger.addHandler(gestionnaire)
    logger.setLevel(logging.DEBUG if verbeux else logging.WARNING)
    logger.propagate = False
    return logger


logger = logging.getLogger(NOM_LOGGER)

# --------------------------------------------------------------------------- #
# Modèles LLM — deux niveaux, température nulle
# --------------------------------------------------------------------------- #
# Vérifié au moment du développement (05/08/2026) : les deux identifiants sont
# disponibles à l'API Anthropic. `claude-sonnet-4-5-20250929` est un modèle
# « legacy actif » — la génération courante est `claude-sonnet-5`, mais celle-ci
# rejette toute valeur de `temperature` non par défaut (erreur 400), ce qui est
# incompatible avec l'exigence de température 0 de la spécification. Le modèle
# retenu est donc conservé tel que spécifié ; ce choix est documenté au README.

MODELE_EXTRACTION: str = "claude-haiku-4-5-20251001"
"""Étapes mécaniques par lots : cartographie des unités, normalisation."""

MODELE_SYNTHESE: str = "claude-sonnet-4-5-20250929"
"""Hiérarchisation, rédaction, lecture critique."""

TEMPERATURE: float = 0.0
MAX_TOKENS_EXTRACTION: int = 8000
MAX_TOKENS_SYNTHESE: int = 16000

NOM_VARIABLE_CLE_API: str = "ANTHROPIC_API_KEY"

TARIFS_USD_PAR_MTOK: dict[str, tuple[float, float]] = {
    MODELE_EXTRACTION: (1.00, 5.00),
    MODELE_SYNTHESE: (3.00, 15.00),
}
"""Tarifs publics (entrée, sortie) en dollars par million de jetons.

Servent uniquement à estimer le coût d'une exécution et à le journaliser. Ce
sont des valeurs de référence saisies à la main : elles ne sont pas interrogées
en ligne et peuvent avoir changé. À vérifier avant tout usage budgétaire.
"""

# --------------------------------------------------------------------------- #
# Seuils de constitution du corpus — heuristiques ajustables
# --------------------------------------------------------------------------- #

SEUIL_PERTINENCE_AMONT: float = 0.5
"""Unités dont le score de pertinence collecteur est inférieur sont écartées.

Une pertinence absente (`None`) est acceptée : l'absence de score n'est pas une
preuve de non-pertinence. Les commentaires Reddit héritent de la pertinence et
de la portée de leur post parent.
"""

MAX_UNITES_CORPUS: int = 400
"""Plafond d'unités courtes soumises à la cartographie LLM (coût et latence)."""

PART_MAX_PAR_SOURCE: float = 0.5
"""Part maximale d'une source dans le corpus échantillonné.

N'est appliquée que si d'autres sources sont disponibles : une source unique
occupe légitimement 100 % du corpus.
"""

MAX_CARACTERES_UNITE: int = 1200
"""Troncature d'une unité courte (post, commentaire, avis)."""

MAX_DOCUMENTS_WEB: int = 20
MAX_CARACTERES_DOCUMENT: int = 6000
"""Les pages web sont traitées comme des documents, non comme des unités."""

LONGUEUR_MIN_TEXTE: int = 15
"""En deçà, un texte ne porte aucune opinion exploitable."""

MIN_UNITES_ECRITURE: int = 3
"""Nombre d'unités minimal avant de signaler une écriture non latine.

Sans ce plancher, un unique symbole isolé (µ, Ω, un emoji translittéré) suffirait
à faire état d'une écriture absente du corpus.
"""

# --------------------------------------------------------------------------- #
# Lots LLM et plafonds de sortie
# --------------------------------------------------------------------------- #

TAILLE_LOT_UNITES: int = 15
TAILLE_LOT_DOCUMENTS: int = 4

MAX_THEMES: int = 12
MAX_PAIN_POINTS: int = 15
"""Plafonds appliqués après normalisation des libellés."""

MAX_VERBATIMS_PAR_PAIN_POINT: int = 3
MAX_CARACTERES_EXTRAIT: int = 300

NB_TENTATIVES_LLM: int = 2
"""1 appel + 1 re-prompt portant le message d'erreur de validation, puis dégradation."""

# --------------------------------------------------------------------------- #
# Seuils d'interprétation
# --------------------------------------------------------------------------- #

SEUIL_MIN_UNITES_FIABLE: int = 30
"""En deçà, `confiance_globale` est plafonnée à « faible »."""

SEUIL_CONFIANCE_ELEVEE_NB: int = 10
SEUIL_CONFIANCE_MOYENNE_NB: int = 4
"""Nombre d'unités distinctes requis pour qualifier la confiance d'un insight.

Un insight n'est dit de confiance élevée qu'à partir de
`SEUIL_CONFIANCE_ELEVEE_NB` unités **et** d'au moins deux sources.
"""

MAX_ELEMENTS_COMPORTEMENT: int = 8
"""Plafond par famille de signaux d'achat (critères, freins, déclencheurs…)."""

MAX_BESOINS: int = 10
MAX_ATTENTES: int = 8
MAX_SIGNAUX_POSITIFS: int = 8
"""Plafonds de rédaction, appliqués après la synthèse."""

SEUIL_PORTEE_DOMINANTE: float = 0.70
"""Un insight est dit régional ou global si ≥ 70 % de ses unités le sont."""

COEFFICIENT_MULTI_SOURCE: float = 0.25
"""Bonus par source supplémentaire dans le score de priorité (voir ci-dessous)."""

FORMULE_SCORE_PRIORITE: str = (
    "score_priorite = frequence_pct × intensite_moyenne "
    "× (1 + 0.25 × (nb_sources − 1))"
)
"""Énoncé littéral de la formule, recopié dans les hypothèses de la sortie.

HYPOTHÈSE DE TRAVAIL : pondération arbitraire, à recalibrer sur cas réels.
"""

# --------------------------------------------------------------------------- #
# Vocabulaire — valeurs constatées dans les contrats amont
# --------------------------------------------------------------------------- #

SOURCE_REDDIT: str = "reddit"
SOURCE_AMAZON: str = "amazon"
SOURCE_WEB: str = "recherche_web"
SOURCES: tuple[str, ...] = (SOURCE_REDDIT, SOURCE_AMAZON, SOURCE_WEB)

UNITE_POST: str = "reddit_post"
UNITE_COMMENTAIRE: str = "reddit_commentaire"
UNITE_AVIS: str = "amazon_avis"

SOURCE_PAR_TYPE_UNITE: dict[str, str] = {
    UNITE_POST: SOURCE_REDDIT,
    UNITE_COMMENTAIRE: SOURCE_REDDIT,
    UNITE_AVIS: SOURCE_AMAZON,
}

PORTEE_REGIONALE: str = "regionale"
PORTEE_GLOBALE: str = "globale"
PORTEE_MIXTE: str = "mixte"
PORTEE_INCONNUE: str = "inconnue"

SENTIMENT_POSITIF: str = "positif"
SENTIMENT_NEGATIF: str = "negatif"
SENTIMENT_NEUTRE: str = "neutre"
SENTIMENT_MIXTE: str = "mixte"
SENTIMENT_NON_APPLICABLE: str = "non_applicable"
SENTIMENTS_APPLICABLES: tuple[str, ...] = (
    SENTIMENT_POSITIF,
    SENTIMENT_NEGATIF,
    SENTIMENT_NEUTRE,
    SENTIMENT_MIXTE,
)

CONFIANCE_ELEVEE: str = "elevee"
CONFIANCE_MOYENNE: str = "moyenne"
CONFIANCE_FAIBLE: str = "faible"

FAMILLE_THEMES_LIBELLE: str = "thèmes"
FAMILLE_PAIN_POINTS_LIBELLE: str = "pain points"
"""Libellés de familles injectés dans le prompt de normalisation."""

AXE_INSIGHTS: str = "axe1"
"""Valeur attendue dans `pages[].axes_servis` pour qu'une page nous concerne."""

INTENSITE_MIN: int = 1
INTENSITE_MAX: int = 3

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
    "Le corpus analysé n'est pas exhaustif et reflète les biais de collecte des "
    "agents amont (moteurs de recherche, plafonds de collecte, seuils de "
    "pertinence). Il ne constitue en aucun cas un échantillon représentatif de "
    "la population d'un marché.",
    "La classification des sentiments, thèmes et pain points est produite par un "
    "modèle de langage et n'a pas été validée empiriquement contre un codage "
    "humain.",
    "Les populations diffèrent fortement d'une source à l'autre (contributeurs "
    "Reddit, acheteurs Amazon, rédacteurs web) : les agrégats inter-sources "
    "mélangent des publics qui ne sont pas comparables.",
    "Aucune inférence sur la taille du marché ne peut être tirée de ces volumes : "
    "un nombre d'unités mesure l'activité de discussion collectée, pas la demande.",
)

HYPOTHESES_SYSTEMATIQUES: tuple[str, ...] = (
    f"Seuil de pertinence amont retenu : {SEUIL_PERTINENCE_AMONT} (heuristique).",
    f"Formule de priorité des pain points : {FORMULE_SCORE_PRIORITE} (heuristique).",
    f"Un insight est qualifié de régional ou global à partir de "
    f"{int(SEUIL_PORTEE_DOMINANTE * 100)} % d'unités d'une même portée, sinon « mixte ».",
    "Les commentaires Reddit héritent de la pertinence et de la portée de leur "
    "post parent ; un commentaire orphelin est de portée inconnue.",
    "Le produit et le marché retenus pour la sortie sont ceux du premier fichier "
    "valide, dans l'ordre Reddit → Amazon → Recherche web.",
)

# --------------------------------------------------------------------------- #
# Plomberie LLM partagée
# --------------------------------------------------------------------------- #
# Placée ici plutôt que dans un module dédié : le sens des dépendances impose
# que `carte` et `synthese` ne s'importent pas mutuellement, et `config` est le
# seul point commun autorisé. Aucune dépendance interne n'est introduite.

TypeSortie = TypeVar("TypeSortie", bound=BaseModel)


def resumer_consommation(usage: dict[str, Any]) -> str:
    """Résume la consommation de jetons d'une exécution et son coût estimé.

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


def construire_modele(nom_modele: str, max_tokens: int) -> Any:
    """Instancie un modèle de discussion Anthropic à température nulle.

    Args:
        nom_modele: Identifiant du modèle, issu de ce module.
        max_tokens: Plafond de jetons produits.

    Returns:
        Une instance de `ChatAnthropic`.
    """
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=nom_modele,
        temperature=TEMPERATURE,
        max_tokens=max_tokens,
        timeout=None,
        stop=None,
    )


def invoquer_structure(
    chaine: Any,
    entree: dict[str, Any],
    libelle: str,
) -> tuple[Any | None, int, str | None]:
    """Invoque une chaîne LCEL à sortie structurée, avec une reprise sur échec.

    La reprise consiste à relancer la même chaîne en lui adjoignant le message
    d'erreur rencontré, conformément à `NB_TENTATIVES_LLM`.

    Args:
        chaine: Chaîne LCEL déjà dotée de `with_structured_output`.
        entree: Variables du gabarit de prompt. La clé `erreur_precedente` est
            réservée : elle est injectée par cette fonction.
        libelle: Libellé de l'étape, pour la journalisation.

    Returns:
        Un triplet `(resultat, nb_tentatives, message_erreur)`. `resultat` vaut
        `None` si toutes les tentatives ont échoué.
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
        except Exception as erreur:  # noqa: BLE001 — toute erreur doit dégrader, pas casser
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
            logger.warning("%s — tentative %d : sortie vide", libelle, tentative)
            continue
        logger.debug("%s — succès en %d tentative(s)", libelle, tentative)
        return resultat, tentative, None
    return None, NB_TENTATIVES_LLM, derniere_erreur
