"""Passage des réponses brutes aux modèles de sortie, et contrôles associés.

Aucun appel LLM ici, aucune entrée-sortie : uniquement des fonctions pures,
appliquées au schéma RÉELLEMENT constaté le 03/08/2026 (voir README) et jamais
à des champs supposés.

Trois règles structurent ce module :

1. **Les prix de recherche viennent exclusivement des champs `target*`.**
   `salePrice` et `originalPrice` sont libellés dans la devise du vendeur (CNY,
   parfois USD) quelle que soit la devise demandée : les lire détruirait le
   ciblage régional. Ces clés ne sont pas même définies dans `config.py`.
2. **La devise se contrôle, elle ne se corrige pas.** Toute ligne dont la devise
   renvoyée diffère de la devise demandée est EXCLUE et consignée en anomalie.
   Aucune conversion, aucun rattrapage silencieux.
3. **Un prix est daté.** Chaque ligne porte son contexte régional complet, dont
   l'horodatage UTC du relevé : c'est un point d'une série temporelle, pas un
   attribut du produit.
"""

from __future__ import annotations

import json
import statistics
import unicodedata
from datetime import UTC, datetime

from config import (
    CLE_CATEGORIES,
    CLE_CONTENEUR_SKUS,
    CLE_DELAI_LIVRAISON,
    CLE_DEVISE_CIBLE,
    CLE_IMAGE,
    CLE_INFOS_BASE,
    CLE_ITEM_ID,
    CLE_LISTE_PROPRIETES,
    CLE_LISTE_SKUS,
    CLE_LOGISTIQUE,
    CLE_NB_COMMANDES,
    CLE_NB_EVALUATIONS,
    CLE_NB_VENTES,
    CLE_NOTE,
    CLE_NOTE_MOYENNE,
    CLE_ORIGINE_PAYS_LIVRAISON,
    CLE_PAYS_LIVRAISON,
    CLE_PRIX_FORMATE,
    CLE_PRIX_MIN_ORIGINE,
    CLE_PRIX_ORIGINAL_CIBLE,
    CLE_PRIX_VENTE_CIBLE,
    CLE_PROPRIETE_NOM,
    CLE_PROPRIETE_VALEUR,
    CLE_SKU_ATTRIBUTS,
    CLE_SKU_DEVISE,
    CLE_SKU_ID,
    CLE_SKU_PRIX_BASE,
    CLE_SKU_PRIX_VENTE,
    CLE_SKU_PROPRIETES,
    CLE_SKU_STOCK,
    CLE_STATUT_PRODUIT,
    CLE_SUJET,
    CLE_TAUX_EVALUATION,
    CLE_TITRE,
    CLE_URL_PRODUIT,
    ETAPE_CONTROLE_DEVISE,
    ETAPE_CONTROLE_PRIX,
    ETAPE_CONTROLE_REGION,
    ETAPE_DETAIL,
    ETAPE_RECHERCHE,
    LIMITE_DEVISE_DIVERGENTE,
    LIMITE_PAYS_LIVRAISON_DIVERGENT,
    LIMITE_PRIX_INCOHERENT,
    LIMITE_REMISE_SUSPECTE,
    LIMITE_SELECTION_NON_FILTREE,
    LONGUEUR_MIN_MOT_SIMILARITE,
    NB_MAX_PRODUITS_DETAILLES,
    SEUIL_REMISE_SUSPECTE,
    SEUIL_SIMILARITE_TITRE,
    obtenir_logger,
)
from schemas import (
    ContexteRegional,
    ParametresMarche,
    PrixSku,
    ProduitDetaille,
    ProduitRecherche,
    StatsCollecte,
    StatutCollecte,
)

_LOG = obtenir_logger(__name__)

_PREFIXE_PROTOCOLE_RELATIF = "//"
_PROTOCOLE = "https:"
_SUFFIXE_VOLUME = "+"
_SEPARATEUR_MILLIERS = ","
_SEPARATEUR_CATEGORIES = ","


def horodatage_utc() -> str:
    """Retourne l'instant du relevé.

    Returns:
        La date courante en ISO 8601 UTC.
    """
    return datetime.now(UTC).isoformat()


