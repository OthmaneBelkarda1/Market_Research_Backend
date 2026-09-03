"""Constantes, chargement de l'environnement et configuration du logging.

Ce module centralise **toutes** les valeurs configurables du projet : aucune
valeur magique ne doit apparaître ailleurs dans le code.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from dotenv import find_dotenv, load_dotenv

# --------------------------------------------------------------------------- #
# Correctif d'encodage — appliqué au chargement du module.
# Sur Windows, la console utilise cp1252 par défaut : un mot-clé accentué écrit
# sur stdout/stderr y devient illisible (« Θcouteurs sport »), et un terme
# corrompu envoyé à Google Trends renvoie un dataset vide.
# --------------------------------------------------------------------------- #
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# --------------------------------------------------------------------------- #
# Environnement
# --------------------------------------------------------------------------- #
load_dotenv(find_dotenv(usecwd=True))

ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
# APIFY_API_TOKEN est accepté en repli : c'est le nom utilisé par la console Apify.
APIFY_TOKEN: str | None = os.getenv("APIFY_TOKEN") or os.getenv("APIFY_API_TOKEN")

# --------------------------------------------------------------------------- #
# Modèle LLM
# --------------------------------------------------------------------------- #
# Étapes LLM mécaniques et à sortie courte (contrôle qualité + dérivation de
# mot-clé) : le modèle le plus rapide de la gamme suffit.
MODELE_CLAUDE: str = "claude-haiku-4-5-20251001"
TEMPERATURE_LLM: float = 0.0
MAX_TOKENS_LLM: int = 1024

TARIFS_USD_PAR_MTOK: dict[str, tuple[float, float]] = {MODELE_CLAUDE: (1.00, 5.00)}
"""Tarif public (entrée, sortie) en dollars par million de jetons.

Saisi à la main, non interrogé en ligne : à revérifier à chaque migration de
modèle. Un identifiant absent de cette table est signalé par
`resumer_consommation`, jamais compté pour zéro en silence.
"""

# --------------------------------------------------------------------------- #
# Source de données Apify
# --------------------------------------------------------------------------- #
ACTOR_TENDANCES: str = "data_xplorer/google-trends-fast-scraper"
MODE_ACTOR: str = "keyword"

TIMEFRAME_12M: str = "today 12-m"
TIMEFRAME_5ANS: str = "today 5-y"

TIMEOUT_RUN_SECS: int = 600
"""Durée maximale d'un run Apify (le run observé dure 5 à 45 s)."""

PAUSE_ENTRE_APPELS_SECS: int = 20
"""Pause obligatoire entre deux appels : deux requêtes rapprochées depuis le
même pool de proxies déclenchent la détection anti-bot de Google."""

NB_TENTATIVES_MAX: int = 2
BACKOFF_TENTATIVES_SECS: tuple[int, ...] = (5, 20)
"""Attente avant la n-ième nouvelle tentative."""

GROUPES_PROXY: list[str] = ["RESIDENTIAL"]
"""Proxies résidentiels explicites : un repli silencieux sur des proxies
datacenter conduit à un blocage quasi immédiat par Google."""

CLE_TIMELINE_PARTIELLE: str = "isPartial"
"""Clé technique cohabitant avec la série dans `timeline_data`."""

# --------------------------------------------------------------------------- #
# Stratégie de repli de mot-clé
# --------------------------------------------------------------------------- #
SEUIL_INDICE_BRUIT: float = 5.0
"""En dessous de cet indice moyen sur 12 mois, la série est considérée comme
du bruit d'échantillonnage : un repli de mot-clé est tenté."""

NB_REPLIS_MAX: int = 2

# --------------------------------------------------------------------------- #
# Calcul des indicateurs
# --------------------------------------------------------------------------- #
FENETRE_MOMENTUM_JOURS: int = 90
MIN_JOURS_MOMENTUM: int = 180
"""Sous 180 jours d'historique, le momentum 90 jours n'est pas calculable."""

MIN_POINTS_SERIE: int = 4
"""Nombre minimal de points pour qu'une série soit exploitable."""

MIN_MOIS_REGRESSION: int = 12
"""Nombre minimal de mois pour ajuster une régression linéaire sur 5 ans."""

NB_ZONES_GEO: int = 5
SEUIL_BREAKOUT_PCT: float = 5000.0
LIBELLE_BREAKOUT: str = "breakout"

# --------------------------------------------------------------------------- #
# Seuils de classification de `profil_courbe`
#
# ⚠️ Toutes les valeurs ci-dessous sont des HEURISTIQUES NON VALIDÉES
# EMPIRIQUEMENT. Elles n'ont fait l'objet d'aucune calibration statistique sur
# un échantillon de référence et ne doivent pas être présentées comme un
# résultat de mesure. Elles sont ajustables sans modifier le reste du code.
# --------------------------------------------------------------------------- #
SEUIL_PENTE_POSITIVE: float = 2.0
"""Heuristique non validée : au-delà de +2 points d'indice par an, la courbe
est qualifiée de croissante."""

