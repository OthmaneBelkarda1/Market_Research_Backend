"""Constantes, chargement de l'environnement et configuration du logging.

Ce module centralise **toutes** les valeurs configurables du module : aucune
valeur magique ne doit apparaître ailleurs dans le code, y compris les noms de
champs du schéma de réponse de l'API AliExpress.

Une exception délibérée à cette centralisation : **la région d'étude**. Le
triplet {pays, devise, langue} n'a aucune valeur par défaut ici, et ne doit
jamais en recevoir. Il est fourni à chaque exécution en ligne de commande et
propagé de bout en bout ; toute constante de repli régionale réintroduirait
silencieusement le biais que ce module existe pour éliminer.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

# --------------------------------------------------------------------------- #
# Correctif d'encodage — appliqué au chargement du module.
# Sur Windows, la console utilise cp1252 par défaut : un titre de produit
# accentué écrit sur stdout y devient illisible (« Θcouteurs »), et une requête
# corrompue envoyée à la recherche AliExpress renvoie du hors-sujet.
# --------------------------------------------------------------------------- #
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# --------------------------------------------------------------------------- #
# Environnement — seule source des identifiants API.
# --------------------------------------------------------------------------- #
load_dotenv(find_dotenv(usecwd=True))

ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")

ALIEXPRESS_APP_KEY: str | None = os.getenv("ALIEXPRESS_APP_KEY")
ALIEXPRESS_APP_SECRET: str | None = os.getenv("ALIEXPRESS_APP_SECRET")
ALIEXPRESS_ACCESS_TOKEN: str | None = os.getenv("ALIEXPRESS_ACCESS_TOKEN")
ALIEXPRESS_REFRESH_TOKEN: str | None = os.getenv("ALIEXPRESS_REFRESH_TOKEN")

CHEMIN_FICHIER_TOKENS: Path = Path(__file__).resolve().parent / ".tokens.json"
"""État de session API rafraîchi par `auth.py`.

Seule écriture disque autorisée au module, et seule donnée persistée : ce n'est
pas une donnée d'étude. Le fichier prime sur `.env`, qui n'est jamais modifié.
"""

# --------------------------------------------------------------------------- #
# Modèle LLM
# --------------------------------------------------------------------------- #
# Étapes LLM mécaniques et à sortie courte (contrôle qualité de la fiche,
# dérivation des requêtes marketplace) : le modèle le plus rapide de la gamme
# suffit.
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
# Passerelles API — relevées et vérifiées le 03/08/2026.
# --------------------------------------------------------------------------- #
URL_PASSERELLE_SYNC: str = "https://api-sg.aliexpress.com/sync"
"""Méthodes métier `aliexpress.ds.*` : POST, paramètres en query string."""

URL_TOKEN_CREATE: str = "https://api-sg.aliexpress.com/rest/auth/token/create"
URL_TOKEN_REFRESH: str = "https://api-sg.aliexpress.com/rest/auth/token/refresh"

CHEMIN_REST_CREATE: str = "/auth/token/create"
CHEMIN_REST_REFRESH: str = "/auth/token/refresh"
"""Préfixe de la base de signature des endpoints `/rest/auth/*` — absent des
appels `/sync`."""

METHODE_RECHERCHE: str = "aliexpress.ds.text.search"
METHODE_DETAIL: str = "aliexpress.ds.product.get"

METHODE_SIGNATURE: str = "sha256"

URL_AUTORISATION_OAUTH: str = (
    "https://api-sg.aliexpress.com/oauth/authorize"
    "?response_type=code&force_auth=true&redirect_uri={redirect_uri}&client_id={app_key}"
)
"""Gabarit de l'URL d'autorisation à ouvrir manuellement lorsque le refresh
token a expiré. Le module ne déroule jamais ce flux lui-même : il exige un clic
humain (voir README)."""

GABARIT_REDIRECT_URI: str = "<URI de redirection déclarée dans la console>"
"""L'URI de redirection n'est pas un identifiant : elle appartient à la
configuration de l'app dans la console développeur, que le module ne lit pas."""


def url_autorisation(redirect_uri: str | None = None) -> str:
    """Compose l'URL d'autorisation OAuth à ouvrir dans un navigateur.

    Args:
        redirect_uri: URI de redirection déclarée dans la console développeur.
            Laissée en gabarit si elle n'est pas connue à l'exécution.

    Returns:
        L'URL d'autorisation, à ouvrir manuellement.
    """
    return URL_AUTORISATION_OAUTH.format(
        redirect_uri=redirect_uri or GABARIT_REDIRECT_URI,
        app_key=ALIEXPRESS_APP_KEY or "<ALIEXPRESS_APP_KEY>",
    )