def construire_contexte(
    marche: ParametresMarche,
    methode_api: str,
    horodatage: str,
    pays_confirme: str | None = None,
) -> ContexteRegional:
    """Assemble le contexte régional d'une ligne de prix.

    Args:
        marche: Région d'étude demandée.
        methode_api: Méthode d'origine du relevé.
        horodatage: Horodatage UTC du relevé.
        pays_confirme: Pays de livraison confirmé par l'API, si disponible.

    Returns:
        Le contexte à attacher à la ligne.
    """
    return ContexteRegional(
        pays_livraison=marche.geo,
        devise=marche.devise,
        langue=marche.langue,
        horodatage_utc=horodatage,
        methode_api=methode_api,
        pays_livraison_confirme=pays_confirme,
    )


# --------------------------------------------------------------------------- #
# Conversions élémentaires
# --------------------------------------------------------------------------- #


def _flottant(valeur: object) -> float | None:
    """Convertit une valeur de la passerelle en nombre décimal.

    Args:
        valeur: Valeur brute, souvent une chaîne, parfois vide.

    Returns:
        Le nombre correspondant, ou `None` si la valeur est absente ou
        inexploitable.
    """
    if valeur is None:
        return None
    texte = str(valeur).strip()
    if not texte:
        return None
    try:
        return float(texte)
    except ValueError:
        return None


def _entier(valeur: object) -> int | None:
    """Convertit une valeur de la passerelle en entier.

    Args:
        valeur: Valeur brute.

    Returns:
        L'entier correspondant, ou `None` si la valeur est inexploitable.
    """
    nombre = _flottant(valeur)
    return int(nombre) if nombre is not None else None


def _volume_commandes(valeur: object) -> int | None:
    """Interprète un volume de commandes tel que l'affiche la passerelle.

    Le champ est une chaîne mise en forme : « 5 », « 3,000+ », ou vide. Le
    suffixe « + » indique un palier : la valeur retenue est donc un PLANCHER,
    jamais un compte exact.

    Args:
        valeur: Valeur brute du champ `orders`.

    Returns:
        Le nombre de commandes annoncé, ou `None` si le champ est vide.
    """
    if valeur is None:
        return None
    texte = str(valeur).strip().removesuffix(_SUFFIXE_VOLUME)
    texte = texte.replace(_SEPARATEUR_MILLIERS, "").replace(" ", "")
    if not texte:
        return None
    try:
        return int(texte)
    except ValueError:
        return None


def _url_absolue(valeur: object) -> str | None:
    """Rétablit le protocole des URL renvoyées en relatif.

    La passerelle renvoie `//www.aliexpress.com/item/…`, inexploitable tel quel.

    Args:
        valeur: URL brute.

    Returns:
        L'URL absolue, ou `None` si le champ est vide.
    """
    if not valeur:
        return None
    texte = str(valeur).strip()
    if texte.startswith(_PREFIXE_PROTOCOLE_RELATIF):
        return _PROTOCOLE + texte
    return texte or None


def _remise(prix_vente: float | None, prix_base: float | None) -> float | None:
    """Recalcule la profondeur de remise à partir des prix cibles.

    Le champ `discount` de la passerelle n'est pas exploité : il a été observé à
    « 0 % » sur un produit affichant −50 % entre ses prix cibles.

    Args:
        prix_vente: Prix de vente dans la devise d'étude.
        prix_base: Prix barré dans la devise d'étude.

    Returns:
        La remise en pourcentage, ou `None` si elle n'est pas calculable.
    """
    if prix_vente is None or not prix_base:
        return None
    return round((prix_base - prix_vente) / prix_base * 100, 2)


def _pays_livraison_annonce(item: dict) -> str | None:
    """Lit le pays de livraison confirmé dans `originMinPrice`.

    Ce champ est une chaîne contenant du JSON imbriqué ; c'est le seul contrôle
    indépendant du ciblage régional disponible en phase A.

    Args:
        item: Produit brut de la recherche.

    Returns:
        Le pays confirmé, ou `None` si le champ est absent ou illisible.
    """
    brut = item.get(CLE_PRIX_MIN_ORIGINE)
    if not brut:
        return None
    try:
        detail = json.loads(brut)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    pays = detail.get(CLE_ORIGINE_PAYS_LIVRAISON) if isinstance(detail, dict) else None
    return str(pays) if pays else None


# --------------------------------------------------------------------------- #
# Phase A — produits de recherche
# --------------------------------------------------------------------------- #


