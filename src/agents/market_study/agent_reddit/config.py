"""Constantes, chargement de l'environnement et configuration du logging.

Ce module centralise **toutes** les valeurs configurables du projet : aucune
valeur magique ne doit apparaître ailleurs dans le code, y compris les noms de
champs du schéma de sortie de l'actor Apify.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from dotenv import find_dotenv, load_dotenv

# --------------------------------------------------------------------------- #
# Correctif d'encodage — appliqué au chargement du module.
# Sur Windows, la console utilise cp1252 par défaut : une requête accentuée
# écrite sur stdout/stderr y devient illisible (« Θcouteurs » au lieu de
# « écouteurs »), et un terme corrompu envoyé à la recherche Reddit renvoie un
# résultat vide ou hors sujet.
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

SEL_ANONYMISATION_DEFAUT: str = "agent_reddit_sel_par_defaut"
"""Sel de repli si `SEL_ANONYMISATION` est absent de l'environnement.

Un sel public affaiblit le hachage : le pseudonyme redevient réversible par
force brute sur le dictionnaire des pseudos Reddit. Le repli existe uniquement
pour ne jamais bloquer une exécution ; un avertissement est émis à l'usage.
"""

SEL_ANONYMISATION: str = os.getenv("SEL_ANONYMISATION") or SEL_ANONYMISATION_DEFAUT
SEL_ANONYMISATION_FOURNI: bool = bool(os.getenv("SEL_ANONYMISATION"))

# --------------------------------------------------------------------------- #
# Modèle LLM
# --------------------------------------------------------------------------- #
# Étapes LLM mécaniques et à sortie courte (contrôle qualité, stratégie de
# recherche, scoring de pertinence par lots) : le modèle le plus rapide de la
# gamme suffit.
MODELE_CLAUDE: str = "claude-haiku-4-5-20251001"
TEMPERATURE_LLM: float = 0.0
MAX_TOKENS_LLM: int = 2048

TARIFS_USD_PAR_MTOK: dict[str, tuple[float, float]] = {MODELE_CLAUDE: (1.00, 5.00)}
"""Tarif public (entrée, sortie) en dollars par million de jetons.

Saisi à la main, non interrogé en ligne : à revérifier à chaque migration de
modèle. Un identifiant absent de cette table est signalé par
`resumer_consommation`, jamais compté pour zéro en silence.
"""

# --------------------------------------------------------------------------- #
# Source de données Apify
# --------------------------------------------------------------------------- #
ACTOR_REDDIT: str = "harshmaur/reddit-scraper"

TIMEOUT_RUN_SECS: int = 600
"""Durée maximale d'un run Apify (les runs observés durent 20 s à 3 min)."""

NB_TENTATIVES_MAX: int = 2
BACKOFF_TENTATIVES_SECS: tuple[int, ...] = (5, 20)
"""Attente avant la n-ième nouvelle tentative."""

GROUPES_PROXY: list[str] = ["RESIDENTIAL"]
"""Proxies résidentiels explicites : Reddit bloque agressivement les plages
datacenter, un repli silencieux conduit à un dataset vide."""

FENETRE_RECHERCHE: str = "year"
"""Valeur du champ `searchTime` de l'actor (énumération : all/hour/day/week/
month/year)."""

TRI_RECHERCHE: str = "relevance"
"""Valeur du champ `searchSort`. `postedAfter` forcerait le tri `new` et
ignorerait `searchTime` : nous conservons relevance + fenêtre glissante."""

# --------------------------------------------------------------------------- #
# Plafonds de coût
#
# Tarification constatée de l'actor au 30/07/2026 (modèle PAY_PER_EVENT) :
#   - événement « init »   : 0,02 $ par Go de mémoire à chaque DÉMARRAGE de run
#                            (2 Go par défaut) — d'où l'exécution séquentielle
#                            d'un nombre borné de runs plutôt qu'un run par
#                            requête ;
#   - événement « result » : 0,0018 $ par item sauvegardé en tier Bronze
#                            (0,002 $ en tier gratuit), un post ET un
#                            commentaire comptant chacun pour un item.
# --------------------------------------------------------------------------- #

