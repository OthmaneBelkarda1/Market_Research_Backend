"""Constantes, chargement de l'environnement et configuration du logging.

Ce module centralise **toutes** les valeurs configurables du module : aucune
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
# « écouteurs »). Une requête corrompue envoyée à la SERP renvoie un résultat
# vide ou hors sujet — leçon d'un run précédent sur un autre collecteur.
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
# Les trois étapes LLM du module (contrôle qualité de la fiche, plan de
# requêtes, classification des pages par lots) sont mécaniques et à sortie
# courte : le modèle le plus rapide de la gamme suffit.
MODELE_CLAUDE: str = "claude-haiku-4-5-20251001"
TEMPERATURE_LLM: float = 0.0
MAX_TOKENS_LLM: int = 4096

TARIFS_USD_PAR_MTOK: dict[str, tuple[float, float]] = {MODELE_CLAUDE: (1.00, 5.00)}
"""Tarif public (entrée, sortie) en dollars par million de jetons.

Saisi à la main, non interrogé en ligne : à revérifier à chaque migration de
modèle. Un identifiant absent de cette table est signalé par
`resumer_consommation`, jamais compté pour zéro en silence.
"""

# --------------------------------------------------------------------------- #
# Source de données Apify
# --------------------------------------------------------------------------- #
ACTOR_RAG_WEB_BROWSER: str = "apify/rag-web-browser"
"""Actor de recherche + extraction de contenu.

⚠️ Le tag de build `latest` de cet actor pointe sur une version de 2024 (0.0.32)
dont le schéma d'entrée est INCOMPATIBLE avec celui utilisé ici : ni
`scrapingTool`, ni `serpProxyGroup`. Le build par défaut réellement exécuté est
`version-1` (1.0.24 au 01/08/2026), relevé sur les runs d'exploration. Ne pas
épingler `latest` en croyant obtenir la dernière version."""

SCRAPING_TOOL: str = "raw-http"
"""Outil d'extraction (`raw-http` ou `browser-playwright`).

`raw-http` retenu : les runs d'exploration ont produit un Markdown exploitable
sur toutes les pages éditoriales testées (8 000 à 68 000 caractères), pour un
coût et une latence bien moindres. `browser-playwright` reste l'alternative
documentée dans le README pour les sites entièrement rendus côté client."""

MAX_RESULTS_PAR_REQUETE: int = 3
"""Champ `maxResults` : nombre de résultats Google traités par run."""

REQUEST_TIMEOUT_SECS: int = 60
"""Champ `requestTimeoutSecs` : délai maximal de chargement d'une page cible."""

TIMEOUT_RUN_SECS: int = 300
"""Durée maximale d'un run Apify. Runs d'exploration mesurés à 11–15 s ; une
requête sans résultat organique est montée à 68 s (retries sur pages en échec)."""

NB_TENTATIVES_MAX: int = 2
BACKOFF_TENTATIVES_SECS: tuple[int, ...] = (5, 20)
"""Attente avant la n-ième nouvelle tentative."""

PARALLELISME_MAX: int = 3
"""Nombre maximal de runs Apify simultanés.

Contrairement à Google Trends — où les sessions se font bloquer par l'anti-bot
dès qu'elles se chevauchent — chaque run passe ici par l'infrastructure SERP
gérée d'Apify (`serpProxyGroup=GOOGLE_SERP`, valeur par défaut conservée) : un
parallélisme modéré est sans risque de blocage. La valeur 1 doit rester
utilisable pour revenir à une exécution strictement séquentielle."""

MARGE_ATTENTE_RUN_SECS: int = 60
"""Marge d'attente côté client au-delà du timeout du run lui-même."""

# --------------------------------------------------------------------------- #
# Quotas du plan de requêtes
# --------------------------------------------------------------------------- #
NB_REQUETES_PAR_AXE: int = 4
"""Requêtes par axe d'analyse, réparties en quotas égaux entre les deux modes
de ciblage régional : 2 en `tld` et 2 en `geo_keywords`."""

NB_REQUETES_OUVERTES: int = 2
"""Requêtes en langue du marché sans aucun ciblage régional. Filet de sécurité :
si le ciblage régional ne remonte rien, ces requêtes fournissent au moins un
corpus de repli, et leur échec signale un problème de requête ou de proxy."""