def normaliser_produits_recherche(
    items: list[dict],
    requete: str,
    marche: ParametresMarche,
    horodatage: str,
) -> tuple[list[ProduitRecherche], list[StatutCollecte], list[str]]:
    """Convertit les produits bruts d'une recherche en modèles validés.

    Les lignes dont la devise renvoyée diffère de la devise demandée, ou dont le
    prix cible est absent, sont exclues et consignées : une ligne de prix
    douteuse vaut moins que pas de ligne du tout.

    Args:
        items: Produits bruts renvoyés par la passerelle.
        requete: Requête ayant produit ces items.
        marche: Région d'étude demandée.
        horodatage: Horodatage UTC du relevé.

    Returns:
        Un triplet `(produits, anomalies, limites)`.
    """
    produits: list[ProduitRecherche] = []
    anomalies: list[StatutCollecte] = []
    limites: list[str] = []

    for item in items:
        item_id = str(item.get(CLE_ITEM_ID) or "").strip()
        titre = str(item.get(CLE_TITRE) or "").strip()
        if not item_id or not titre:
            continue

        devise_reponse = str(item.get(CLE_DEVISE_CIBLE) or "").strip().upper()
        if devise_reponse != marche.devise:
            anomalies.append(
                StatutCollecte(
                    etape=ETAPE_CONTROLE_DEVISE,
                    cible=item_id,
                    succes=False,
                    message_erreur=(
                        f"Devise renvoyée « {devise_reponse or 'absente'} » "
                        f"différente de la devise demandée « {marche.devise} » : "
                        "produit exclu, aucune conversion effectuée."
                    ),
                )
            )
            limites.append(LIMITE_DEVISE_DIVERGENTE)
            continue

        prix_vente = _flottant(item.get(CLE_PRIX_VENTE_CIBLE))
        if prix_vente is None:
            anomalies.append(
                StatutCollecte(
                    etape=ETAPE_CONTROLE_DEVISE,
                    cible=item_id,
                    succes=False,
                    message_erreur=(
                        f"Champ « {CLE_PRIX_VENTE_CIBLE} » absent ou illisible : "
                        "produit exclu (le prix hors devise cible n'est jamais "
                        "utilisé en repli)."
                    ),
                )
            )
            continue

        prix_original = _flottant(item.get(CLE_PRIX_ORIGINAL_CIBLE))
        if prix_original is not None and prix_vente > prix_original:
            limites.append(LIMITE_PRIX_INCOHERENT)
            anomalies.append(
                StatutCollecte(
                    etape=ETAPE_CONTROLE_PRIX,
                    cible=item_id,
                    succes=False,
                    message_erreur=(
                        f"Prix de vente {prix_vente} supérieur au prix de base "
                        f"{prix_original} : ligne conservée et signalée."
                    ),
                )
            )

        remise = _remise(prix_vente, prix_original)
        if remise is not None and remise > SEUIL_REMISE_SUSPECTE:
            limites.append(LIMITE_REMISE_SUSPECTE)

        pays_confirme = _pays_livraison_annonce(item)
        if pays_confirme and pays_confirme.upper() != marche.geo:
            limites.append(LIMITE_PAYS_LIVRAISON_DIVERGENT)
            anomalies.append(
                StatutCollecte(
                    etape=ETAPE_CONTROLE_REGION,
                    cible=item_id,
                    succes=False,
                    message_erreur=(
                        f"Pays de livraison confirmé « {pays_confirme} » "
                        f"différent du pays demandé « {marche.geo} »."
                    ),
                )
            )

        categories = [
            partie.strip()
            for partie in str(item.get(CLE_CATEGORIES) or "").split(_SEPARATEUR_CATEGORIES)
            if partie.strip()
        ]

        produits.append(
            ProduitRecherche(
                item_id=item_id,
                titre=titre,
                url_produit=_url_absolue(item.get(CLE_URL_PRODUIT)),
                image=_url_absolue(item.get(CLE_IMAGE)),
                prix_vente=prix_vente,
                prix_original=prix_original,
                devise=devise_reponse,
                prix_formate=str(item.get(CLE_PRIX_FORMATE) or "") or None,
                remise_pourcentage=remise,
                note=_flottant(item.get(CLE_NOTE)),
                taux_evaluation=_flottant(item.get(CLE_TAUX_EVALUATION)),
                nb_commandes=_volume_commandes(item.get(CLE_NB_COMMANDES)),
                ids_categories=categories,
                requete_origine=requete,
                contexte=construire_contexte(
                    marche, ETAPE_RECHERCHE, horodatage, pays_confirme
                ),
            )
        )

    return produits, anomalies, limites