NB_MAX_REQUETES: int = 6
"""Requêtes marché + anglais confondues. Les requêtes d'un même run se
partagent le plafond `maxPostsCount` : au-delà, chaque requête supplémentaire
dilue la couverture sans coût direct additionnel."""

NB_MAX_SUBREDDITS_CIBLES: int = 3
"""`withinCommunity` n'accepte qu'un seul subreddit par run : chaque subreddit
ciblé coûte un démarrage de run supplémentaire (0,02 $)."""

NB_SUBREDDITS_REGIONAUX: int = 1
"""Subreddit(s) généraliste(s) du pays imposé(s) à la stratégie.

Sans ancrage régional explicite, le corpus est du Reddit anglophone mondial :
constaté sur un run `geo=US` où la chaîne n'avait proposé aucun subreddit
régional — les 56 posts retenus étaient tous classés `globale`. Sur un marché
anglophone, c'est le SEUL levier de régionalisation disponible, la règle fondée
sur la langue des requêtes étant alors inopérante.

Un seul subreddit : au-delà, les runs régionaux consommeraient les créneaux des
subreddits thématiques, qui portent l'essentiel du signal produit."""

NB_MAX_POSTS_RECHERCHE_GLOBALE: int = 100
"""Plafond du run de prospection globale : 100 items ≈ 0,18 $."""

NB_MAX_POSTS_PAR_SUBREDDIT: int = 30
"""Plafond par run restreint : 30 items ≈ 0,054 $, soit ≈ 0,16 $ pour 3 runs."""

NB_MIN_POSTS_PAR_REQUETE: int = 1
"""Plancher du plafond réparti entre les requêtes d'un même run.

ÉCART CONFIRMÉ ENTRE LA DOCUMENTATION DE L'ACTOR ET SON COMPORTEMENT RÉEL.
`maxPostsCount` est décrit comme un plafond global (« across all search
results ») mais l'actor l'applique comme un QUOTA PAR MOT-CLÉ, à la lettre.
Mesure de contrôle du 30/07/2026 : 4 requêtes à `maxPostsCount=10` ont renvoyé
exactement 10 items par requête, soit 40 au total. Sans répartition, un run à
`maxPostsCount=100` sur 6 requêtes produit ~571 items — 1,03 $ au lieu des
0,18 $ budgétés.

`reddit_source` répartit donc le plafond entre les requêtes avant l'envoi, de
sorte que les deux constantes ci-dessus conservent leur sémantique de COÛT
TOTAL DU RUN, quel que soit le nombre de requêtes dérivées par le LLM."""

NB_MAX_POSTS_APPROFONDIS: int = 15
"""Posts dont les commentaires sont collectés en phase B. Le run de phase B
re-sauvegarde aussi chaque post ciblé : le coût est donc
(15 posts + 15 × commentaires) items."""

NB_MAX_COMMENTAIRES_PAR_POST: int = 25
"""Au pire 15 × 25 = 375 commentaires ≈ 0,675 $ : premier poste de dépense du
module."""

NB_MAX_RUNS_PROSPECTION: int = 1 + NB_MAX_SUBREDDITS_CIBLES
"""Recherche globale + un run par subreddit cible."""

# --------------------------------------------------------------------------- #
# Filtrage de pertinence
# --------------------------------------------------------------------------- #
SEUIL_PERTINENCE: float = 0.5
"""Heuristique, mais CONTRÔLÉE sur un corpus réel (136 posts, genouillère
orthopédique, geo=US — voir README, « Calibration du seuil »).

La distribution des scores est fortement bimodale : 53 % des posts sous 0,3,
36 % au-dessus de 0,7, et seulement 6,6 % entre 0,30 et 0,49. La position exacte
du seuil a donc peu d'incidence — l'abaisser à 0,3 n'ajouterait que 9 posts, dont
un seul relevait d'un manque réel à la relecture manuelle.

Reste une heuristique : aucune mesure de précision ni de rappel sur un
échantillon annoté de référence. Ajustable sans modifier le reste du code."""

TAILLE_LOT_PERTINENCE: int = 20
"""Posts soumis par appel LLM de scoring."""

LONGUEUR_EXTRAIT_PERTINENCE: int = 200
"""Nombre de caractères de corps de post transmis au scoring : le titre porte
l'essentiel du signal, le corps ne sert qu'à lever les ambiguïtés."""

