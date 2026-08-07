"""Normalisation des items bruts et statistiques descriptives du corpus.

Aucun appel LLM, aucun effet de bord : toutes les fonctions de ce module sont
pures — à une exception près, `duree_diffusion_jours`, qui a besoin de la date
du jour pour mesurer l'ancienneté d'une annonce encore diffusée.

Le mapping des items bruts vers `Annonce` repose exclusivement sur le schéma
**constaté** dans le dataset de l'actor, relevé dans le README. Les noms de
champs sont centralisés dans `config`.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from statistics import median

from config import (
    CLE_ACTIVE,
    CLE_ANNONCEUR,
    CLE_COLLATION,
    CLE_COLLATION_NB,
    CLE_CORPS,
    CLE_CORPS_TEXTE,
    CLE_CTA,
    CLE_DATE_DEBUT,
    CLE_DATE_DEBUT_ISO,
    CLE_DATE_FIN,
    CLE_DATE_FIN_ISO,
    CLE_DEPENSE,
    CLE_DESCRIPTION_LIEN,
    CLE_DEVISE,
    CLE_FORMAT,
    CLE_ID_ANNONCE,
    CLE_ID_ANNONCE_ALT,
    CLE_ID_ANNONCEUR,
    CLE_ID_ANNONCEUR_ALT,
    CLE_IMAGES,
    CLE_LEGENDE,
    CLE_LIEN,
    CLE_PLATEFORMES,
    CLE_PORTEE,
    CLE_SNAPSHOT,
    CLE_TITRE,
    CLE_VIDEOS,
    CLES_IMAGE,
    CLES_VIDEO,
    FORMATS_META,
    MEDIA_IMAGE,
    MEDIA_INCONNU,
    MEDIA_VIDEO,
    PARAM_IDENTIFIANT,
    URL_BIBLIOTHEQUE,
    obtenir_logger,
)
from schemas import Annonce, RecherchePlanifiee, StatsCollecte

_LOG = obtenir_logger(__name__)

ANNONCEUR_INCONNU: str = "annonceur inconnu"
CORRESPONDANCE_NON_CLASSIFIEE: str = "non_classifie"
CTA_ABSENT: str = "sans appel à l'action"
LONGUEUR_DATE_ISO: int = 10
"""Longueur du préfixe « AAAA-MM-JJ » d'une date ISO."""


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
    if isinstance(valeur, str):
        texte = valeur.strip()
        return texte or None
    if isinstance(valeur, (int, float)) and not isinstance(valeur, bool):
        return str(valeur)
    return None


def _entier_ou_none(valeur: object) -> int | None:
    """Convertit une valeur brute en entier lorsque c'est possible.

    Args:
        valeur: Valeur brute issue du dataset.

    Returns:
        L'entier correspondant, ou `None` si la conversion est impossible.
    """
    if isinstance(valeur, bool) or valeur is None:
        return None
    if isinstance(valeur, (int, float)):
        return int(valeur)
    return None


def _booleen_ou_none(valeur: object) -> bool | None:
    """Convertit une valeur brute en booléen strict.

    Args:
        valeur: Valeur brute issue du dataset.

    Returns:
        Le booléen, ou `None` si le champ n'en était pas un.
    """
    return valeur if isinstance(valeur, bool) else None


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


def _premiere_valeur(item: dict, *cles: str) -> str | None:
    """Retourne le premier champ renseigné parmi plusieurs orthographes.

    L'actor a servi certains identifiants tantôt en `…ID`, tantôt en `…Id`.

    Args:
        item: Item brut du dataset.
        *cles: Clés à essayer, dans l'ordre.

    Returns:
        La première valeur non vide, ou `None`.
    """
    for cle in cles:
        valeur = _texte_ou_none(item.get(cle))
        if valeur:
            return valeur
    return None


def _date_iso(item: dict, cle_iso: str, cle_horodatage: str) -> str | None:
    """Extrait une date au format « AAAA-MM-JJ ».

    Les champs `…Formatted` sont préférés ; l'horodatage Unix ne sert que de
    repli, l'actor ne renseignant pas toujours les deux.

    Args:
        item: Item brut du dataset.
        cle_iso: Clé du champ ISO.
        cle_horodatage: Clé du champ horodatage Unix.

    Returns:
        La date, ou `None` si aucune n'est exploitable.
    """
    iso = item.get(cle_iso)
    if isinstance(iso, str) and len(iso) >= LONGUEUR_DATE_ISO:
        return iso[:LONGUEUR_DATE_ISO]

    horodatage = item.get(cle_horodatage)
    if isinstance(horodatage, (int, float)) and not isinstance(horodatage, bool):
        if horodatage > 0:
            return datetime.fromtimestamp(horodatage, tz=timezone.utc).strftime("%Y-%m-%d")
    return None