# --------------------------------------------------------------------------- #
# Appels réseau
# --------------------------------------------------------------------------- #
TIMEOUT_APPEL_SECS: int = 30
NB_TENTATIVES_MAX: int = 2
BACKOFF_TENTATIVES_SECS: tuple[int, ...] = (5, 20)
"""Attente avant la n-ième nouvelle tentative."""

NB_TENTATIVES_MAX_TRANSITOIRE: int = 5
BACKOFF_TRANSITOIRE_SECS: tuple[int, ...] = (10, 25, 45, 60)
"""Politique de reprise dédiée aux erreurs listées dans
`CODES_ERREUR_TRANSITOIRE`, nettement plus insistante que la politique générale.

MESURÉ LE 03/08/2026 sur `text.search`, requête et région valides, sans changer
un seul paramètre entre les appels :
    * 6 appels espacés de 10 s → 2 succès (4 × NGSELECTION_SEARCH_ERROR d'abord) ;
    * 5 appels espacés de 30 s → 1 succès ;
    * 12 appels espacés de 0,5 s → 0 succès ;
    * soit 3 succès sur 11 appels en régime espacé.

L'échec est donc INDÉPENDANT DE L'ESPACEMENT : il ne s'agit pas d'un plafond de
débit que l'on pourrait respecter, mais d'une instabilité de la passerelle par
fenêtres de quelques minutes. Espacer davantage ne sert à rien ; seule
l'insistance dans le temps traverse ces fenêtres. `product.get` n'a jamais
montré ce comportement."""

# --------------------------------------------------------------------------- #
# Plafonds de quota
#
# Budget NOMINAL maximal d'appels métier par exécution :
#   NB_MAX_REQUETES × NB_MAX_PAGES_PAR_REQUETE  (phase A, au plus 8)
# + NB_MAX_PRODUITS_DETAILLES                   (phase B, au plus 15)
# + 1 appel d'authentification si le token doit être rafraîchi
# soit 24 appels au plus. Les nouvelles tentatives sur erreur transitoire
# s'ajoutent à ce nominal (au pire ×NB_TENTATIVES_MAX sur les appels concernés) ;
# le compte réel est reporté dans `StatsCollecte.nb_appels_api`.
#
# Aucune limite de débit n'est affichée dans la console développeur (« API Call
# Limit » vide) ; des sources secondaires citent ~5 000 requêtes/jour, non
# confirmées. En revanche, un plafond de DÉBIT non documenté a été constaté à
# l'exploration sur `text.search` (voir PAUSE_ENTRE_RECHERCHES_SECS).
# --------------------------------------------------------------------------- #
NB_MAX_REQUETES: int = 4
TAILLE_PAGE: int = 20
NB_MAX_PAGES_PAR_REQUETE: int = 2
NB_MAX_PRODUITS_DETAILLES: int = 15
PAUSE_ENTRE_APPELS_SECS: float = 0.5

PAUSE_ENTRE_RECHERCHES_SECS: float = 3.0
"""Pause spécifique aux appels `text.search`, plus longue que la pause générale.

Prudence résiduelle, et non remède : la mesure du 03/08/2026 (voir
`NB_TENTATIVES_MAX_TRANSITOIRE`) montre que l'instabilité de cette méthode ne
dépend pas de l'espacement des appels. Une pause de 30 s n'améliorerait rien et
allongerait l'exécution pour rien. `product.get` conserve
`PAUSE_ENTRE_APPELS_SECS`."""

# --------------------------------------------------------------------------- #
# Sélection des produits détaillés
# --------------------------------------------------------------------------- #
SEUIL_SIMILARITE_TITRE: float = 0.25
"""Part minimale des mots d'une requête devant apparaître dans le titre du
produit pour que celui-ci soit jugé pertinent.

Heuristique NON VALIDÉE EMPIRIQUEMENT : aucun échantillon annoté n'a servi à
la calibrer. Elle sert uniquement à écarter le hors-sujet manifeste que la
recherche AliExpress remonte systématiquement en fin de liste (une requête
absurde a renvoyé 504 résultats de composants électroniques)."""

