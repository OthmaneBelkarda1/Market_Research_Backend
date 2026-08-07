"""Constantes, chargement de l'environnement et configuration du logging.

Ce module centralise **toutes** les valeurs configurables du module : aucune
valeur magique ne doit apparaître ailleurs dans le code, y compris les noms de
paramètres d'URL de la bibliothèque publicitaire de Meta et les noms de champs
du schéma de sortie de l'actor Apify.
"""

from __future__ import annotations

import logging
import os
import sys

from dotenv import find_dotenv, load_dotenv

# --------------------------------------------------------------------------- #
# Correctif d'encodage — appliqué au chargement du module.
# Sur Windows, la console utilise cp1252 par défaut : un texte d'annonce
# accentué, un émoji de créatif ou un symbole € y provoque un UnicodeEncodeError
# et fait tomber l'exécution après que la collecte a été facturée.
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
# d'une région en texte libre, plan de recherches, classification des annonces
# par lots) sont mécaniques et à sortie courte : le modèle le plus rapide de la
# gamme suffit.
MODELE_CLAUDE: str = "claude-haiku-4-5-20251001"
TEMPERATURE_LLM: float = 0.0
MAX_TOKENS_LLM: int = 4096

# --------------------------------------------------------------------------- #
# Source de données Apify
# --------------------------------------------------------------------------- #
ACTOR_META_ADS: str = "apify/facebook-ads-scraper"
"""Actor « Facebook Ads Library Scraper », maintenu par Apify.

Actor officiel, sur SDK courant : il se lance aussi bien par `apify-client` que
par le serveur MCP. Les actors communautaires de la même catégorie
(`solidcode/…`, `automly/…`) échouent, eux, sur l'origine de run « MCP ». Ce
module passe de toute façon par `apify-client` en direct.

Il accepte dans `startUrls` aussi bien une URL de recherche de la bibliothèque
publicitaire qu'une URL de Page Facebook."""

MAX_ANNONCES_PAR_RECHERCHE: int = 30
"""Champ `resultsLimit` : annonces scrapées par recherche, donc par run.

⚠️ **L'actor est facturé À L'ANNONCE.** C'est le principal levier de coût du
module, très loin devant le nombre de recherches. Une recherche large sur un
grand pays remonte des milliers d'annonces : sans ce plafond, une exécution
coûterait un multiple de ce qu'elle vaut."""

TIMEOUT_RUN_SECS: int = 600
"""Durée maximale d'un run de collecte d'annonces."""

MARGE_ATTENTE_RUN_SECS: int = 60
"""Marge d'attente côté client au-delà du timeout du run lui-même."""

NB_TENTATIVES_MAX: int = 2
BACKOFF_TENTATIVES_SECS: tuple[int, ...] = (20, 60)
"""Attente avant la n-ième nouvelle tentative.

Volontairement longue : un échec vient le plus souvent d'un blocage de Meta.
Réessayer immédiatement réutilise la session proxy qui vient d'être refusée."""

PAUSE_AVANT_REPLI_SECS: int = 20
"""Pause avant de relancer une recherche restée vide, sans ses filtres d'URL."""

PARALLELISME_MAX: int = 3
"""Nombre maximal de runs Apify simultanés.

Chaque run dispose de sa propre session proxy côté Apify : un parallélisme
modéré n'augmente pas le risque de blocage. La valeur 1 doit rester utilisable
pour revenir à une exécution strictement séquentielle."""

# --------------------------------------------------------------------------- #
# Plan de recherches
# --------------------------------------------------------------------------- #
NB_RECHERCHES: int = 3
"""Recherches distinctes du plan, une par run.

Le moteur de la bibliothèque publicitaire est un simple appariement de mots sur
le texte des annonces : deux formulations proches ne remontent pas les mêmes
créatifs. Chaque recherche supplémentaire est cependant un run facturé."""

