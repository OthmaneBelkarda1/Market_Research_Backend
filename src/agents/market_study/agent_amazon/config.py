"""Constantes, chargement de l'environnement et configuration du logging.

Ce module centralise **toutes** les valeurs configurables du module : aucune
valeur magique ne doit apparaître ailleurs dans le code, y compris les noms de
champs des schémas de sortie des actors Apify.
"""

from __future__ import annotations

import logging
import os
import sys

from dotenv import find_dotenv, load_dotenv

# --------------------------------------------------------------------------- #
# Correctif d'encodage — appliqué au chargement du module.
# Sur Windows, la console utilise cp1252 par défaut : un titre de produit
# accentué, un symbole € ou un « ★ » y provoque un UnicodeEncodeError et fait
# tomber l'exécution après que la collecte a été facturée.
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
# Les quatre étapes LLM du module (contrôle qualité de la fiche, résolution
# d'une région en texte libre, plan de recherches, classification des produits
# par lots) sont mécaniques et à sortie courte : le modèle le plus rapide de la
# gamme suffit.
MODELE_CLAUDE: str = "claude-haiku-4-5-20251001"
TEMPERATURE_LLM: float = 0.0
MAX_TOKENS_LLM: int = 4096

# --------------------------------------------------------------------------- #
# Sources de données Apify
# --------------------------------------------------------------------------- #
ACTOR_AMAZON_CRAWLER: str = "junglee/Amazon-crawler"
"""Actor « Amazon Product Scraper ».

Il crawle n'importe quelle URL de listing Amazon : page de résultats de
recherche, page de catégorie, page Best Sellers, ou fiche produit isolée. Ce
module ne lui envoie que des URLs de recherche qu'il construit lui-même.

⚠️ La casse du nom est celle de l'actor (« Amazon-crawler », pas
« amazon-crawler ») : c'est celle que le serveur MCP d'Apify expose et celle qui
figure dans la boutique."""

ACTOR_AMAZON_AVIS: str = "junglee/amazon-reviews-scraper"
"""Actor « Amazon Reviews Scraper », un run PAR produit (voir NB_PRODUITS_AVIS)."""

MAX_PRODUITS_PAR_RECHERCHE: int = 30
"""Champ `maxItemsPerStartUrl` : produits scrapés par recherche, donc par run.

Une page de résultats Amazon contient environ 48 produits : le plafond est
normalement atteint sur la première page."""

PRODUITS_PAR_PAGE_SERP: int = 24
"""Base de calcul de `maxSearchPagesPerStartUrl`.

Volontairement inférieur aux ~48 produits réels d'une page : Amazon bloque une
part non négligeable des requêtes et l'actor perd des produits en route. La
marge évite qu'une recherche étroite s'arrête sous le quota demandé."""

MIN_PAGES_SERP: int = 2
"""Plancher de pages de résultats à parcourir, quel que soit le quota demandé."""

TIMEOUT_RUN_SECS: int = 900
"""Durée maximale d'un run de collecte de produits.

`scrapeProductDetails=True` fait visiter une page produit par item : un run de
30 produits dure typiquement plusieurs minutes, bien plus qu'un run de SERP."""

TIMEOUT_RUN_AVIS_SECS: int = 300
"""Durée maximale d'un run de collecte d'avis, plafonné à 10 avis."""

MARGE_ATTENTE_RUN_SECS: int = 60
"""Marge d'attente côté client au-delà du timeout du run lui-même."""

NB_TENTATIVES_MAX: int = 2
BACKOFF_TENTATIVES_SECS: tuple[int, ...] = (20, 60)
"""Attente avant la n-ième nouvelle tentative.

Volontairement longue : un échec vient le plus souvent d'un blocage anti-bot
d'Amazon. Réessayer immédiatement réutilise la session proxy qui vient d'être
refusée."""

PAUSE_AVANT_REPLI_SECS: int = 20
"""Pause avant de relancer une recherche restée vide, sans ses filtres d'URL."""