LONGUEUR_MIN_MOT_SIMILARITE: int = 3
"""Les mots plus courts (« de », « à », « le ») ne discriminent rien et
fausseraient la part calculée."""

SEUIL_REMISE_SUSPECTE: float = 60.0
"""Au-delà de ce pourcentage de remise, le produit est conservé mais signalé :
une remise affichée de −67 % relève souvent d'un prix barré théorique."""

# --------------------------------------------------------------------------- #
# Schéma réel des réponses — constaté aux runs d'exploration du 03/08/2026,
# et non déduit de la documentation. Voir README pour le relevé complet.
# --------------------------------------------------------------------------- #
CLE_ENVELOPPE_RECHERCHE: str = "aliexpress_ds_text_search_response"
CLE_ENVELOPPE_DETAIL: str = "aliexpress_ds_product_get_response"

CLE_CODE_RECHERCHE: str = "code"
CODE_SUCCES_RECHERCHE: str = "00"
"""Succès de `text.search` : chaîne « 00 »."""

CLE_CODE_DETAIL: str = "rsp_code"
CODE_SUCCES_DETAIL: int = 200
CLE_MESSAGE_DETAIL: str = "rsp_msg"
"""Succès de `product.get` : entier 200. La détection de succès est donc
hétérogène entre les deux méthodes."""

CLE_DONNEES_RECHERCHE: str = "data"
CLE_RESULTAT_DETAIL: str = "result"
CLE_TOTAL_ANNONCE: str = "totalCount"
CLE_CONTENEUR_PRODUITS: str = "products"
CLE_LISTE_PRODUITS: str = "selection_search_product"

# Champs d'un produit de recherche.
CLE_ITEM_ID: str = "itemId"
CLE_TITRE: str = "title"
CLE_URL_PRODUIT: str = "itemUrl"
CLE_IMAGE: str = "itemMainPic"
CLE_NOTE: str = "score"
CLE_TAUX_EVALUATION: str = "evaluateRate"
CLE_NB_COMMANDES: str = "orders"
CLE_CATEGORIES: str = "cateId"

CLE_PRIX_VENTE_CIBLE: str = "targetSalePrice"
CLE_PRIX_ORIGINAL_CIBLE: str = "targetOriginalPrice"
CLE_DEVISE_CIBLE: str = "targetOriginalPriceCurrency"
CLE_PRIX_FORMATE: str = "salePriceFormat"
"""Les SEULS champs de prix exploitables en recherche.

PIÈGE RÉGIONAL CONFIRMÉ À L'EXPLORATION : `salePrice` / `originalPrice` sont
exprimés dans la devise du vendeur (`salePriceCurrency` valant « CNY », parfois
« USD »), indépendamment de la devise demandée. Consommer `salePrice` par
erreur détruirait le ciblage régional — ces clés ne sont volontairement PAS
définies ici, afin qu'aucun code du module ne puisse les lire."""

CLE_PRIX_MIN_ORIGINE: str = "originMinPrice"
CLE_ORIGINE_PAYS_LIVRAISON: str = "shipToCountry"
CLE_ORIGINE_DEVISE: str = "currencyCode"
"""`originMinPrice` est une chaîne contenant du JSON imbriqué. Elle porte le
pays de livraison réellement appliqué par la passerelle : c'est le seul
contrôle indépendant du ciblage régional disponible en recherche."""

# Champs d'une réponse de détail produit.
CLE_INFOS_BASE: str = "ae_item_base_info_dto"
CLE_SUJET: str = "subject"
CLE_NB_VENTES: str = "sales_count"
CLE_NOTE_MOYENNE: str = "avg_evaluation_rating"
CLE_NB_EVALUATIONS: str = "evaluation_count"
CLE_STATUT_PRODUIT: str = "product_status_type"
CLE_CATEGORIE_DETAIL: str = "category_id"

CLE_CONTENEUR_SKUS: str = "ae_item_sku_info_dtos"
CLE_LISTE_SKUS: str = "ae_item_sku_info_d_t_o"
CLE_SKU_ID: str = "sku_id"
CLE_SKU_ATTRIBUTS: str = "sku_attr"
CLE_SKU_PRIX_BASE: str = "sku_price"
CLE_SKU_PRIX_VENTE: str = "offer_sale_price"
CLE_SKU_DEVISE: str = "currency_code"
CLE_SKU_STOCK: str = "sku_available_stock"
CLE_SKU_PROPRIETES: str = "ae_sku_property_dtos"
CLE_LISTE_PROPRIETES: str = "ae_sku_property_d_t_o"
CLE_PROPRIETE_NOM: str = "sku_property_name"
CLE_PROPRIETE_VALEUR: str = "sku_property_value"