NB_RECHERCHES_REPLI: int = 1
"""Recherches de repli générées lorsque le corpus reste sous le seuil, sur un
seul cycle."""

SEUIL_MIN_ANNONCES: int = 5
"""Nombre d'annonces retenues en deçà duquel un cycle de repli est déclenché.

Heuristique non validée : c'est un plancher de non-vacuité, pas un seuil de
représentativité."""

# --------------------------------------------------------------------------- #
# Pays ciblé
# --------------------------------------------------------------------------- #
PAYS_TOUS: str = "ALL"
"""Valeur du paramètre `country` couvrant tous les pays à la fois.

À la différence des marketplaces marchandes, la bibliothèque publicitaire de
Meta n'a pas de « pays non couverts » : elle expose les annonces diffusées dans
n'importe quel pays. Le module ne refuse donc jamais une région — il refuse
seulement une région qu'il n'a pas su résoudre en un pays."""

MOTS_MONDE: frozenset[str] = frozenset(
    {"all", "monde", "world", "worldwide", "global", "international", "tous", "*"}
)
"""Saisies de `--geo` interprétées comme « tous les pays »."""

MOTIF_PAYS_NON_RESOLU: str = (
    "La région d'étude n'a pas pu être résolue en un pays. Aucune collecte n'est "
    "lancée : interroger la bibliothèque publicitaire sur un pays par défaut — ou "
    "sur le monde entier — livrerait un corpus qui ne décrit pas la région "
    "demandée, sans que rien ne le signale. Reprendre avec un code ISO-2 "
    "(« MA », « FR »), ou avec « ALL » pour viser explicitement tous les pays."
)
"""Motif renvoyé lorsque la région d'étude n'est pas résolue."""

MOTIF_CIBLAGE_PAYS: str = (
    "Le pays retenu sélectionne les annonces DIFFUSÉES dans ce pays, sans exiger "
    "qu'il soit leur unique ciblage (`is_targeted_country=false`). Ce n'est ni le "
    "pays de l'annonceur, ni le pays d'expédition du produit : une annonce "
    "internationale diffusée localement figure au corpus, et c'est voulu — c'est "
    "de la pression publicitaire subie sur ce marché qu'il s'agit."
)
"""Justification centrale du module, reprise dans les hypothèses du résultat."""

# --------------------------------------------------------------------------- #
# Construction des URLs de la bibliothèque publicitaire
# --------------------------------------------------------------------------- #
URL_BIBLIOTHEQUE: str = "https://www.facebook.com/ads/library/"
"""Base de toutes les URLs produites, recherche comme fiche d'annonce."""

PARAM_REQUETE: str = "q"
PARAM_PAYS: str = "country"
PARAM_STATUT: str = "active_status"
PARAM_TYPE_ANNONCE: str = "ad_type"
PARAM_TYPE_RECHERCHE: str = "search_type"
PARAM_MEDIA: str = "media_type"
PARAM_CIBLAGE: str = "is_targeted_country"
PARAM_LANGUE_CONTENU: str = "content_languages[0]"
PARAM_IDENTIFIANT: str = "id"

VALEUR_TYPE_ANNONCE: str = "all"
"""`ad_type=all` : toutes les annonces, et non le seul périmètre politique."""

VALEUR_MEDIA: str = "all"
"""`media_type=all` : images, vidéos et créatifs sans média."""

VALEUR_CIBLAGE: str = "false"
"""`is_targeted_country=false` : voir `MOTIF_CIBLAGE_PAYS`."""

STATUT_ACTIVES: str = "actives"
STATUT_INACTIVES: str = "inactives"
STATUT_TOUTES: str = "toutes"

STATUTS: tuple[str, ...] = (STATUT_ACTIVES, STATUT_INACTIVES, STATUT_TOUTES)