PARALLELISME_MAX: int = 3
"""Nombre maximal de runs Apify simultanés.

Chaque run dispose de sa propre session proxy côté Apify : un parallélisme
modéré n'augmente pas le risque de blocage. La valeur 1 doit rester utilisable
pour revenir à une exécution strictement séquentielle."""

# --------------------------------------------------------------------------- #
# Avis clients
# --------------------------------------------------------------------------- #
NB_PRODUITS_AVIS: int = 5
"""Produits du corpus final enrichis d'avis. Un run d'actor PAR produit : ce
nombre est le principal levier de coût du module."""

NB_AVIS_PAR_PRODUIT: int = 10
"""Champ `maxReviews`. L'actor plafonne de toute façon un run aux alentours de
10 avis sur le plan gratuit — d'où un run par produit plutôt qu'un run groupé,
qui dépenserait toute l'allocation sur le premier produit."""

ANCIENNETE_MAX_AVIS: str = "2 years"
"""Champ `reviewsCutoffDate` : les avis plus anciens sont ignorés.

Une annonce ayant changé de main ou de qualité conserve ses anciens avis ; la
coupure dépense le petit budget d'avis sur ce qui est vrai aujourd'hui. Accepte
« N days|months|years » ou une date ISO."""

TRI_AVIS: str = "helpful"
"""Champ `sort` : équivalent des « Top reviews » d'Amazon."""

FILTRE_NOTES_AVIS: list[str] = ["allStars"]
"""Champ `filterByRatings` : toutes les notes, aucune sélection par étoiles."""

INCLURE_DONNEES_PERSONNELLES: bool = False
"""Champ `includeGdprSensitive`. Laissé à faux : le nom du relecteur est une
donnée personnelle dont ce module n'a aucun usage."""

# --------------------------------------------------------------------------- #
# Plan de recherches
# --------------------------------------------------------------------------- #
NB_RECHERCHES: int = 3
"""Recherches distinctes du plan, une par run.

Plusieurs angles de recherche valent mieux qu'un seul jeu de mots-clés : le
classement d'Amazon dépend fortement de la formulation. Chaque recherche
supplémentaire est cependant un run facturé."""

NB_RECHERCHES_REPLI: int = 1
"""Recherches de repli générées lorsque le corpus reste sous le seuil, sur un
seul cycle."""

SEUIL_MIN_PRODUITS: int = 5
"""Nombre de produits retenus en deçà duquel un cycle de repli est déclenché.

Heuristique non validée : c'est un plancher de non-vacuité, pas un seuil de
représentativité."""

# --------------------------------------------------------------------------- #
# Marketplaces Amazon
# --------------------------------------------------------------------------- #
MARKETPLACES: tuple[str, ...] = (
    "amazon.com",
    "amazon.ca",
    "amazon.com.mx",
    "amazon.com.br",
    "amazon.co.uk",
    "amazon.de",
    "amazon.fr",
    "amazon.es",
    "amazon.it",
    "amazon.nl",
    "amazon.com.be",
    "amazon.se",
    "amazon.pl",
    "amazon.com.tr",
    "amazon.eg",
    "amazon.sa",
    "amazon.ae",
    "amazon.co.za",
    "amazon.in",
    "amazon.co.jp",
    "amazon.sg",
    "amazon.com.au",
)
"""Toutes les marketplaces que ce module sait interroger."""