def _duree_jours(debut: str | None, fin: str | None) -> int | None:
    """Mesure la durée de diffusion d'une annonce, en jours.

    Une annonce sans date de fin est réputée encore diffusée : la durée est
    comptée jusqu'à la date du run. C'est le seul point non pur du module, et il
    rend le champ dépendant du moment de la collecte.

    Args:
        debut: Date ISO de début de diffusion.
        fin: Date ISO de fin, ou `None` si la diffusion se poursuit.

    Returns:
        La durée en jours, ou `None` si la date de début est inconnue ou
        illisible.
    """
    if not debut:
        return None
    try:
        depuis = date.fromisoformat(debut)
        jusqu_a = date.fromisoformat(fin) if fin else datetime.now(tz=timezone.utc).date()
    except ValueError:
        _LOG.info("Dates de diffusion illisibles ignorées : %s → %s", debut, fin)
        return None
    return max(0, (jusqu_a - depuis).days)


# --------------------------------------------------------------------------- #
# Créatif
# --------------------------------------------------------------------------- #


def _corps(snapshot: dict) -> str | None:
    """Extrait le texte du créatif.

    Le champ `body` est servi tantôt comme objet `{"text": "…"}`, tantôt comme
    chaîne nue : les deux formes sont acceptées.

    Args:
        snapshot: Sous-objet créatif de l'item.

    Returns:
        Le texte de l'annonce, ou `None`.
    """
    corps = snapshot.get(CLE_CORPS)
    if isinstance(corps, dict):
        return _texte_ou_none(corps.get(CLE_CORPS_TEXTE))
    return _texte_ou_none(corps)


def _url_media(snapshot: dict, cle_liste: str, cles_url: tuple[str, ...]) -> str | None:
    """Extrait une URL de média utilisable du créatif.

    Args:
        snapshot: Sous-objet créatif de l'item.
        cle_liste: Clé de la liste de médias à parcourir.
        cles_url: Clés d'URL à essayer dans chaque média, par ordre de
            préférence.

    Returns:
        L'URL du premier média exploitable, ou `None`.
    """
    medias = snapshot.get(cle_liste)
    if not isinstance(medias, list):
        return None

    for media in medias:
        if isinstance(media, str):
            url = _texte_ou_none(media)
            if url:
                return url
        elif isinstance(media, dict):
            for cle in cles_url:
                url = _texte_ou_none(media.get(cle))
                if url:
                    return url
    return None


def _type_media(snapshot: dict) -> str:
    """Qualifie le média porteur du créatif.

    Le `displayFormat` déclaré par Meta fait foi : une annonce vidéo porte
    souvent aussi une liste d'images — sa vignette —, si bien que se fier à la
    seule présence des listes la ferait passer pour une image. Ces listes ne
    servent donc que de repli, pour les formats hors nomenclature.

    Args:
        snapshot: Sous-objet créatif de l'item.

    Returns:
        « video », « image » ou « inconnu ».
    """
    format_meta = _texte_ou_none(snapshot.get(CLE_FORMAT))
    if format_meta:
        type_media = FORMATS_META.get(format_meta.strip().upper())
        if type_media:
            return type_media

    if snapshot.get(CLE_VIDEOS):
        return MEDIA_VIDEO
    if snapshot.get(CLE_IMAGES):
        return MEDIA_IMAGE
    return MEDIA_INCONNU


def _plateformes(item: dict) -> list[str]:
    """Normalise la liste des plateformes de diffusion.

    Le champ est servi comme liste, mais une chaîne unique a été observée sur
    certaines annonces.

    Args:
        item: Item brut du dataset.

    Returns:
        Les plateformes en minuscules, sans doublon, dans l'ordre d'origine.
    """
    brut = item.get(CLE_PLATEFORMES)
    valeurs = brut if isinstance(brut, list) else [brut]

    plateformes: list[str] = []
    for valeur in valeurs:
        texte = _texte_ou_none(valeur)
        if texte and texte.casefold() not in plateformes:
            plateformes.append(texte.casefold())
    return plateformes


# --------------------------------------------------------------------------- #
# Annonces
# --------------------------------------------------------------------------- #


