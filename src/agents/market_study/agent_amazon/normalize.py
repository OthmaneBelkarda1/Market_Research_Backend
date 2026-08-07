"""Normalisation des items bruts et statistiques descriptives du corpus.

Aucun appel LLM, aucun effet de bord : toutes les fonctions de ce module sont
pures.

Le mapping des items bruts vers `ProduitAmazon` et `Avis` repose exclusivement
sur le schéma **constaté** dans les datasets des deux actors, relevé dans le
README. Les noms de champs sont centralisés dans `config`.
"""

from __future__ import annotations

import re
from collections import Counter
from statistics import fmean, median

from config import (
    CLE_ASIN,
    CLE_AVIS_DATE,
    CLE_AVIS_DATE_REPLI,
    CLE_AVIS_NOTE,
    CLE_AVIS_REACTION,
    CLE_AVIS_TEXTE,
    CLE_AVIS_TITRE,
    CLE_AVIS_VERIFIE,
    CLE_CHOIX_AMAZON,
    CLE_EN_STOCK,
    CLE_ERREUR,
    CLE_IMAGE,
    CLE_LIVRAISON,
    CLE_MARQUE,
    CLE_NB_AVIS,
    CLE_NOTE,
    CLE_PRIX,
    CLE_PRIX_BARRE,
    CLE_PRIX_DEVISE,
    CLE_PRIX_VALEUR,
    CLE_RANG_CATEGORIE,
    CLE_RANG_VALEUR,
    CLE_RANGS,
    CLE_TITRE,
    CLE_URL,
    CLE_VENDEUR,
    CLE_VENDEUR_ETOILES,
    CLE_VENDEUR_NB_NOTES,
    CLE_VENDEUR_NOM,
    CLE_VENDEUR_NOTE_GLOBALE,
    CLE_VOLUME_ACHATS,
    MOTIF_ETOILES_TITRE,
    obtenir_logger,
)
from schemas import Avis, ProduitAmazon, RecherchePlanifiee, StatsCollecte

_LOG = obtenir_logger(__name__)

_MOTIF_ETOILES = re.compile(MOTIF_ETOILES_TITRE, re.IGNORECASE)

TITRE_MANQUANT: str = "(titre absent)"
CORRESPONDANCE_NON_CLASSIFIEE: str = "non_classifie"
MARQUE_INCONNUE: str = "inconnue"
SUFFIXE_VOTES_UTILES: str = "personne(s) ont trouvé cet avis utile"


# --------------------------------------------------------------------------- #
# Conversions élémentaires
# --------------------------------------------------------------------------- #


def _texte_ou_none(valeur: object) -> str | None:
    """Nettoie un champ texte optionnel.

    Args:
        valeur: Valeur brute issue du dataset.

    Returns:
        Le texte débarrassé de ses espaces de bord, ou `None` s'il est vide.
    """
    if not isinstance(valeur, str):
        return None
    texte = valeur.strip()
    return texte or None


def _nombre_ou_none(valeur: object) -> float | None:
    """Convertit une valeur brute en nombre à virgule lorsque c'est possible.

    Args:
        valeur: Valeur brute issue du dataset.

    Returns:
        Le nombre correspondant, ou `None` si la conversion est impossible.
    """
    if isinstance(valeur, bool) or valeur is None:
        return None
    if isinstance(valeur, (int, float)):
        return float(valeur)
    return None


def _entier_ou_none(valeur: object) -> int | None:
    """Convertit une valeur brute en entier lorsque c'est possible.

    Args:
        valeur: Valeur brute issue du dataset.

    Returns:
        L'entier correspondant, ou `None` si la conversion est impossible.
    """
    nombre = _nombre_ou_none(valeur)
    return None if nombre is None else int(nombre)


def _sous_objet(item: dict, cle: str) -> dict:
    """Récupère un sous-objet d'un item brut.

    Args:
        item: Item brut du dataset.
        cle: Clé du sous-objet attendu.

    Returns:
        Le sous-objet, ou un dictionnaire vide s'il est absent ou mal typé.
    """
    valeur = item.get(cle)
    return valeur if isinstance(valeur, dict) else {}


def _prix(valeur: object) -> tuple[float | None, str | None]:
    """Décompose un champ de prix en montant et devise.

    L'actor renvoie normalement `{"value": 25.99, "currency": "$"}`, mais un
    nombre nu est également accepté — la devise est alors inconnue.

    Args:
        valeur: Champ de prix brut.

    Returns:
        Un couple `(montant, devise)`.
    """
    if isinstance(valeur, dict):
        return (
            _nombre_ou_none(valeur.get(CLE_PRIX_VALEUR)),
            _texte_ou_none(valeur.get(CLE_PRIX_DEVISE)),
        )
    return _nombre_ou_none(valeur), None


# --------------------------------------------------------------------------- #
# Produits
# --------------------------------------------------------------------------- #