MARKETPLACE_PAR_PAYS: dict[str, str] = {
    # Europe
    "GB": "amazon.co.uk",
    "DE": "amazon.de",
    "FR": "amazon.fr",
    "ES": "amazon.es",
    "IT": "amazon.it",
    "NL": "amazon.nl",
    "BE": "amazon.com.be",  # ouvert en octobre 2022
    "SE": "amazon.se",
    "PL": "amazon.pl",
    "TR": "amazon.com.tr",
    # Afrique et Moyen-Orient
    "EG": "amazon.eg",
    "AE": "amazon.ae",
    "SA": "amazon.sa",
    "ZA": "amazon.co.za",  # ouvert en mai 2024
    # Asie-Pacifique
    "IN": "amazon.in",
    "JP": "amazon.co.jp",
    "SG": "amazon.sg",
    "AU": "amazon.com.au",
    # Amériques
    "US": "amazon.com",
    "CA": "amazon.ca",
    "MX": "amazon.com.mx",
    "BR": "amazon.com.br",
}
"""Pays disposant de leur PROPRE site Amazon, et ce site.

⚠️ Table EXHAUSTIVE et volontairement stricte : un pays qui n'y figure pas rend
l'agent INAPPLICABLE, et l'exécution s'arrête avant toute dépense (voir
`MOTIF_PAYS_SANS_MARKETPLACE`). Aucun repli sur « la marketplace la plus proche »
n'est fait : `amazon.fr` interrogé pour le Maroc décrit le marché français, pas
le marché marocain, et une étude bâtie dessus serait fausse sans le dire.

Un pays absent se rajoute ici en une ligne, à condition qu'Amazon y exploite
réellement un site.

⚠️ Relevé manuel, à jour au 03/08/2026, et NON vérifié contre les domaines que
l'actor `junglee/Amazon-crawler` sait effectivement crawler. Deux erreurs
possibles, de gravité opposée :
  • un pays manquant → refus injustifié, mais aucune dépense ;
  • un domaine erroné ou non supporté par l'actor → URLs mortes et runs
    facturés pour rien.
Cas laissés DEHORS faute de certitude, à trancher avant de les ajouter :
  • IE (`amazon.ie`) : site irlandais dédié annoncé, ouverture non vérifiée ici ;
  • CN (`amazon.cn`) : exclu délibérément — la marketplace domestique chinoise a
    fermé en 2019, il ne reste qu'une vitrine d'import sans catalogue à
    étudier."""

MOTIF_PAYS_SANS_MARKETPLACE: str = (
    "Amazon n'exploite pas de site dans ce pays. Cet agent est donc INAPPLICABLE "
    "à cette région : il ne saurait interroger qu'une marketplace étrangère, dont "
    "le catalogue, les prix et les avis décriraient un autre marché. Utiliser un "
    "collecteur adapté à la région — AliExpress, Temu, recherche web ou Reddit."
)
"""Motif renvoyé lorsque la région d'étude n'a pas de marketplace propre."""

MARKETPLACES_SANS_DECIMALES: frozenset[str] = frozenset({"amazon.co.jp"})
"""Marketplaces dont la devise n'a pas de sous-unité.

Le filtre de prix d'Amazon (`rh=p_36`) s'exprime en unités MINEURES — centimes,
cents. Sur ces marketplaces, l'unité mineure est l'unité elle-même : multiplier
par 100 y demanderait un prix cent fois trop élevé."""

MOTIF_ABSENCE_LIVRAISON: str = (
    "Aucun `countryCode` ni `zipCode` n'est transmis à l'actor, délibérément. "
    "Renseigner une adresse de livraison ferait masquer par Amazon tout ce "
    "qu'il ne peut pas y expédier et convertirait les prix dans la devise de "
    "ce pays. Le corpus livré est le catalogue COMPLET de la marketplace, dans "
    "sa propre devise : une marketplace n'est pas une destination de livraison."
)
"""Justification centrale du module, reprise dans les hypothèses du résultat."""

# --------------------------------------------------------------------------- #
# Construction des URLs de recherche Amazon
# --------------------------------------------------------------------------- #
TRI_PERTINENCE: str = "pertinence"
TRI_MEILLEURES_VENTES: str = "meilleures_ventes"
TRI_PRIX_CROISSANT: str = "prix_croissant"
TRI_PRIX_DECROISSANT: str = "prix_decroissant"
TRI_NOTE: str = "note"
TRI_NOUVEAUTES: str = "nouveautes"

TRIS: tuple[str, ...] = (
    TRI_PERTINENCE,
    TRI_MEILLEURES_VENTES,
    TRI_PRIX_CROISSANT,
    TRI_PRIX_DECROISSANT,
    TRI_NOTE,
    TRI_NOUVEAUTES,
)