STATUTS_META: dict[str, str] = {
    STATUT_ACTIVES: "active",
    STATUT_INACTIVES: "inactive",
    STATUT_TOUTES: "all",
}
"""Valeurs du paramètre `active_status` de la bibliothèque publicitaire."""

RECHERCHE_MOTS_CLES: str = "mots_cles"
RECHERCHE_EXPRESSION_EXACTE: str = "expression_exacte"

TYPES_RECHERCHE: tuple[str, ...] = (RECHERCHE_MOTS_CLES, RECHERCHE_EXPRESSION_EXACTE)

TYPES_RECHERCHE_META: dict[str, str] = {
    RECHERCHE_MOTS_CLES: "keyword_unordered",
    RECHERCHE_EXPRESSION_EXACTE: "keyword_exact_phrase",
}
"""Valeurs du paramètre `search_type`.

`keyword_unordered` apparie les mots isolément, dans n'importe quel ordre :
c'est le mode utile pour une recherche catégorielle. `keyword_exact_phrase`
n'apparie que la suite exacte : c'est le mode d'une marque ou d'un nom de
produit, qui remonterait sinon des annonces sans rapport."""

FILTRER_PAR_LANGUE_CONTENU: bool = False
"""Ajoute `content_languages[0]=<langue>` aux URLs de recherche.

⚠️ Laissé à FAUX, et pour deux raisons. D'abord, le filtre porte sur la langue
du CRÉATIF, pas sur le marché : sur un pays multilingue, il ampute le corpus
d'une partie de la pression publicitaire réellement subie. Ensuite, ce paramètre
n'a **pas été vérifié sur un run réel** de ce module — l'activer sans le
contrôler risque de produire des recherches systématiquement vides."""

# --------------------------------------------------------------------------- #
# Hygiène et qualification du corpus
# --------------------------------------------------------------------------- #
SEUIL_PERTINENCE: float = 0.5
"""Score minimal de pertinence pour figurer dans le corpus final.

Heuristique NON validée empiriquement : aucune mesure de précision ni de rappel
sur un échantillon annoté. Ajustable sans modifier le reste du code."""

TAILLE_LOT_CLASSIFICATION: int = 15
"""Annonces soumises par appel LLM de classification."""

LONGUEUR_TEXTE_CLASSIFICATION: int = 400
"""Caractères de texte d'annonce transmis au classifieur. Un créatif Meta
commence par son argument de vente ; au-delà, ce sont les mentions légales, les
émojis et les hashtags qui diluent le signal."""

LONGUEUR_CLE_CREATIF: int = 200
"""Caractères de texte retenus dans la clé de dédoublonnage par créatif.

Un même créatif est diffusé sous des dizaines d'identifiants d'annonce
différents. Le début du texte suffit à les rapprocher, et tronquer évite qu'une
variation de mention légale en fin de message fasse échouer le rapprochement."""

# --------------------------------------------------------------------------- #
# Schéma de sortie réel de l'actor — apify/facebook-ads-scraper.
# Les noms de champs sont ceux CONSTATÉS dans le dataset, relevés sur les runs
# réels du 04/08/2026. Relevé complet dans le README.
# --------------------------------------------------------------------------- #
CLE_ENVELOPPE_RESULTATS: str = "results"
CLE_ENVELOPPE_TOTAL: str = "totalCount"
"""⚠️ Piège central de cet actor : une recherche SANS RÉSULTAT ne produit pas un
dataset vide, mais **un item d'enveloppe** `{inputUrl, results: [], totalCount:
0, pageInfo, isResultComplete}`. Pris pour une annonce, il fait croire à un item
collecté et empêche de détecter la recherche vide. `meta_ads_source` déballe ces
enveloppes : `results` est repris s'il contient quelque chose, sinon l'enveloppe
est jetée."""

CLE_ID_ANNONCE: str = "adArchiveID"
CLE_ID_ANNONCE_ALT: str = "adArchiveId"
"""L'actor a servi les deux casses selon les versions : les deux sont lues."""