def dedoublonner(produits: list[ProduitRecherche]) -> list[ProduitRecherche]:
    """Élimine les doublons d'`item_id` entre requêtes.

    La première occurrence est conservée, avec sa `requete_origine` : elle
    documente par quelle formulation le produit a été atteint en premier.

    Args:
        produits: Produits de toutes les requêtes, dans l'ordre de collecte.

    Returns:
        Les produits uniques, dans l'ordre d'apparition.
    """
    vus: set[str] = set()
    uniques: list[ProduitRecherche] = []
    for produit in produits:
        if produit.item_id in vus:
            continue
        vus.add(produit.item_id)
        uniques.append(produit)
    return uniques


# --------------------------------------------------------------------------- #
# Sélection des produits à détailler
# --------------------------------------------------------------------------- #


def _mots_significatifs(texte: str) -> set[str]:
    """Découpe un texte en mots comparables.

    Les accents sont repliés et les mots courts écartés : « de », « à » ou
    « le » n'ont aucun pouvoir discriminant et gonfleraient artificiellement la
    similarité.

    Args:
        texte: Texte à découper.

    Returns:
        L'ensemble des mots significatifs, en minuscules sans accents.
    """
    normalise = unicodedata.normalize("NFKD", texte.casefold())
    sans_accents = "".join(c for c in normalise if not unicodedata.combining(c))
    mots = "".join(c if c.isalnum() else " " for c in sans_accents).split()
    return {mot for mot in mots if len(mot) >= LONGUEUR_MIN_MOT_SIMILARITE}


def similarite_titre(titre: str, requetes: list[str]) -> float:
    """Mesure l'accord entre un titre de produit et les requêtes émises.

    La mesure retenue est la part des mots significatifs d'une requête que l'on
    retrouve dans le titre — et non l'inverse : un titre AliExpress compte
    couramment vingt mots, ce qui écraserait toute mesure symétrique. La
    meilleure requête l'emporte.

    Args:
        titre: Titre du produit.
        requetes: Requêtes émises pendant la collecte.

    Returns:
        La part maximale observée, entre 0 et 1.
    """
    mots_titre = _mots_significatifs(titre)
    if not mots_titre:
        return 0.0

    meilleure = 0.0
    for requete in requetes:
        mots_requete = _mots_significatifs(requete)
        if not mots_requete:
            continue
        part = len(mots_requete & mots_titre) / len(mots_requete)
        meilleure = max(meilleure, part)
    return meilleure


def selectionner_produits(
    produits: list[ProduitRecherche], requetes: list[str]
) -> tuple[list[ProduitRecherche], list[str]]:
    """Retient les produits à soumettre au détail, par règle déterministe.

    Règle, documentée et reproductible :
        1. écarter les produits dont le titre partage moins de
           `SEUIL_SIMILARITE_TITRE` des mots significatifs d'une requête — la
           recherche AliExpress remonte systématiquement du hors-sujet en fin de
           liste ;
        2. trier par nombre de commandes décroissant, puis par note
           décroissante, puis par `item_id` pour lever tout ex æquo et garantir
           un ordre stable d'une exécution à l'autre ;
        3. conserver les `NB_MAX_PRODUITS_DETAILLES` premiers — chaque produit
           détaillé coûte un appel.

    Args:
        produits: Produits dédoublonnés de la phase A.
        requetes: Requêtes émises, servant de référence de similarité.

    Returns:
        Un couple `(selection, limites)`.
    """
    limites: list[str] = []
    pertinents = [
        produit
        for produit in produits
        if similarite_titre(produit.titre, requetes) >= SEUIL_SIMILARITE_TITRE
    ]

    if not pertinents and produits:
        # Le seuil est une heuristique : s'il vide la sélection, c'est lui qu'il
        # faut suspecter, pas la collecte. On préfère détailler des produits
        # possiblement hors sujet — signalés comme tels — que rien du tout.
        _LOG.warning(
            "Le filtre de similarité a écarté les %s produits : filtre neutralisé.",
            len(produits),
        )
        pertinents = list(produits)
        limites.append(LIMITE_SELECTION_NON_FILTREE)

    classes = sorted(
        pertinents,
        key=lambda produit: (
            -(produit.nb_commandes or 0),
            -(produit.note or 0.0),
            produit.item_id,
        ),
    )
    return classes[:NB_MAX_PRODUITS_DETAILLES], limites