def normaliser_annonces(
    items: list[dict], recherche: RecherchePlanifiee
) -> list[Annonce]:
    """Convertit les items bruts d'un run en annonces normalisées.

    Args:
        items: Items bruts renvoyés par l'actor.
        recherche: Recherche à l'origine du run, reportée sur chaque annonce.

    Returns:
        Les annonces normalisées, dans l'ordre du dataset — donc dans l'ordre
        servi par la bibliothèque publicitaire, qui n'est pas documenté.
    """
    annonces: list[Annonce] = []

    for rang, item in enumerate(items, start=1):
        snapshot = _sous_objet(item, CLE_SNAPSHOT)
        identifiant = _premiere_valeur(item, CLE_ID_ANNONCE, CLE_ID_ANNONCE_ALT)
        debut = _date_iso(item, CLE_DATE_DEBUT_ISO, CLE_DATE_DEBUT)
        fin = _date_iso(item, CLE_DATE_FIN_ISO, CLE_DATE_FIN)

        annonces.append(
            Annonce(
                id_annonce=identifiant,
                url_bibliotheque=(
                    f"{URL_BIBLIOTHEQUE}?{PARAM_IDENTIFIANT}={identifiant}"
                    if identifiant
                    else None
                ),
                annonceur=(
                    _texte_ou_none(item.get(CLE_ANNONCEUR))
                    or _texte_ou_none(snapshot.get(CLE_ANNONCEUR))
                ),
                id_annonceur=_premiere_valeur(item, CLE_ID_ANNONCEUR, CLE_ID_ANNONCEUR_ALT),
                titre=_texte_ou_none(snapshot.get(CLE_TITRE)),
                texte=_corps(snapshot),
                description_lien=_texte_ou_none(snapshot.get(CLE_DESCRIPTION_LIEN)),
                legende=_texte_ou_none(snapshot.get(CLE_LEGENDE)),
                cta=_texte_ou_none(snapshot.get(CLE_CTA)),
                lien=_texte_ou_none(snapshot.get(CLE_LIEN)),
                image=_url_media(snapshot, CLE_IMAGES, CLES_IMAGE),
                video=_url_media(snapshot, CLE_VIDEOS, CLES_VIDEO),
                type_media=_type_media(snapshot),
                id_collation=_texte_ou_none(item.get(CLE_COLLATION)),
                nb_declinaisons=_entier_ou_none(item.get(CLE_COLLATION_NB)),
                plateformes=_plateformes(item),
                active=_booleen_ou_none(item.get(CLE_ACTIVE)),
                date_debut=debut,
                date_fin=fin,
                duree_diffusion_jours=_duree_jours(debut, fin),
                portee_estimee=_texte_ou_none(item.get(CLE_PORTEE)),
                depense=_texte_ou_none(item.get(CLE_DEPENSE)),
                devise=_texte_ou_none(item.get(CLE_DEVISE)),
                recherche_origine=recherche.mots_cles,
                rang_collecte=rang,
                correspondance=None,
                pertinence=None,
            )
        )
    return annonces


# --------------------------------------------------------------------------- #
# Statistiques
# --------------------------------------------------------------------------- #


def calculer_stats(
    nb_annonces_collectees: int,
    annonces_retenues: list[Annonce],
    nb_doublons_ecartes: int,
    nb_doublons_creatif: int,
    nb_annonces_hors_criteres: int,
    nb_annonces_sous_seuil: int,
    nb_annonces_non_classifiees: int,
) -> StatsCollecte:
    """Calcule les statistiques descriptives du corpus.

    Aucun de ces indicateurs n'est une mesure de marché : ils décrivent le
    corpus collecté, qui est plafonné par recherche et servi dans un ordre non
    documenté.

    Args:
        nb_annonces_collectees: Annonces renvoyées par tous les runs, avant
            filtrage.
        annonces_retenues: Annonces du corpus final.
        nb_doublons_ecartes: Annonces écartées comme doublons d'identifiant.
        nb_doublons_creatif: Annonces écartées comme reprises d'un créatif.
        nb_annonces_hors_criteres: Annonces écartées par le statut demandé ou
            faute de contenu exploitable.
        nb_annonces_sous_seuil: Annonces écartées par le seuil de pertinence.
        nb_annonces_non_classifiees: Annonces conservées sans étiquetage LLM.

    Returns:
        Les statistiques du corpus.
    """
    durees = [
        annonce.duree_diffusion_jours
        for annonce in annonces_retenues
        if annonce.duree_diffusion_jours is not None
    ]
    annonceurs = {
        (annonce.annonceur or ANNONCEUR_INCONNU).casefold() for annonce in annonces_retenues
    }

    return StatsCollecte(
        nb_annonces_collectees=nb_annonces_collectees,
        nb_annonces_retenues=len(annonces_retenues),
        nb_annonceurs=len(annonceurs),
        nb_annonces_actives=sum(1 for annonce in annonces_retenues if annonce.active),
        nb_doublons_ecartes=nb_doublons_ecartes,
        nb_doublons_creatif=nb_doublons_creatif,
        nb_annonces_hors_criteres=nb_annonces_hors_criteres,
        nb_annonces_sous_seuil=nb_annonces_sous_seuil,
        nb_annonces_non_classifiees=nb_annonces_non_classifiees,
        duree_diffusion_mediane_jours=round(median(durees), 1) if durees else None,
        duree_diffusion_max_jours=max(durees) if durees else None,
        repartition_par_correspondance=dict(
            Counter(
                annonce.correspondance or CORRESPONDANCE_NON_CLASSIFIEE
                for annonce in annonces_retenues
            ).most_common()
        ),
        repartition_par_annonceur=dict(
            Counter(
                annonce.annonceur or ANNONCEUR_INCONNU for annonce in annonces_retenues
            ).most_common()
        ),
        repartition_par_plateforme=dict(
            Counter(
                plateforme
                for annonce in annonces_retenues
                for plateforme in annonce.plateformes
            ).most_common()
        ),
        repartition_par_cta=dict(
            Counter(annonce.cta or CTA_ABSENT for annonce in annonces_retenues).most_common()
        ),
        repartition_par_recherche=dict(
            Counter(annonce.recherche_origine for annonce in annonces_retenues).most_common()
        ),
    )