NB_REQUETES_REPLI: int = 2
"""Requêtes générées pour un axe sous-couvert, sur un seul cycle. Réparties en
1 `tld` + 1 `geo_keywords`."""

NB_MODES_CIBLAGE_REGIONAL: int = 2
"""`tld` et `geo_keywords` — sert au calcul des quotas par mode."""

# --------------------------------------------------------------------------- #
# Ciblage régional
# --------------------------------------------------------------------------- #
TLD_EXCEPTIONS: dict[str, str] = {"GB": "uk"}
"""Codes pays dont le TLD national diffère du code ISO-2 en minuscules.

Mapping volontairement simpliste : hors exception, TLD = code ISO-2 minuscule
(FR → .fr, MA → .ma). Il ne couvre ni les TLD de second niveau (.co.uk, .com.br)
ni les marchés dont l'audience se concentre en .com. À enrichir au besoin."""

OPERATEUR_SITE: str = "site:"
"""Opérateur Google de restriction de domaine, vérifié en sortie de la chaîne
de génération du plan."""

# --------------------------------------------------------------------------- #
# Hygiène du corpus
# --------------------------------------------------------------------------- #
DOMAINES_EXCLUS: list[str] = [
    "amazon.",
    "aliexpress.",
    "reddit.com",
    "facebook.com",
    "instagram.com",
    "pinterest.",
    "youtube.com",
]
"""Domaines déjà couverts par les collecteurs dédiés du projet (Amazon,
AliExpress, Reddit) ou sans contenu textuel exploitable (réseaux sociaux,
vidéo). Les exclure évite le double comptage entre sources et empêche une fiche
produit marchande de passer pour un article éditorial.

Constaté à l'exploration : une requête `site:.fr` a bien remonté une fiche
amazon.fr en position 2 — l'exclusion n'est pas théorique."""

PARAMETRES_URL_IGNORES: frozenset[str] = frozenset(
    {
        "srsltid",
        "gclid",
        "gbraid",
        "wbraid",
        "fbclid",
        "gad_source",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "mc_cid",
        "mc_eid",
    }
)
"""Paramètres d'URL purement publicitaires, retirés de la clé de dédoublonnage.

Sans ce retrait, le dédoublonnage est inopérant sur une partie des résultats :
Google appose un `srsltid` DIFFÉRENT à chaque clic. Constaté sur un run réel —
le même article d'un même domaine a été retenu TROIS fois, trois URLs distinctes
au seul `srsltid` près, ce qui triple son poids dans le corpus livré.

Seuls ces paramètres sont retirés : les autres (`?p=123`, `?page=2`) identifient
souvent la ressource et ne peuvent pas être supprimés sans confondre des pages
différentes. L'URL stockée dans `PageWeb.url` reste celle de la collecte, non
altérée."""

MIN_CARACTERES_PAGE: int = 500
"""En deçà, la page est écartée : ni contenu éditorial exploitable, ni
extraction réussie. Couvre aussi le cas `markdown=None`, observé sur les pages
dont le crawl échoue (HTTP 500) mais que l'actor émet quand même."""

MAX_CARACTERES_PAR_PAGE: int = 20_000
"""Troncature du Markdown conservé. Les pages observées vont de 8 000 à 178 000
caractères, l'essentiel du volume étant de la navigation et du pied de page."""

SEUIL_PERTINENCE: float = 0.5
"""Score minimal de pertinence pour figurer dans le corpus final.

Heuristique NON validée empiriquement sur ce module : aucune mesure de
précision ni de rappel sur un échantillon annoté. Ajustable sans modifier le
reste du code."""

SEUIL_MIN_PAGES_PAR_AXE: int = 3
"""Nombre de pages en deçà duquel un axe est jugé sous-couvert et déclenche un
cycle de requêtes de repli. Heuristique non validée : trois pages ne garantissent
en rien la représentativité d'un axe, c'est un plancher de non-vacuité."""

TAILLE_LOT_CLASSIFICATION: int = 10
"""Pages soumises par appel LLM de classification."""

LONGUEUR_EXTRAIT_CLASSIFICATION: int = 1_500
"""Caractères de Markdown transmis au classifieur : le titre, l'URL et le début
de page portent l'essentiel du signal de typage."""