# --------------------------------------------------------------------------- #
# Phase B — détail par SKU
# --------------------------------------------------------------------------- #


def _attributs_lisibles(sku: dict) -> dict[str, str]:
    """Extrait les attributs traduits d'un SKU.

    Args:
        sku: SKU brut.

    Returns:
        Les couples {nom d'attribut: valeur}, vides si la structure est absente.
    """
    conteneur = sku.get(CLE_SKU_PROPRIETES) or {}
    proprietes = conteneur.get(CLE_LISTE_PROPRIETES) or []
    lisibles: dict[str, str] = {}
    for propriete in proprietes:
        if not isinstance(propriete, dict):
            continue
        nom = str(propriete.get(CLE_PROPRIETE_NOM) or "").strip()
        valeur = str(propriete.get(CLE_PROPRIETE_VALEUR) or "").strip()
        if nom and valeur:
            lisibles[nom] = valeur
    return lisibles


def normaliser_detail(
    resultat: dict,
    item_id: str,
    marche: ParametresMarche,
    horodatage: str,
) -> tuple[ProduitDetaille | None, list[StatutCollecte], list[str]]:
    """Convertit une réponse de détail produit en modèle validé.

    Le contrôle de devise porte sur le SKU, et sur lui seul :
    `ae_item_base_info_dto.currency_code` vaut « CNY » même sur une demande
    MA/MAD — c'est la devise du vendeur, pas celle de l'étude.

    Args:
        resultat: Bloc `result` de la réponse.
        item_id: Identifiant du produit interrogé.
        marche: Région d'étude demandée.
        horodatage: Horodatage UTC du relevé.

    Returns:
        Un triplet `(produit, anomalies, limites)` ; le produit vaut `None` si
        la réponse ne contient aucune information exploitable.
    """
    anomalies: list[StatutCollecte] = []
    limites: list[str] = []

    infos = resultat.get(CLE_INFOS_BASE) or {}
    logistique = resultat.get(CLE_LOGISTIQUE) or {}
    pays_confirme = str(logistique.get(CLE_PAYS_LIVRAISON) or "") or None
    if pays_confirme and pays_confirme.upper() != marche.geo:
        limites.append(LIMITE_PAYS_LIVRAISON_DIVERGENT)
        anomalies.append(
            StatutCollecte(
                etape=ETAPE_CONTROLE_REGION,
                cible=item_id,
                succes=False,
                message_erreur=(
                    f"Pays de livraison confirmé « {pays_confirme} » différent du "
                    f"pays demandé « {marche.geo} »."
                ),
            )
        )

    conteneur = resultat.get(CLE_CONTENEUR_SKUS) or {}
    skus_bruts = conteneur.get(CLE_LISTE_SKUS) or []
    skus: list[PrixSku] = []

    for sku in skus_bruts:
        if not isinstance(sku, dict):
            continue
        devise_sku = str(sku.get(CLE_SKU_DEVISE) or "").strip().upper()
        if devise_sku != marche.devise:
            limites.append(LIMITE_DEVISE_DIVERGENTE)
            anomalies.append(
                StatutCollecte(
                    etape=ETAPE_CONTROLE_DEVISE,
                    cible=f"{item_id}/{sku.get(CLE_SKU_ID)}",
                    succes=False,
                    message_erreur=(
                        f"Devise du SKU « {devise_sku or 'absente'} » différente "
                        f"de la devise demandée « {marche.devise} » : SKU exclu, "
                        "aucune conversion effectuée."
                    ),
                )
            )
            continue

        prix_vente = _flottant(sku.get(CLE_SKU_PRIX_VENTE))
        prix_base = _flottant(sku.get(CLE_SKU_PRIX_BASE))
        if prix_vente is None:
            anomalies.append(
                StatutCollecte(
                    etape=ETAPE_CONTROLE_DEVISE,
                    cible=f"{item_id}/{sku.get(CLE_SKU_ID)}",
                    succes=False,
                    message_erreur=(
                        f"Champ « {CLE_SKU_PRIX_VENTE} » absent ou illisible : SKU exclu."
                    ),
                )
            )
            continue

        if prix_base is not None and prix_vente > prix_base:
            limites.append(LIMITE_PRIX_INCOHERENT)
            anomalies.append(
                StatutCollecte(
                    etape=ETAPE_CONTROLE_PRIX,
                    cible=f"{item_id}/{sku.get(CLE_SKU_ID)}",
                    succes=False,
                    message_erreur=(
                        f"Prix de vente {prix_vente} supérieur au prix de base "
                        f"{prix_base} : SKU conservé et signalé."
                    ),
                )
            )

        remise = _remise(prix_vente, prix_base)
        if remise is not None and remise > SEUIL_REMISE_SUSPECTE:
            limites.append(LIMITE_REMISE_SUSPECTE)

        skus.append(
            PrixSku(
                sku_id=str(sku.get(CLE_SKU_ID) or "") or None,
                attributs_sku=str(sku.get(CLE_SKU_ATTRIBUTS) or ""),
                attributs_lisibles=_attributs_lisibles(sku),
                prix_base=prix_base,
                prix_vente=prix_vente,
                devise=devise_sku,
                remise_pourcentage=remise,
                stock_disponible=_entier(sku.get(CLE_SKU_STOCK)),
            )
        )

    titre = str(infos.get(CLE_SUJET) or "").strip()
    if not titre and not skus:
        return None, anomalies, limites

    produit = ProduitDetaille(
        item_id=item_id,
        titre=titre,
        nb_ventes=_entier(infos.get(CLE_NB_VENTES)),
        note_moyenne=_flottant(infos.get(CLE_NOTE_MOYENNE)),
        nb_evaluations=_entier(infos.get(CLE_NB_EVALUATIONS)),
        statut_produit=str(infos.get(CLE_STATUT_PRODUIT) or "") or None,
        delai_livraison_jours=_entier(logistique.get(CLE_DELAI_LIVRAISON)),
        skus=skus,
        contexte=construire_contexte(marche, ETAPE_DETAIL, horodatage, pays_confirme),
    )
    return produit, anomalies, limites