CLE_ANNONCEUR: str = "pageName"
CLE_ID_ANNONCEUR: str = "pageId"
CLE_ID_ANNONCEUR_ALT: str = "pageID"

CLE_COLLATION: str = "collationId"
CLE_COLLATION_NB: str = "collationCount"
"""Groupement des déclinaisons d'un même créatif, tel que Meta le calcule.

C'est la meilleure clé de dédoublonnage disponible : un annonceur diffuse le
même créatif sous des dizaines d'identifiants d'annonce, et Meta les rattache à
une `collationId` commune. `collationCount` en donne le nombre. Champs souvent
nuls : le rapprochement heuristique de `filtering` reste nécessaire en repli."""

CLE_PLATEFORMES: str = "publisherPlatform"
CLE_ACTIVE: str = "isActive"
CLE_DATE_DEBUT: str = "startDate"
CLE_DATE_DEBUT_ISO: str = "startDateFormatted"
CLE_DATE_FIN: str = "endDate"
CLE_DATE_FIN_ISO: str = "endDateFormatted"
CLE_PORTEE: str = "reachEstimate"
CLE_DEPENSE: str = "spend"
CLE_DEVISE: str = "currency"
"""`reachEstimate`, `spend` et `currency` ne sont renseignés que sur les annonces
politiques et de société : Meta ne publie aucun chiffre de diffusion pour les
annonces commerciales. Attendre `null` sur la quasi-totalité du corpus."""

CLE_SNAPSHOT: str = "snapshot"
"""Le créatif lui-même est dans ce sous-objet, pas à la racine de l'item."""

CLE_TITRE: str = "title"
CLE_CORPS: str = "body"
CLE_CORPS_TEXTE: str = "text"
"""`body` est servi tantôt comme objet `{"text": "…"}`, tantôt comme chaîne nue :
les deux formes sont gérées à la normalisation."""

CLE_CTA: str = "ctaText"
CLE_LIEN: str = "linkUrl"
CLE_LEGENDE: str = "caption"
CLE_DESCRIPTION_LIEN: str = "linkDescription"
"""Texte affiché sous le lien. **Porte souvent l'argumentaire complet**, là où
`body.text` se réduit à un titre : constaté sur les runs du 04/08/2026, où le
corps tenait en quatre mots et la description en quatre lignes. Transmis à la
classification au même titre que le corps."""

CLE_IMAGES: str = "images"
CLE_VIDEOS: str = "videos"

CLES_IMAGE: tuple[str, ...] = (
    "resized_image_url",
    "original_image_url",
    "resizedImageUrl",
    "originalImageUrl",
)
"""Clés d'URL d'image, par ordre de préférence. L'actor a servi les deux
conventions de nommage — snake_case côté Meta, camelCase côté actor."""

CLES_VIDEO: tuple[str, ...] = (
    "videoHdUrl",
    "videoSdUrl",
    "videoPreviewImageUrl",
)
"""Clés d'URL de vidéo, par ordre de préférence. L'aperçu ferme la liste : à
défaut du fichier, il reste une image du créatif.

⚠️ Ces URLs sont **signées et éphémères** (CDN Facebook) : elles expirent en
quelques heures. Elles servent à consulter un créatif dans la foulée de la
collecte, pas à l'archiver."""

CLE_FORMAT: str = "displayFormat"
"""Format du créatif tel que Meta le déclare — « VIDEO », « IMAGE », « DCO »…

Source autoritative de `type_media`, préférée à la présence des listes `images`
et `videos` : une annonce vidéo peut avoir une liste d'images (la vignette)."""

MEDIA_IMAGE: str = "image"
MEDIA_VIDEO: str = "video"
MEDIA_INCONNU: str = "inconnu"