# --------------------------------------------------------------------------- #
# Schéma de sortie réel de l'actor — constaté sur les runs d'exploration du
# 01/08/2026, et non déduit de la documentation. Relevé complet dans le README.
#
# Item = {crawl, markdown, metadata, query, searchResult}
# --------------------------------------------------------------------------- #
CLE_MARKDOWN: str = "markdown"
"""Contenu de la page. Peut valoir `None` lorsque le crawl a échoué."""

CLE_METADATA: str = "metadata"
CLE_META_URL: str = "url"
CLE_META_URL_REDIRIGEE: str = "redirectedUrl"
CLE_META_TITRE: str = "title"
CLE_META_LANGUE: str = "languageCode"

CLE_SEARCH_RESULT: str = "searchResult"
CLE_SERP_URL: str = "url"
CLE_SERP_TITRE: str = "title"
CLE_SERP_RANG: str = "rank"
CLE_SERP_TYPE: str = "resultType"

CLE_CRAWL: str = "crawl"
CLE_CRAWL_STATUT_HTTP: str = "httpStatusCode"

TYPE_RESULTAT_ORGANIQUE: str = "ORGANIC"
TYPE_RESULTAT_SUGGERE: str = "SUGGESTED"
"""Valeur observée quand Google n'a AUCUN résultat organique pour la requête :
il renvoie alors des pages de substitution, souvent hors marché et hors langue.
Un run de contrôle sur une requête volontairement introuvable a ainsi renvoyé
trois pages `SUGGESTED` en anglais, allemand et croate."""

# --------------------------------------------------------------------------- #
# Libellés du modèle de sortie
# --------------------------------------------------------------------------- #
AXE_CONSOMMATEURS: str = "axe1"
AXE_CONCURRENCE: str = "axe2"
AXE_MIXTE: str = "mixte"
AXES_ANALYSE: tuple[str, ...] = (AXE_CONSOMMATEURS, AXE_CONCURRENCE)

LIBELLES_AXES: dict[str, str] = {
    AXE_CONSOMMATEURS: "consommateurs (tests, avis éditoriaux, problèmes rapportés)",
    AXE_CONCURRENCE: "concurrence (comparatifs, marques, positionnement)",
}

CIBLAGE_TLD: str = "tld"
CIBLAGE_GEO_KEYWORDS: str = "geo_keywords"
CIBLAGE_OUVERT: str = "ouverte"
CIBLAGES_REGIONAUX: tuple[str, ...] = (CIBLAGE_TLD, CIBLAGE_GEO_KEYWORDS)

TYPES_SOURCE: tuple[str, ...] = (
    "comparatif",
    "test_avis",
    "article_presse",
    "blog",
    "site_marque",
    "site_marchand",
    "forum",
    "autre",
)

PREFIXE_WWW: str = "www."

# --------------------------------------------------------------------------- #
# Limites méthodologiques injectées systématiquement dans le résultat
# --------------------------------------------------------------------------- #
LIMITES_METHODOLOGIQUES: list[str] = [
    "La SERP interrogée est géolocalisée aux États-Unis et en anglais : l'actor "
    "n'expose aucun paramètre de pays ni de langue de recherche "
    "(`serpProxyGroup` ne sert pas à choisir un pays). Le ciblage régional par "
    "TLD et par mots-clés géographiques est donc une APPROXIMATION — le "
    "classement des résultats reste celui que verrait un utilisateur américain, "
    "pas un consommateur du marché étudié.",
    "L'opérateur `site:.<tld>` exclut mécaniquement les acteurs locaux hébergés "
    "en .com, qui sont nombreux sur la plupart des marchés. Les requêtes à "
    "mots-clés géographiques compensent partiellement ce biais, sans aucune "
    "garantie d'exhaustivité.",
    "Le corpus se limite aux premiers résultats Google de chaque requête : il "
    "n'est pas exhaustif et reflète les biais du référencement (optimisation "
    "SEO, contenu affilié, pages générées). Chaque page est un SIGNAL À "
    "RECOUPER, jamais un fait ; les chiffres qui y figurent (prix, parts de "
    "marché, classements) doivent être revalidés sur des sources structurées.",
    "`type_source`, `portee_regionale` et `pertinence` sont des heuristiques "
    "produites par un LLM, sans validation sur un échantillon annoté.",
    "Une redondance avec les corpus des autres collecteurs reste possible "
    "malgré l'exclusion de domaines : un même contenu peut être republié sur un "
    "domaine non listé.",
    "La fraîcheur des contenus n'est pas garantie : ce mode de collecte "
    "n'expose aucun filtre de date fiable, et les pages retenues peuvent dater "
    "de plusieurs années.",
]