CLES_TRI_AMAZON: dict[str, str] = {
    TRI_MEILLEURES_VENTES: "exact-aware-popularity-rank",
    TRI_PRIX_CROISSANT: "price-asc-rank",
    TRI_PRIX_DECROISSANT: "price-desc-rank",
    TRI_NOTE: "review-rank",
    TRI_NOUVEAUTES: "date-desc-rank",
}
"""Valeurs du paramètre `s=` d'Amazon. `pertinence` est l'absence de paramètre."""

PARAM_RECHERCHE: str = "k"
PARAM_TRI: str = "s"
PARAM_FACETTE: str = "rh"
PREFIXE_FACETTE_PRIX: str = "p_36"
"""Facette de prix native d'Amazon : `rh=p_36:<min>-<max>`, en unités mineures.

Filtrer dans l'URL est le principal gain d'efficacité du module : sans cette
facette, l'actor dépense tout son quota d'items à scraper des produits que le
code écarte ensuite."""

UTILISER_SOLVEUR_CAPTCHA_SUR: frozenset[str] = frozenset({"amazon.com"})
"""Marketplaces où `useCaptchaSolver=True` est activé.

D'après la documentation de l'actor, l'option n'est fiable que sur .com ;
ailleurs elle ajoute des tentatives au lieu d'en épargner."""

# --------------------------------------------------------------------------- #
# Hygiène et qualification du corpus
# --------------------------------------------------------------------------- #
SEUIL_PERTINENCE: float = 0.5
"""Score minimal de pertinence pour figurer dans le corpus final.

Heuristique NON validée empiriquement : aucune mesure de précision ni de rappel
sur un échantillon annoté. Ajustable sans modifier le reste du code."""

TAILLE_LOT_CLASSIFICATION: int = 15
"""Produits soumis par appel LLM de classification."""

LONGUEUR_TITRE_CLASSIFICATION: int = 300
"""Caractères de titre transmis au classifieur. Les titres Amazon sont bourrés
de mots-clés ; au-delà, c'est du remplissage SEO qui dilue le signal."""

# --------------------------------------------------------------------------- #
# Schéma de sortie réel de l'actor produits — junglee/Amazon-crawler.
# Les noms de champs sont ceux CONSTATÉS dans le dataset, pas ceux déduits de la
# documentation. Relevé complet dans le README.
# --------------------------------------------------------------------------- #
CLE_ERREUR: str = "error"
"""L'actor écrit `{"error": "no_results_found", ...}` DANS le dataset au lieu de
faire échouer le run. Ces enregistrements ne sont pas des produits, et leur
présence signale le plus souvent une page bloquée par l'anti-bot."""

CLE_ASIN: str = "asin"
CLE_TITRE: str = "title"
CLE_URL: str = "url"
CLE_IMAGE: str = "thumbnailImage"
CLE_PRIX: str = "price"
CLE_PRIX_BARRE: str = "listPrice"
CLE_PRIX_VALEUR: str = "value"
CLE_PRIX_DEVISE: str = "currency"
CLE_NOTE: str = "stars"
CLE_NB_AVIS: str = "reviewsCount"
CLE_VOLUME_ACHATS: str = "monthlyPurchaseVolume"
"""« X bought in past month » : le signal de demande le plus direct qu'Amazon
expose publiquement."""

CLE_MARQUE: str = "brand"
CLE_CHOIX_AMAZON: str = "isAmazonChoice"
CLE_RANGS: str = "bestsellerRanks"
CLE_RANG_VALEUR: str = "rank"
CLE_RANG_CATEGORIE: str = "category"
CLE_LIVRAISON: str = "delivery"
CLE_EN_STOCK: str = "inStock"

CLE_VENDEUR: str = "seller"
"""`scrapeSellers=True` transforme ce champ en profil complet ; sans l'option,
c'est une simple chaîne. Les deux formes sont gérées à la normalisation."""

CLE_VENDEUR_NOM: str = "name"
CLE_VENDEUR_NOTE_GLOBALE: str = "ratingLifetime"
CLE_VENDEUR_ETOILES: str = "starsOutOf5"
CLE_VENDEUR_NB_NOTES: str = "ratingCount"