LONGUEUR_MIN_TITRE_EXPLOITABLE: int = 3
"""En deçà, le post est considéré comme non exploitable par le filtre
déterministe."""

# --------------------------------------------------------------------------- #
# Anonymisation RGPD
# --------------------------------------------------------------------------- #
LONGUEUR_HASH_PSEUDO: int = 16
"""Troncature du sha256 : 16 caractères hexadécimaux suffisent à distinguer
les auteurs d'un corpus de quelques centaines d'items."""

PSEUDO_ANONYME: str = "anonyme"
"""Valeur attribuée aux auteurs supprimés : ces marqueurs ne désignent aucune
personne, les hacher créerait de faux auteurs distincts."""

PSEUDOS_NON_NOMINATIFS: frozenset[str] = frozenset(
    {"[deleted]", "[removed]", "deleted", "removed", ""}
)

# --------------------------------------------------------------------------- #
# Schéma réel de l'actor — constaté sur les runs d'exploration du 30/07/2026,
# et non déduit de la documentation. Voir README pour le relevé complet.
# --------------------------------------------------------------------------- #
CLE_TYPE_ITEM: str = "dataType"
TYPE_ITEM_POST: str = "post"
TYPE_ITEM_COMMENTAIRE: str = "comment"

# Champs d'un item « post ».
CLE_POST_ID: str = "id"
CLE_POST_TITRE: str = "title"
CLE_POST_TEXTE: str = "body"
CLE_POST_SUBREDDIT: str = "communityName"
CLE_POST_URL: str = "postUrl"
CLE_POST_DATE: str = "createdAt"
CLE_POST_SCORE: str = "score"
CLE_POST_NB_COMMENTAIRES: str = "commentsCount"
CLE_POST_AUTEUR: str = "authorName"
CLE_POST_REQUETE: str = "searchTerm"
"""Requête ayant produit le post. Absent des items issus d'un run `startUrls`."""

# Champs d'un item « comment ».
CLE_COMMENTAIRE_ID: str = "id"
CLE_COMMENTAIRE_ID_POST: str = "postId"
CLE_COMMENTAIRE_TEXTE: str = "body"
CLE_COMMENTAIRE_DATE: str = "commentCreatedAt"
CLE_COMMENTAIRE_SCORE: str = "score"
CLE_COMMENTAIRE_PROFONDEUR: str = "depth"
CLE_COMMENTAIRE_AUTEUR: str = "authorName"

PREFIXE_SUBREDDIT: str = "r/"

# --------------------------------------------------------------------------- #
# Libellés du modèle de sortie
# --------------------------------------------------------------------------- #
PORTEE_REGIONALE: str = "regionale"
PORTEE_GLOBALE: str = "globale"

ORIGINE_RECHERCHE_GLOBALE: str = "recherche_globale"
ORIGINE_SUBREDDIT_CIBLE: str = "subreddit_cible"

PHASE_PROSPECTION_GLOBALE: str = "prospection_globale"
PHASE_PROSPECTION_SUBREDDIT: str = "prospection_subreddit"
PHASE_COMMENTAIRES: str = "commentaires"

LANGUE_ANGLAISE: str = "en"

# --------------------------------------------------------------------------- #
# Limites méthodologiques injectées systématiquement dans le résultat
# --------------------------------------------------------------------------- #
LIMITES_METHODOLOGIQUES: list[str] = [
    "Reddit n'est pas représentatif de la population d'un marché : la base "
    "d'utilisateurs est documentée comme jeune, majoritairement masculine, "
    "technophile et anglophone. Le corpus ne constitue en aucun cas un "
    "échantillon de consommateurs.",
    "Reddit n'offre aucun filtre géographique natif : la « régionalisation » "
    "repose uniquement sur le choix des subreddits et sur la langue des "
    "requêtes. C'est une approximation, à interpréter comme telle.",
    "La couverture varie fortement selon le pays : pour les marchés non "
    "anglophones, le volume de discussions peut être très faible. L'absence de "
    "discussions ne constitue pas un signal d'absence de marché.",
    "Le score de pertinence est une heuristique LLM non validée empiriquement "
    "et le seuil de rétention est arbitraire.",
    "La recherche Reddit est non exhaustive : toute liste de résultats est "
    "plafonnée à environ 1 000 posts côté Reddit, le mode rapide de l'actor "
    "(actif d'office sur les recherches par mots-clés) peut manquer des posts, "
    "et le tri par pertinence est opaque.",
    f"Les contenus collectés se limitent à la fenêtre de recherche configurée "
    f"(`searchTime={FENETRE_RECHERCHE}`) : les opinions exprimées ont pu "
    f"évoluer depuis.",
]