FORMATS_META: dict[str, str] = {
    "VIDEO": MEDIA_VIDEO,
    "IMAGE": MEDIA_IMAGE,
    "MEME": MEDIA_IMAGE,
}
"""Correspondance `displayFormat` → `type_media`. Tout format absent de la table
(« DCO », « CAROUSEL »…) retombe sur la présence des listes de médias."""

# --------------------------------------------------------------------------- #
# Libellés du modèle de sortie
# --------------------------------------------------------------------------- #
CORRESPONDANCE_CONCURRENT: str = "concurrent_direct"
CORRESPONDANCE_CATEGORIE: str = "categorie_proche"
CORRESPONDANCE_ACCESSOIRE: str = "accessoire"
CORRESPONDANCE_HORS_SUJET: str = "hors_sujet"

TYPES_CORRESPONDANCE: tuple[str, ...] = (
    CORRESPONDANCE_CONCURRENT,
    CORRESPONDANCE_CATEGORIE,
    CORRESPONDANCE_ACCESSOIRE,
    CORRESPONDANCE_HORS_SUJET,
)

# --------------------------------------------------------------------------- #
# Limites méthodologiques injectées systématiquement dans le résultat
# --------------------------------------------------------------------------- #
LIMITES_METHODOLOGIQUES: list[str] = [
    "Le corpus ne décrit QUE la publicité payante diffusée sur les plateformes "
    "de Meta (Facebook, Instagram, Messenger, Audience Network). Google, TikTok, "
    "les places de marché et le référencement naturel en sont absents : ce n'est "
    "pas une photographie de la pression publicitaire totale sur le marché.",
    "La bibliothèque publicitaire n'expose durablement que les annonces "
    "COMMERCIALES ACTIVES : une annonce arrêtée en sort, sauf dans l'Union "
    "européenne où le règlement sur les services numériques impose un archivage, "
    "et sauf pour les annonces politiques et de société, archivées sept ans. Une "
    "recherche sur les annonces inactives est donc normalement vide hors UE — ce "
    "point est tiré de la politique affichée par Meta et n'a pas été vérifié run "
    "à run.",
    "Aucune donnée de diffusion n'est publiée pour les annonces commerciales : "
    "ni portée, ni dépense, ni ciblage, ni performance. `portee_estimee` et "
    "`depense` restent nuls hors annonces politiques. Rien dans ce corpus ne dit "
    "qu'une annonce a marché.",
    "`duree_diffusion_jours` est un indicateur de LONGÉVITÉ, pas de rentabilité. "
    "Une annonce diffusée longtemps est un indice qu'un annonceur y trouve son "
    "compte, jamais une preuve : un budget mal suivi produit la même trace.",
    "Le moteur de recherche de la bibliothèque apparie des mots sur le TEXTE des "
    "annonces. Un produit vendu par une créative purement visuelle, ou décrit "
    "avec un autre vocabulaire, est invisible à ce corpus.",
    "Un même créatif est diffusé sous de nombreux identifiants d'annonce. Le "
    "module les rapproche par annonceur et par texte, mais un rapprochement reste "
    "approximatif : le décompte d'annonces n'est pas un décompte de campagnes.",
    "`pertinence` et `correspondance` sont des heuristiques produites par un "
    "LLM, sans validation sur un échantillon annoté.",
    "Le corpus est plafonné par recherche et n'est en aucun cas exhaustif : "
    "l'ordre servi par la bibliothèque n'est pas documenté et ne constitue pas un "
    "classement de notoriété ou de budget.",
    "Un compte Apify en fin de quota tronque les runs SANS les faire échouer : "
    "ils remontent en statut SUCCEEDED avec un dataset incomplet. Vérifier le "
    "quota avant toute interprétation d'un volume d'annonces.",
]

LIMITE_AUCUNE_DONNEE: str = (
    "Aucune donnée collectée : l'intégralité des recherches a échoué (voir "
    "`statuts_collecte`). Ce résultat ne dit rien du marché étudié."
)