def est_enregistrement_erreur(item: dict) -> bool:
    """Indique si un item est un enregistrement d'erreur de l'actor.

    L'actor écrit `{"error": "no_results_found", ...}` dans le dataset au lieu
    de faire échouer le run : ces enregistrements ne sont pas des produits.

    Args:
        item: Item brut du dataset.

    Returns:
        Vrai s'il s'agit d'un enregistrement d'erreur.
    """
    return bool(item.get(CLE_ERREUR))


def compter_erreurs(items: list[dict]) -> int:
    """Compte les enregistrements d'erreur d'un dataset.

    Args:
        items: Items bruts renvoyés par un run.

    Returns:
        Le nombre d'enregistrements d'erreur.
    """
    return sum(1 for item in items if est_enregistrement_erreur(item))


def _vendeur(item: dict) -> tuple[str | None, float | None, int | None]:
    """Extrait l'identité et la réputation du vendeur.

    `scrapeSellers=True` transforme le champ en profil complet ; sans l'option,
    c'est une simple chaîne. Les deux formes sont acceptées.

    Args:
        item: Item brut du dataset.

    Returns:
        Un triplet `(nom, note_globale, nombre_de_notes)`.
    """
    brut = item.get(CLE_VENDEUR)
    if isinstance(brut, str):
        return _texte_ou_none(brut), None, None
    if not isinstance(brut, dict):
        return None, None, None

    note_globale = _sous_objet(brut, CLE_VENDEUR_NOTE_GLOBALE)
    return (
        _texte_ou_none(brut.get(CLE_VENDEUR_NOM)),
        _nombre_ou_none(note_globale.get(CLE_VENDEUR_ETOILES)),
        _entier_ou_none(note_globale.get(CLE_VENDEUR_NB_NOTES)),
    )


def _meilleur_rang(item: dict) -> tuple[int | None, str | None]:
    """Retient le rang Best Sellers le plus significatif d'une fiche.

    Amazon en affiche souvent plusieurs, du plus large au plus étroit. Le
    premier est conservé : c'est celui de la catégorie la plus large, donc le
    seul qui situe le produit sur son marché plutôt que dans une niche.

    Args:
        item: Item brut du dataset.

    Returns:
        Un couple `(rang, categorie)`, nuls si aucun rang n'est affiché.
    """
    rangs = item.get(CLE_RANGS)
    if not isinstance(rangs, list):
        return None, None
    for rang in rangs:
        if not isinstance(rang, dict):
            continue
        valeur = _entier_ou_none(rang.get(CLE_RANG_VALEUR))
        if valeur is not None:
            return valeur, _texte_ou_none(rang.get(CLE_RANG_CATEGORIE))
    return None, None


def normaliser_produits(
    items: list[dict], recherche: RecherchePlanifiee
) -> list[ProduitAmazon]:
    """Convertit les items bruts d'un run en produits normalisés.

    Les enregistrements d'erreur sont ignorés ; leur décompte s'obtient
    séparément avec `compter_erreurs`.

    Args:
        items: Items bruts renvoyés par l'actor.
        recherche: Recherche à l'origine du run, reportée sur chaque produit.

    Returns:
        Les produits normalisés, dans l'ordre du dataset — donc dans l'ordre du
        classement d'Amazon pour le tri demandé.
    """
    produits: list[ProduitAmazon] = []
    rang = 0

    for item in items:
        if est_enregistrement_erreur(item):
            continue

        titre = _texte_ou_none(item.get(CLE_TITRE))
        if not titre:
            _LOG.info("Item sans titre conservé sous un libellé de substitution.")

        rang += 1
        prix, devise = _prix(item.get(CLE_PRIX))
        prix_barre, devise_barre = _prix(item.get(CLE_PRIX_BARRE))
        nom_vendeur, note_vendeur, nb_notes_vendeur = _vendeur(item)
        rang_best_seller, categorie_best_seller = _meilleur_rang(item)

        produits.append(
            ProduitAmazon(
                asin=_texte_ou_none(item.get(CLE_ASIN)),
                titre=titre or TITRE_MANQUANT,
                url=_texte_ou_none(item.get(CLE_URL)),
                image=_texte_ou_none(item.get(CLE_IMAGE)),
                prix=prix,
                devise=devise or devise_barre,
                prix_barre=prix_barre,
                note=_nombre_ou_none(item.get(CLE_NOTE)),
                nb_avis=_entier_ou_none(item.get(CLE_NB_AVIS)),
                volume_achats_mensuel=_texte_ou_none(item.get(CLE_VOLUME_ACHATS)),
                marque=_texte_ou_none(item.get(CLE_MARQUE)),
                vendeur=nom_vendeur,
                note_vendeur=note_vendeur,
                nb_notes_vendeur=nb_notes_vendeur,
                choix_amazon=bool(item.get(CLE_CHOIX_AMAZON)),
                rang_best_seller=rang_best_seller,
                categorie_best_seller=categorie_best_seller,
                disponible=item.get(CLE_EN_STOCK) if isinstance(item.get(CLE_EN_STOCK), bool) else None,
                livraison=_texte_ou_none(item.get(CLE_LIVRAISON)),
                recherche_origine=recherche.mots_cles,
                rang_collecte=rang,
                correspondance=None,
                pertinence=None,
                avis=[],
            )
        )
    return produits