CLE_LOGISTIQUE: str = "logistics_info_dto"
CLE_PAYS_LIVRAISON: str = "ship_to_country"
CLE_DELAI_LIVRAISON: str = "delivery_time"
"""`logistics_info_dto.ship_to_country` renvoie le pays de livraison appliqué :
contrôle indépendant du ciblage régional en phase B.

À NE PAS CONFONDRE avec `ae_item_base_info_dto.currency_code`, qui vaut « CNY »
même sur une requête MA/MAD : c'est la devise du vendeur. Le contrôle de devise
se fait EXCLUSIVEMENT au niveau du SKU (`currency_code` du SKU)."""

# Erreurs de la passerelle.
#
# ÉCART CONSTATÉ AVEC LA FORME ANNONCÉE. La forme attendue était un objet racine
# {"type": "ISV", "code": "IllegalAccessToken", "message": "..."}. La forme
# RÉELLE, vérifiée le 03/08/2026 sur les deux méthodes avec un token
# volontairement invalidé, est enveloppée et la clé du message diffère :
#     {"error_response": {"type": "ISV", "code": "IllegalAccessToken",
#                         "msg": "The specified access token is invalid or expired",
#                         "request_id": "...", "_trace_id_": "..."}}
# La forme racine reste acceptée en repli, au cas où elle existerait sur
# d'autres codes d'erreur que ceux rencontrés.
CLE_ENVELOPPE_ERREUR: str = "error_response"
CLE_ERREUR_TYPE: str = "type"
CLE_ERREUR_CODE: str = "code"
CLES_ERREUR_MESSAGE: tuple[str, ...] = ("msg", "message")

CODE_TOKEN_INVALIDE: str = "IllegalAccessToken"

CODES_ERREUR_TRANSITOIRE: frozenset[str] = frozenset({"NGSELECTION_SEARCH_ERROR"})
"""Erreurs constatées comme transitoires : nouvelle tentative légitime."""

CODE_PRODUIT_INTROUVABLE: int = 605
MESSAGE_PRODUIT_INTROUVABLE: str = "ITEM_ID_NOT_FOUND"

MARQUEURS_ERREUR_QUOTA: tuple[str, ...] = (
    "flow",
    "limit",
    "quota",
    "frequency",
    "too many",
    "traffic",
)
"""Fragments recherchés dans le code et le message d'erreur pour reconnaître un
dépassement de débit ou de quota. La liste des codes officiels n'étant pas
publiée, la détection est heuristique et signalée comme telle."""

MESSAGE_QUOTA: str = (
    "Dépassement de quota ou de débit signalé par la passerelle AliExpress. "
    "Aucune limite officielle n'est affichée dans la console développeur ; "
    "relancer plus tard ou réduire les plafonds de config.py."
)

# --------------------------------------------------------------------------- #
# Libellés du modèle de sortie
# --------------------------------------------------------------------------- #
ETAPE_RECHERCHE: str = "recherche"
ETAPE_DETAIL: str = "detail"
ETAPE_RAFRAICHISSEMENT_TOKEN: str = "rafraichissement_token"
ETAPE_REAUTORISATION_OAUTH: str = "reautorisation_oauth_requise"
ETAPE_CONTROLE_DEVISE: str = "controle_devise"
ETAPE_CONTROLE_PRIX: str = "controle_prix"
ETAPE_CONTROLE_REGION: str = "controle_region"
ETAPE_STRATEGIE: str = "strategie"

MESSAGE_RECHERCHE_VIDE: str = (
    "Appel réussi, aucun produit renvoyé : soit aucun produit du programme "
    "dropshipping ne correspond à la requête, soit aucun n'est livrable dans la "
    "région demandée. Information légitime, pas un échec de collecte."
)