# --------------------------------------------------------------------------- #
# Schéma de sortie réel de l'actor avis — junglee/amazon-reviews-scraper.
# --------------------------------------------------------------------------- #
CLE_AVIS_NOTE: str = "ratingScore"
CLE_AVIS_TITRE: str = "reviewTitle"
CLE_AVIS_TEXTE: str = "reviewDescription"
CLE_AVIS_DATE: str = "date"
CLE_AVIS_DATE_REPLI: str = "reviewedIn"
CLE_AVIS_VERIFIE: str = "isVerified"
CLE_AVIS_REACTION: str = "reviewReaction"

MOTIF_ETOILES_TITRE: str = r"^\s*\d(?:[.,]\d)?\s*(?:out of 5 stars|sur 5 étoiles)\s*[-–—:]?\s*"
"""Les titres d'avis arrivent préfixés de la ligne d'étoiles — « 5.0 out of 5
stars – Great sound ». La note est déjà un champ à part : le préfixe est retiré."""

# --------------------------------------------------------------------------- #
# Libellés du modèle de sortie
# --------------------------------------------------------------------------- #
CORRESPONDANCE_EQUIVALENT: str = "produit_equivalent"
CORRESPONDANCE_VARIANTE: str = "variante"
CORRESPONDANCE_ACCESSOIRE: str = "accessoire"
CORRESPONDANCE_HORS_SUJET: str = "hors_sujet"

TYPES_CORRESPONDANCE: tuple[str, ...] = (
    CORRESPONDANCE_EQUIVALENT,
    CORRESPONDANCE_VARIANTE,
    CORRESPONDANCE_ACCESSOIRE,
    CORRESPONDANCE_HORS_SUJET,
)

PREFIXE_WWW: str = "www."

# --------------------------------------------------------------------------- #
# Limites méthodologiques injectées systématiquement dans le résultat
# --------------------------------------------------------------------------- #
LIMITES_METHODOLOGIQUES: list[str] = [
    "Le corpus provient d'UNE seule marketplace Amazon, celle du pays étudié. "
    "Il décrit l'offre présente sur ce site, et non l'ensemble du commerce en "
    "ligne du pays : les places de marché concurrentes et le commerce local en "
    "sont absents.",
    "Aucun filtre de livraison n'est appliqué : le corpus est le catalogue "
    "complet de la marketplace, y compris des produits qui ne seraient pas "
    "expédiables vers la région d'étude. C'est un choix assumé — restreindre à "
    "l'expédiable amputerait l'étude de marché de l'essentiel de l'offre.",
    "Les prix sont exprimés dans la devise de la marketplace et ne sont NI "
    "convertis, NI comparables d'une marketplace à l'autre. Ils sont relevés à "
    "l'instant du run : les prix Amazon varient de jour en jour.",
    "Amazon bloque une part variable des requêtes automatisées. Un corpus court "
    "ou vide peut être le fait de l'anti-bot et non du catalogue : les runs "
    "concernés sont signalés dans `statuts_collecte`.",
    "Le classement d'Amazon est commercial : il mêle publicité, performance "
    "vendeur et ancienneté de l'annonce. L'ordre de collecte n'est pas un "
    "classement de qualité, et le corpus n'est pas exhaustif.",
    "Les avis collectés sont les « meilleurs avis » retenus par Amazon, "
    "quelques-uns par produit et sur une fenêtre récente : ils illustrent des "
    "retours d'usage, ils ne constituent en aucun cas un échantillon "
    "représentatif ni une base de mesure de satisfaction.",
    "`pertinence` et `correspondance` sont des heuristiques produites par un "
    "LLM, sans validation sur un échantillon annoté.",
    "Un compte Apify en fin de quota tronque les runs SANS les faire échouer : "
    "ils remontent en statut SUCCEEDED avec un dataset incomplet. Vérifier le "
    "quota avant toute interprétation d'un volume de produits.",
]