# --------------------------------------------------------------------------- #
# Avis
# --------------------------------------------------------------------------- #


def normaliser_avis(items: list[dict]) -> list[Avis]:
    """Convertit les items bruts d'un run d'avis en avis normalisés.

    Les items sans texte sont écartés : un avis vide n'apporte rien à une
    analyse qualitative, et sa note est déjà comptée dans la note moyenne du
    produit.

    Args:
        items: Items bruts renvoyés par l'actor d'avis.

    Returns:
        Les avis exploitables, dans l'ordre du dataset.
    """
    avis: list[Avis] = []

    for item in items:
        texte = _texte_ou_none(item.get(CLE_AVIS_TEXTE))
        if not texte:
            continue

        titre = _texte_ou_none(item.get(CLE_AVIS_TITRE))
        if titre:
            # Le titre arrive préfixé de la ligne d'étoiles ; la note est déjà
            # un champ à part.
            titre = _texte_ou_none(_MOTIF_ETOILES.sub("", titre))

        reaction = item.get(CLE_AVIS_REACTION)
        votes = _texte_ou_none(str(reaction)) if reaction is not None else None
        if votes and votes.isdigit():
            votes = f"{votes} {SUFFIXE_VOTES_UTILES}"

        avis.append(
            Avis(
                note=_entier_ou_none(item.get(CLE_AVIS_NOTE)),
                titre=titre,
                texte=texte,
                date=(
                    _texte_ou_none(item.get(CLE_AVIS_DATE))
                    or _texte_ou_none(item.get(CLE_AVIS_DATE_REPLI))
                ),
                achat_verifie=(
                    item.get(CLE_AVIS_VERIFIE)
                    if isinstance(item.get(CLE_AVIS_VERIFIE), bool)
                    else None
                ),
                votes_utiles=votes,
            )
        )
    return avis


# --------------------------------------------------------------------------- #
# Statistiques
# --------------------------------------------------------------------------- #


def _devise_dominante(produits: list[ProduitAmazon]) -> str | None:
    """Détermine la devise majoritaire du corpus.

    Args:
        produits: Produits du corpus final.

    Returns:
        La devise la plus fréquente, ou `None` si aucune n'est renseignée.
    """
    devises = Counter(produit.devise for produit in produits if produit.devise)
    return devises.most_common(1)[0][0] if devises else None


def calculer_stats(
    nb_produits_collectes: int,
    produits_retenus: list[ProduitAmazon],
    nb_doublons_ecartes: int,
    nb_produits_hors_criteres: int,
    nb_produits_sous_seuil: int,
    nb_produits_non_classifies: int,
    nb_enregistrements_erreur: int,
) -> StatsCollecte:
    """Calcule les statistiques descriptives du corpus.

    Les indicateurs de prix ne sont PAS convertis : ils n'ont de sens que dans
    la devise de la marketplace interrogée.

    Args:
        nb_produits_collectes: Produits renvoyés par tous les runs, avant
            filtrage.
        produits_retenus: Produits du corpus final.
        nb_doublons_ecartes: Produits écartés comme doublons.
        nb_produits_hors_criteres: Produits écartés par les critères du plan.
        nb_produits_sous_seuil: Produits écartés par le seuil de pertinence.
        nb_produits_non_classifies: Produits conservés sans étiquetage LLM.
        nb_enregistrements_erreur: Enregistrements `error` émis par l'actor.

    Returns:
        Les statistiques du corpus.
    """
    prix = sorted(produit.prix for produit in produits_retenus if produit.prix is not None)
    notes = [produit.note for produit in produits_retenus if produit.note is not None]
    avis = [avis for produit in produits_retenus for avis in produit.avis]

    return StatsCollecte(
        nb_produits_collectes=nb_produits_collectes,
        nb_produits_retenus=len(produits_retenus),
        nb_produits_avec_avis=sum(1 for produit in produits_retenus if produit.avis),
        nb_avis_collectes=len(avis),
        nb_doublons_ecartes=nb_doublons_ecartes,
        nb_produits_hors_criteres=nb_produits_hors_criteres,
        nb_produits_sous_seuil=nb_produits_sous_seuil,
        nb_produits_non_classifies=nb_produits_non_classifies,
        nb_enregistrements_erreur=nb_enregistrements_erreur,
        prix_min=prix[0] if prix else None,
        prix_median=round(median(prix), 2) if prix else None,
        prix_max=prix[-1] if prix else None,
        devise=_devise_dominante(produits_retenus),
        note_moyenne=round(fmean(notes), 2) if notes else None,
        repartition_par_correspondance=dict(
            Counter(
                produit.correspondance or CORRESPONDANCE_NON_CLASSIFIEE
                for produit in produits_retenus
            ).most_common()
        ),
        repartition_par_marque=dict(
            Counter(
                produit.marque or MARQUE_INCONNUE for produit in produits_retenus
            ).most_common()
        ),
        repartition_par_recherche=dict(
            Counter(produit.recherche_origine for produit in produits_retenus).most_common()
        ),
    )