# --------------------------------------------------------------------------- #
# Limites méthodologiques injectées systématiquement dans le résultat
# --------------------------------------------------------------------------- #
LIMITES_METHODOLOGIQUES: list[str] = [
    "Biais de couverture : l'API Dropshipping n'expose que les produits "
    "éligibles au programme dropshipping ET livrables dans la région demandée. "
    "L'absence d'un produit dans cette collecte ne constitue en aucun cas un "
    "signal d'absence de ce produit sur le marché.",
    "Les prix renvoyés par l'API sont des prix de référence : ils excluent les "
    "Welcome Deals, coupons panier et promotions de session. Le prix perçu par "
    "l'acheteur sur le site peut être inférieur. L'écart avec une source de "
    "scraping est un signal d'intensité promotionnelle, pas une erreur.",
    "Les prix et les stocks sont un instantané horodaté, valable pour le seul "
    "triplet {pays de livraison, devise, langue} demandé. Toute comparaison "
    "exige le même triplet et un horodatage proche.",
    "Aucune conversion de change n'est effectuée : l'API ne fournit pas de "
    "taux. Les montants ne sont comparables qu'au sein d'une même exécution, "
    "dans la devise d'étude.",
    "Le quota journalier n'est pas confirmé officiellement (console développeur "
    "sans limite affichée ; ~5 000 requêtes/jour selon des sources "
    "secondaires). Un plafond de débit non documenté a en revanche été constaté "
    "sur la méthode de recherche.",
    "La sélection des produits soumis au détail (phase B) repose sur une "
    "heuristique déterministe — similarité de titre puis tri par volume de "
    "commandes et note — qui n'a pas été validée empiriquement.",
    "Le champ `discount` de la recherche est incohérent avec les prix cibles "
    "(observé à « 0 % » sur un produit affichant −50 %) : il n'est pas exploité, "
    "la remise est recalculée à partir des prix cibles.",
    "La méthode de recherche est instable par fenêtres de quelques minutes "
    "(erreur NGSELECTION_SEARCH_ERROR mesurée sur 8 appels valides sur 11 le "
    "03/08/2026, indépendamment de l'espacement). Le module insiste, mais une "
    "requête peut rester en échec : la couverture d'une exécution n'est pas "
    "reproductible à l'identique, et un écart de volume entre deux exécutions "
    "peut n'être qu'un artefact de disponibilité de la passerelle.",
]

LIMITE_REQUETES_NON_OPTIMISEES: str = (
    "Requêtes non optimisées (repli sans LLM) : la dérivation des requêtes "
    "marketplace a échoué, le nom brut du produit a servi de requête unique. "
    "La couverture de la collecte en est réduite."
)

LIMITE_PHASE_B_ABSENTE: str = (
    "Phase B non aboutie : aucun prix par SKU n'a pu être collecté. Le résultat "
    "se limite aux prix d'annonce de la recherche, qui ne sont que le prix du "
    "SKU le moins cher de chaque produit."
)

LIMITE_PHASE_B_PARTIELLE: str = (
    "Phase B partielle : le détail n'a pu être obtenu que pour une partie des "
    "produits sélectionnés (voir `statuts_collecte`)."
)

LIMITE_PHASE_A_PARTIELLE: str = (
    "Phase A partielle : une ou plusieurs requêtes de recherche ont échoué, le "
    "corpus de produits est incomplet (voir `statuts_collecte`)."
)

LIMITE_AUCUNE_DONNEE: str = (
    "Aucune donnée collectée : l'intégralité de la collecte a échoué (voir "
    "`statuts_collecte`). Ce résultat ne dit rien du marché étudié."
)

LIMITE_DEVISE_DIVERGENTE: str = (
    "Divergence de devise détectée : une ou plusieurs lignes de prix renvoyées "
    "par l'API n'étaient pas libellées dans la devise demandée. Ces lignes ont "
    "été EXCLUES, jamais converties ni corrigées (voir `statuts_collecte`)."
)

LIMITE_REMISE_SUSPECTE: str = (
    f"Remise(s) supérieure(s) à {SEUIL_REMISE_SUSPECTE:.0f} % — vérifier s'il "
    "s'agit d'une promotion exceptionnelle ou d'un prix barré théorique."
)

LIMITE_PRIX_INCOHERENT: str = (
    "Incohérence de prix détectée : prix de vente supérieur au prix de base sur "
    "une ou plusieurs lignes. Les lignes sont conservées et signalées."
)

LIMITE_SELECTION_NON_FILTREE: str = (
    "Le filtre de similarité titre/requête a écarté tous les produits : il a été "
    "neutralisé pour cette exécution et la sélection repose uniquement sur le "
    "tri par commandes et note. Les produits détaillés peuvent être hors sujet."
)