LIMITE_AUCUNE_DONNEE: str = (
    "Aucune donnée collectée : l'intégralité des recherches a échoué (voir "
    "`statuts_collecte`). Ce résultat ne dit rien du marché étudié."
)

LIMITE_COLLECTE_PARTIELLE: str = (
    "Collecte partielle : une ou plusieurs recherches ont échoué, le corpus est "
    "incomplet (voir `statuts_collecte`)."
)

LIMITE_BLOCAGE_AMAZON: str = (
    "Amazon a renvoyé au moins une page de résultats vide ou en erreur "
    "(enregistrements `error` dans le dataset). C'est habituellement une "
    "protection anti-bot plutôt qu'un catalogue vide : la recherche concernée "
    "mérite d'être relancée avant d'en conclure quoi que ce soit."
)

LIMITE_CORPUS_NON_CLASSIFIE: str = (
    "Corpus non classifié : tous les lots de classification LLM ont échoué. Les "
    "produits sont conservés avec `pertinence=None` et `correspondance=None`, "
    "sans avoir été confrontés au seuil de pertinence."
)

LIMITE_CORPUS_PARTIELLEMENT_CLASSIFIE: str = (
    "Corpus partiellement classifié : une partie des lots de classification LLM "
    "a échoué. Les produits concernés sont conservés avec des champs à `None` et "
    "n'ont pas été confrontés au seuil de pertinence."
)

LIMITE_PLAN_INCOMPLET: str = (
    "Le plan de recherches n'a pas atteint le nombre de recherches visé : "
    "certaines propositions étaient non conformes ou en doublon et ont été "
    "écartées. La couverture du catalogue est d'autant plus partielle."
)

LIMITE_CORPUS_INSUFFISANT: str = (
    "Le corpus reste sous le seuil de produits après le cycle de repli : il est "
    "insuffisant pour conclure, et ne doit pas être interprété comme un signal "
    "d'absence d'offre sur cette marketplace."
)

LIMITE_AVIS_INDISPONIBLES: str = (
    "Aucun avis n'a pu être collecté sur les produits retenus. L'analyse "
    "qualitative des retours d'usage est donc impossible sur ce corpus."
)

LIMITE_REGION_NON_COUVERTE: str = (
    "AUCUNE COLLECTE N'A ÉTÉ LANCÉE : la région d'étude n'a pas de site Amazon "
    "propre, ou n'a pas pu être résolue en un pays. Ce résultat ne dit rien du "
    "marché visé — il constate seulement que cet agent ne s'y applique pas. Le "
    "motif exact figure dans la limite suivante et dans `statuts_collecte`."
)

# --------------------------------------------------------------------------- #
# Hypothèses injectées systématiquement dans le résultat
# --------------------------------------------------------------------------- #
HYPOTHESE_MARKETPLACE: str = (
    "Seul le site Amazon DU PAYS étudié est interrogé, et l'agent s'arrête si ce "
    "pays n'en a pas. Le corpus suppose donc qu'Amazon est un canal significatif "
    "sur ce marché — ce qui reste à vérifier pays par pays, la part de marché "
    "d'Amazon variant fortement d'un site à l'autre."
)

HYPOTHESE_ASSIMILATION_RECHERCHES: str = (
    "Le produit est assimilé aux recherches retenues : les produits collectés "
    "relèvent de la catégorie de besoin visée, pas nécessairement de la "
    "référence exacte de la fiche."
)

HYPOTHESE_SEUILS: str = (
    "Seuils appliqués : pertinence ≥ {seuil_pertinence} pour retenir un produit, "
    "{seuil_produits} produit(s) minimum avant déclenchement du repli, avis "
    "collectés sur les {nb_produits_avis} produits les plus pertinents. Ces "
    "valeurs sont des heuristiques non validées empiriquement."
)

# --------------------------------------------------------------------------- #
# Logging — toujours vers stderr, jamais vers stdout (qui doit rester du JSON
# parsable).
# --------------------------------------------------------------------------- #
NOM_LOGGER: str = "agent_amazon"
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