SEUIL_PENTE_NEUTRE: float = 1.0
"""Heuristique non validée : en deçà de ±1 point d'indice par an, la pente est
considérée comme plate."""

SEUIL_PENTE_NEGATIVE: float = -2.0
"""Heuristique non validée : en deçà de -2 points d'indice par an, la courbe
est qualifiée de déclinante."""

SEUIL_INDICE_MOYEN_FAIBLE: float = 20.0
"""Heuristique non validée : indice moyen 12 mois caractérisant un terme encore
peu recherché (candidat « émergent »)."""

SEUIL_INDICE_MOYEN_ELEVE: float = 40.0
"""Heuristique non validée : indice moyen 12 mois caractérisant une catégorie
installée (candidat « maturité »)."""

SEUIL_MOMENTUM_EMERGENT: float = 0.5
"""Heuristique non validée : +50 % entre les deux fenêtres de 90 jours."""

SEUIL_VOLATILITE_ELEVEE: float = 0.6
"""Heuristique non validée : coefficient de variation au-delà duquel la série
est considérée comme volatile."""

SEUIL_ANCIENNETE_PIC_MOIS: int = 12
"""Heuristique non validée : ancienneté du pic historique au-delà de laquelle
un effet de mode est suspecté."""

RATIO_EFFONDREMENT_MODE: float = 0.3
"""Heuristique non validée : indice actuel inférieur à 30 % du pic historique."""

PROFIL_EFFET_DE_MODE: str = "effet_de_mode"
PROFIL_EMERGENT: str = "emergent"
PROFIL_CROISSANCE: str = "croissance"
PROFIL_MATURITE: str = "maturite"
PROFIL_DECLIN: str = "declin"
PROFIL_INDETERMINE: str = "indetermine"

# --------------------------------------------------------------------------- #
# Limites méthodologiques injectées systématiquement dans le résultat
# --------------------------------------------------------------------------- #
LIMITES_METHODOLOGIQUES: list[str] = [
    "L'indice Google Trends est relatif (0-100) : il ne représente ni un volume "
    "de recherche, ni une taille de marché.",
    "Les valeurs sont normalisées par requête : deux exécutions distinctes ne "
    "sont pas comparables entre elles.",
    "Les données Google Trends sont échantillonnées : les résultats ne sont pas "
    "strictement reproductibles d'un appel à l'autre.",
    "Le classement `profil_courbe` est une heuristique, pas une méthode "
    "statistiquement validée.",
    "Aucun filtre de catégorie n'est disponible dans le mode `keyword` de "
    "l'actor : les homonymes du terme interrogé ne peuvent pas être écartés.",
    "L'absence de données de tendance ne constitue pas un signal négatif sur le "
    "potentiel du produit.",
]

LIMITE_REQUETES_EMERGENTES: str = (
    "L'actor data_xplorer/google-trends-fast-scraper ne renvoie ni requêtes "
    "associées ni sujets associés en mode `keyword` : `requetes_emergentes`, "
    "`sujets_associes` et `nb_breakout` sont donc toujours vides ou nuls, ce qui "
    "ne signifie pas qu'aucune requête émergente n'existe."
)

LIMITE_SERIE_HEBDOMADAIRE: str = (
    "L'horizon 5 ans est renvoyé au pas hebdomadaire : la saisonnalité et la "
    "pente annuelle sont calculées après agrégation mensuelle des semaines."
)

LIMITE_LANGUE_NON_PARAMETRABLE: str = (
    "L'actor ne prend pas de paramètre de langue : la langue d'interface est "
    "déduite du code pays. `ParametresMarche.langue` ne sert qu'à rédiger le "
    "mot-clé interrogé."
)

# --------------------------------------------------------------------------- #
# Logging — toujours vers stderr, jamais vers stdout (qui doit rester du JSON
# parsable).
# --------------------------------------------------------------------------- #
NOM_LOGGER: str = "agent_tendances"
FORMAT_LOG: str = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def configurer_logging(verbose: bool = False) -> logging.Logger:
    """Configure le logger applicatif vers `stderr`.

    Args:
        verbose: Si vrai, active le niveau INFO ; sinon seuls les avertissements
            et les erreurs sont émis.

    Returns:
        Le logger racine de l'application.
    """
    logger = logging.getLogger(NOM_LOGGER)
    logger.setLevel(logging.INFO if verbose else logging.WARNING)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(logging.Formatter(FORMAT_LOG))
        logger.addHandler(handler)
    return logger


def obtenir_logger(nom: str) -> logging.Logger:
    """Retourne un logger enfant du logger applicatif.

    Args:
        nom: Nom du module appelant.

    Returns:
        Le logger enfant correspondant.
    """
    return logging.getLogger(f"{NOM_LOGGER}.{nom}")


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