LIMITE_PAYS_LIVRAISON_DIVERGENT: str = (
    "Le pays de livraison confirmé par l'API diffère du pays demandé sur au "
    "moins un produit (voir `statuts_collecte`) : le ciblage régional de ces "
    "lignes n'est pas garanti."
)

# --------------------------------------------------------------------------- #
# Hypothèses injectées systématiquement dans le résultat
# --------------------------------------------------------------------------- #
HYPOTHESE_ASSIMILATION_REQUETES: str = (
    "Le produit étudié est assimilé aux requêtes marketplace retenues : les "
    "produits collectés relèvent de la catégorie de besoin visée, et non "
    "nécessairement de la référence exacte de la fiche."
)

HYPOTHESE_SELECTION_PHASE_B: str = (
    "Règle de sélection de la phase B : les produits dont le titre partage au "
    f"moins {SEUIL_SIMILARITE_TITRE:.0%} des mots significatifs d'une requête "
    "sont triés par nombre de commandes décroissant, puis par note décroissante, "
    f"et les {NB_MAX_PRODUITS_DETAILLES} premiers sont détaillés. Le nombre de "
    "commandes est retenu comme meilleur signal disponible de traction "
    "commerciale, faute de données de ventes réelles."
)

HYPOTHESE_NOTE_COMPARABLE: str = (
    "Les notes et taux d'évaluation sont supposés approximativement comparables "
    "d'une région à l'autre : ils agrègent des avis de toutes provenances et ne "
    "sont pas régionalisés par la passerelle, contrairement aux prix."
)

HYPOTHESE_PRIX_ANNONCE: str = (
    "Le prix d'annonce de la recherche (`targetSalePrice`) est interprété comme "
    "le prix du SKU le moins cher du produit ; les prix par SKU de la phase B "
    "font foi pour toute lecture fine."
)

# --------------------------------------------------------------------------- #
# Logging — toujours vers stderr, jamais vers stdout (qui doit rester du JSON
# parsable).
# --------------------------------------------------------------------------- #
NOM_LOGGER: str = "agent_aliexpress_api"
FORMAT_LOG: str = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"

LONGUEUR_TOKEN_LOGUEE: int = 8
"""Nombre de caractères de tête d'un token repris dans les logs. Le secret
d'application, lui, n'est jamais logué, même tronqué."""


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


def masquer(secret: str | None) -> str:
    """Tronque un token pour l'affichage dans les logs.

    Args:
        secret: Valeur à masquer, éventuellement nulle.

    Returns:
        Les premiers caractères suivis d'une ellipse, ou « absent ».
    """
    if not secret:
        return "absent"
    return f"{secret[:LONGUEUR_TOKEN_LOGUEE]}…"


def verifier_identifiants() -> None:
    """Vérifie que les identifiants API nécessaires sont disponibles.

    Appelée au démarrage de la CLI plutôt qu'au chargement du module, afin que
    `--help` reste utilisable sans configuration. La vérification porte sur
    l'application (toujours requise) et sur la disponibilité d'au moins un
    couple de tokens, dans `.env` ou dans `.tokens.json`.

    Raises:
        RuntimeError: Si une variable indispensable est absente. Le message
            liste précisément ce qui manque et où le renseigner.
    """
    manquantes: list[str] = []
    if not ALIEXPRESS_APP_KEY:
        manquantes.append("ALIEXPRESS_APP_KEY")
    if not ALIEXPRESS_APP_SECRET:
        manquantes.append("ALIEXPRESS_APP_SECRET")

    tokens_disponibles = bool(ALIEXPRESS_ACCESS_TOKEN and ALIEXPRESS_REFRESH_TOKEN) or (
        CHEMIN_FICHIER_TOKENS.exists()
    )
    if not tokens_disponibles:
        manquantes.extend(["ALIEXPRESS_ACCESS_TOKEN", "ALIEXPRESS_REFRESH_TOKEN"])

    if manquantes:
        raise RuntimeError(
            "Identifiants API AliExpress incomplets. Variable(s) attendue(s) "
            f"dans le fichier .env à la racine du projet : {', '.join(manquantes)}.\n"
            "Ces valeurs ne peuvent être fournies ni en argument de ligne de "
            "commande ni en dur dans le code. Un fichier .env.example accompagne "
            "le module.\n"
            f"Aucun état de session n'a été trouvé non plus dans "
            f"{CHEMIN_FICHIER_TOKENS}."
        )


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