# --------------------------------------------------------------------------- #
# Statistiques
# --------------------------------------------------------------------------- #


def _resume(valeurs: list[float]) -> tuple[float | None, float | None, float | None]:
    """Calcule le minimum, la médiane et le maximum d'une série.

    Args:
        valeurs: Série de prix.

    Returns:
        Le triplet `(min, médiane, max)`, tout à `None` si la série est vide.
    """
    if not valeurs:
        return None, None, None
    return (
        round(min(valeurs), 2),
        round(statistics.median(valeurs), 2),
        round(max(valeurs), 2),
    )


def calculer_stats(
    marche: ParametresMarche,
    produits: list[ProduitRecherche],
    nb_retenus: int,
    produits_detailles: list[ProduitDetaille],
    total_annonce_par_requete: dict[str, int],
    nb_appels_api: int,
) -> StatsCollecte:
    """Assemble les statistiques descriptives de la collecte.

    Toutes les valeurs monétaires sont exprimées dans la devise d'étude, aucune
    n'est convertie.

    Args:
        marche: Région d'étude.
        produits: Produits dédoublonnés de la phase A.
        nb_retenus: Nombre de produits sélectionnés pour la phase B.
        produits_detailles: Produits effectivement détaillés.
        total_annonce_par_requete: Totaux annoncés par la passerelle.
        nb_appels_api: Appels métier réellement émis.

    Returns:
        Les statistiques de collecte.
    """
    prix_annonce = [produit.prix_vente for produit in produits]
    prix_skus = [
        sku.prix_vente for produit in produits_detailles for sku in produit.skus
    ]
    minimum, mediane, maximum = _resume(prix_annonce)
    sku_min, sku_median, sku_max = _resume(prix_skus)

    return StatsCollecte(
        devise=marche.devise,
        nb_produits_recherche=len(produits),
        nb_produits_retenus=nb_retenus,
        nb_produits_detailles=len(produits_detailles),
        nb_skus=len(prix_skus),
        prix_vente_min=minimum,
        prix_vente_median=mediane,
        prix_vente_max=maximum,
        prix_sku_min=sku_min,
        prix_sku_median=sku_median,
        prix_sku_max=sku_max,
        total_annonce_par_requete=total_annonce_par_requete,
        nb_appels_api=nb_appels_api,
    )