LIMITE_COLLECTE_PARTIELLE: str = (
    "Collecte partielle : une ou plusieurs recherches ont échoué, le corpus est "
    "incomplet (voir `statuts_collecte`)."
)

LIMITE_RECHERCHES_VIDES: str = (
    "Une ou plusieurs recherches se sont terminées sans aucune annonce. Sur la "
    "bibliothèque publicitaire, c'est le plus souvent un constat réel — personne "
    "n'annonce sur ces mots dans ce pays — mais cela peut aussi venir d'une "
    "formulation trop étroite ou d'un filtre de statut trop restrictif."
)

LIMITE_CORPUS_NON_CLASSIFIE: str = (
    "Corpus non classifié : tous les lots de classification LLM ont échoué. Les "
    "annonces sont conservées avec `pertinence=None` et `correspondance=None`, "
    "sans avoir été confrontées au seuil de pertinence."
)

LIMITE_CORPUS_PARTIELLEMENT_CLASSIFIE: str = (
    "Corpus partiellement classifié : une partie des lots de classification LLM "
    "a échoué. Les annonces concernées sont conservées avec des champs à `None` "
    "et n'ont pas été confrontées au seuil de pertinence."
)

LIMITE_PLAN_INCOMPLET: str = (
    "Le plan de recherches n'a pas atteint le nombre de recherches visé : "
    "certaines propositions étaient non conformes ou en doublon et ont été "
    "écartées. La couverture de la bibliothèque est d'autant plus partielle."
)

LIMITE_CORPUS_INSUFFISANT: str = (
    "Le corpus reste sous le seuil d'annonces après le cycle de repli. Il est "
    "insuffisant pour conclure — et notamment pour conclure à une absence de "
    "pression publicitaire sur ce marché."
)

LIMITE_PLAFOND_ATTEINT: str = (
    "Au moins une recherche a atteint le plafond d'annonces par run : la "
    "bibliothèque en contenait davantage. Le corpus est un ÉCHANTILLON tronqué "
    "de cette recherche, dans un ordre non documenté, et aucun volume ne doit en "
    "être déduit."
)

LIMITE_REGION_NON_COUVERTE: str = (
    "AUCUNE COLLECTE N'A ÉTÉ LANCÉE : la région d'étude n'a pas pu être résolue "
    "en un pays. Ce résultat ne dit rien du marché visé. Le motif exact figure "
    "dans la limite suivante et dans `statuts_collecte`."
)

# --------------------------------------------------------------------------- #
# Hypothèses injectées systématiquement dans le résultat
# --------------------------------------------------------------------------- #
HYPOTHESE_CANAL: str = (
    "Le corpus suppose que Meta est un canal d'acquisition significatif sur le "
    "marché étudié. C'est vrai de la plupart des marchés grand public, mais la "
    "part de Meta dans le mix publicitaire varie fortement d'un pays et d'une "
    "catégorie à l'autre : une bibliothèque pauvre peut signaler un marché "
    "publicitaire actif ailleurs, pas un marché sans concurrence."
)

HYPOTHESE_ASSIMILATION_RECHERCHES: str = (
    "Le produit est assimilé aux recherches retenues : les annonces collectées "
    "relèvent de la catégorie de besoin visée, pas nécessairement de la référence "
    "exacte de la fiche."
)

HYPOTHESE_SEUILS: str = (
    "Seuils appliqués : pertinence ≥ {seuil_pertinence} pour retenir une annonce, "
    "{seuil_annonces} annonce(s) minimum avant déclenchement du repli, "
    "{max_annonces} annonces au plus par recherche. Ces valeurs sont des "
    "heuristiques non validées empiriquement."
)

# --------------------------------------------------------------------------- #
# Logging — toujours vers stderr, jamais vers stdout (qui doit rester du JSON
# parsable).
# --------------------------------------------------------------------------- #
NOM_LOGGER: str = "agent_meta_ads"
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