LIMITE_CORPUS_NON_FILTRE: str = (
    "Corpus non filtré par pertinence : le scoring LLM a échoué, tous les posts "
    "dédoublonnés sont conservés avec `pertinence=None`."
)

LIMITE_CORPUS_PARTIELLEMENT_FILTRE: str = (
    "Corpus partiellement filtré : une partie des lots de scoring LLM a échoué. "
    "Les posts concernés sont conservés avec `pertinence=None` sans avoir été "
    "confrontés au seuil."
)

LIMITE_SANS_COMMENTAIRES: str = (
    "Phase B non aboutie : le corpus ne contient que des posts, sans fil de "
    "commentaires. Les avis exprimés en réponse aux posts sont absents."
)

LIMITE_PROSPECTION_PARTIELLE: str = (
    "Prospection partielle : un ou plusieurs runs de collecte ont échoué, le "
    "corpus est incomplet (voir `statuts_collecte`)."
)

LIMITE_AUCUNE_DONNEE: str = (
    "Aucune donnée collectée : l'intégralité de la prospection a échoué (voir "
    "`statuts_collecte`). Ce résultat ne dit rien du marché étudié."
)

LIMITE_SANS_ANCRAGE_REGIONAL: str = (
    "Aucun subreddit régional n'a pu être ciblé : le corpus n'a aucun point "
    "d'ancrage géographique et doit être lu comme du Reddit mondial, sans "
    "rapport établi avec le marché étudié."
)

LIMITE_ANCRAGE_REGIONAL_FAIBLE: str = (
    "Le subreddit régional ciblé n'a fourni aucun post retenu : la dimension "
    "régionale du corpus est nulle. Cela signale une absence de discussion "
    "locale sur le sujet, et non une absence de marché."
)

LIMITE_POSTS_SANS_COMMENTAIRES_ECARTES: str = (
    "Les posts annonçant zéro commentaire sont écartés de la phase B : les "
    "interroger coûterait un item facturé pour un rendement nul."
)

# --------------------------------------------------------------------------- #
# Hypothèses injectées systématiquement dans le résultat
# --------------------------------------------------------------------------- #
HYPOTHESE_SUBREDDITS_NON_VERIFIES: str = (
    "Les subreddits cibles sont proposés par un LLM et ne sont pas vérifiés a "
    "priori : leur existence et leur activité ne sont constatées qu'à "
    "l'exécution. Un run restreint renvoyant zéro post est une information "
    "légitime, pas un échec."
)

HYPOTHESE_PORTEE: str = (
    "Attribution de `portee` : un post est dit « regionale » s'il provient d'un "
    "subreddit régional ciblé ou d'une requête rédigée dans la langue du marché "
    "(lorsque celle-ci n'est pas l'anglais) ; « globale » sinon. Règle "
    "d'approximation, aucune géolocalisation réelle n'est disponible."
)

HYPOTHESE_ASSIMILATION_REQUETES: str = (
    "Le produit est assimilé aux requêtes de recherche retenues : les "
    "discussions collectées portent sur la catégorie de besoin visée, pas "
    "nécessairement sur la référence produit exacte."
)

# --------------------------------------------------------------------------- #
# Logging — toujours vers stderr, jamais vers stdout (qui doit rester du JSON
# parsable).
# --------------------------------------------------------------------------- #
NOM_LOGGER: str = "agent_reddit"
FORMAT_LOG: str = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def configurer_logging(verbose: bool = False) -> logging.Logger:
    """Configure le logger applicatif vers `stderr`.

    Args:
        verbose: Si vrai, active le niveau INFO ; sinon seuls les
            avertissements et les erreurs sont émis.

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