LIMITE_RESULTATS_SUGGERES: str = (
    "Une partie des pages provient de résultats de type « SUGGESTED » : Google "
    "n'avait aucun résultat organique pour la requête et a renvoyé des pages de "
    "substitution, fréquemment hors marché et hors langue. Ces pages sont "
    "signalées par `type_resultat_serp` et l'absence de résultat organique est "
    "en soi une information sur la requête."
)

LIMITE_CORPUS_NON_CLASSIFIE: str = (
    "Corpus non classifié : tous les lots de classification LLM ont échoué. Les "
    "pages sont conservées avec `type_source=None`, `portee_regionale=None` et "
    "`pertinence=None`, sans avoir été confrontées au seuil de pertinence. "
    "`axes_servis` reprend alors l'axe de la requête d'origine — les deux axes "
    "pour une requête ouverte, dont l'axe réel est inconnu."
)

LIMITE_CORPUS_PARTIELLEMENT_CLASSIFIE: str = (
    "Corpus partiellement classifié : une partie des lots de classification LLM "
    "a échoué. Les pages concernées sont conservées avec des champs à `None` et "
    "n'ont pas été confrontées au seuil de pertinence."
)

LIMITE_COLLECTE_PARTIELLE: str = (
    "Collecte partielle : une ou plusieurs requêtes ont échoué, le corpus est "
    "incomplet (voir `statuts_collecte`)."
)

LIMITE_AUCUNE_DONNEE: str = (
    "Aucune donnée collectée : l'intégralité des requêtes a échoué (voir "
    "`statuts_collecte`). Ce résultat ne dit rien du marché étudié."
)

LIMITE_PLAN_INCOMPLET: str = (
    "Le plan de requêtes n'a pas atteint les quotas visés pour tous les couples "
    "axe/ciblage : certaines requêtes proposées étaient non conformes et ont été "
    "écartées. La couverture régionale est d'autant plus partielle."
)

LIMITE_AXES_SOUS_COUVERTS: str = (
    "Un ou plusieurs axes restent sous le seuil de pages après le cycle de "
    "repli (voir `stats.axes_sous_couverts`) : le corpus est insuffisant pour "
    "ces axes et ne doit pas être interprété comme un signal d'absence."
)

LIMITE_TLD_PEU_FOURNI: str = (
    "Une ou plusieurs requêtes `site:.<tld>` n'ont renvoyé aucune page "
    "exploitable. C'est une information légitime sur le marché — un TLD "
    "national peu doté en contenu éditorial — et non un échec de collecte."
)

# --------------------------------------------------------------------------- #
# Hypothèses injectées systématiquement dans le résultat
# --------------------------------------------------------------------------- #
HYPOTHESE_ASSIMILATION_REQUETES: str = (
    "Le produit est assimilé aux requêtes retenues : les pages collectées "
    "portent sur la catégorie de besoin visée, pas nécessairement sur la "
    "référence produit exacte."
)

HYPOTHESE_MAPPING_TLD: str = (
    "Mapping geo → TLD appliqué : code ISO-2 en minuscules, hors exceptions "
    "déclarées. Ce mapping suppose que le contenu pertinent d'un marché est "
    "majoritairement publié sous son TLD national."
)

HYPOTHESE_SEUILS: str = (
    "Seuils appliqués : pertinence ≥ {seuil_pertinence} pour retenir une page, "
    "{seuil_pages} page(s) minimum par axe avant déclenchement du repli. Ces "
    "valeurs sont des heuristiques non validées empiriquement."
)

# --------------------------------------------------------------------------- #
# Logging — toujours vers stderr, jamais vers stdout (qui doit rester du JSON
# parsable).
# --------------------------------------------------------------------------- #
NOM_LOGGER: str = "agent_recherche_web"
FORMAT_LOG: str = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def configurer_logging(verbose: bool = False) -> logging.Logger:
    """Configure le logger applicatif vers `stderr`.

    Args:
        verbose: Si vrai, active le niveau INFO ; sinon seuls les
            avertissements et les erreurs sont émis.

    Returns:
        Le logger racine du module.
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
