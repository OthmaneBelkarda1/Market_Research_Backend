"""Construction du référentiel : offres, annonces, pages et avis indexés.

**Aucun appel LLM dans ce module.** Le référentiel est la source de vérité de
toute preuve citable par la suite : un identifiant qui n'y figure pas sera
retiré par la post-validation.
"""

from __future__ import annotations

import re

from config import (
    AXE_CONCURRENCE,
    CORRESPONDANCE_ACCESSOIRE,
    CORRESPONDANCE_HORS_SUJET,
    CORRESPONDANCES_COEUR_AMAZON,
    CORRESPONDANCES_COEUR_META,
    MAX_AVIS_PREUVE_PAR_OFFRE,
    MAX_CARACTERES_EXTRAIT_PAGE,
    MAX_PAGES_REFERENTIEL,
    MULTIPLICATEURS_VOLUME,
    SEUIL_PERTINENCE_AMONT,
    SOURCE_ALIEXPRESS,
    SOURCE_AMAZON,
    logger,
    normaliser_devise,
)
from schemas import (
    AnnonceConcurrente,
    AvisIndexe,
    EntreesChargees,
    OffreConcurrente,
    PageConcurrence,
    Referentiel,
    ReferentielStats,
)

MOTIF_HORS_SUJET: str = "hors_sujet"
MOTIF_PERTINENCE: str = "pertinence_insuffisante"
MOTIF_NON_CLASSE: str = "correspondance_absente"
MOTIF_AXE: str = "axe_non_servi"
MOTIF_SANS_TEXTE: str = "texte_absent"


_MENTION_VOLUME = re.compile(r"(\d+(?:[.,]\d+)?)\s*([KkMm])?\s*\+?")


def _volume_plancher(mention: str | int | None) -> int | None:
    """Convertit une mention de volume Amazon en plancher entier.

    Amazon publie « 1K+ bought in past month » : une borne inférieure de palier,
    jamais un volume exact. La valeur retournée est ce plancher.

    Args:
        mention: Mention brute, entier déjà normalisé, ou `None`.

    Returns:
        Le plancher entier, ou `None` si la mention n'est pas exploitable.
    """
    if isinstance(mention, int):
        return max(0, mention)
    if not isinstance(mention, str):
        return None
    trouve = _MENTION_VOLUME.search(mention)
    if not trouve:
        return None
    valeur = float(trouve.group(1).replace(",", "."))
    suffixe = (trouve.group(2) or "").upper()
    return int(valeur * MULTIPLICATEURS_VOLUME.get(suffixe, 1))


def _pertinence_acceptee(pertinence: float | None) -> bool:
    """Applique le seuil de pertinence amont, en acceptant l'absence de score.

    Args:
        pertinence: Score amont ou `None`.

    Returns:
        Vrai si l'élément est conservé.
    """
    return pertinence is None or pertinence >= SEUIL_PERTINENCE_AMONT


def _offres_aliexpress(
    entrees: EntreesChargees, exclusions: dict[str, int]
) -> list[OffreConcurrente]:
    """Construit les offres AliExpress en fusionnant recherche et détail.

    Le prix de référence est celui de l'annonce (`prix_vente` de la phase de
    recherche) : c'est le prix affiché à l'acheteur avant choix de déclinaison.
    Les prix de SKU sont conservés à part pour les fourchettes.

    Args:
        entrees: Entrées chargées.
        exclusions: Compteurs d'exclusion, enrichis sur place.

    Returns:
        Les offres AliExpress du référentiel.
    """
    if entrees.aliexpress is None:
        return []

    details = {d.item_id: d for d in entrees.aliexpress.produits_detailles}
    offres: list[OffreConcurrente] = []

    for produit in entrees.aliexpress.produits:
        detail = details.get(produit.item_id)
        prix_skus = [
            sku.prix_vente
            for sku in (detail.skus if detail else [])
            if sku.prix_vente is not None
        ]
        devise = normaliser_devise(
            produit.devise
            or (produit.contexte.devise if produit.contexte else None)
            or (entrees.aliexpress.stats.devise if entrees.aliexpress.stats else None)
        )
        badges: list[str] = []
        if detail and detail.delai_livraison_jours is not None:
            badges.append(f"livraison_{detail.delai_livraison_jours}j")

        offres.append(
            OffreConcurrente(
                id_offre=f"ax-{produit.item_id}",
                source=SOURCE_ALIEXPRESS,
                titre=produit.titre,
                marque=None,
                prix=produit.prix_vente,
                devise=devise,
                prix_barre=produit.prix_original,
                note=(detail.note_moyenne if detail else None) or produit.note,
                nb_avis_ou_evaluations=(
                    detail.nb_evaluations if detail else None
                ),
                volume_ventes=(
                    (detail.nb_ventes if detail else None)
                    if (detail and detail.nb_ventes is not None)
                    else produit.nb_commandes
                ),
                badges=badges,
                url=produit.url_produit,
                correspondance=None,
                est_accessoire=False,
                prix_skus=prix_skus,
            )
        )
    return offres


def _offres_amazon(
    entrees: EntreesChargees, exclusions: dict[str, int]
) -> tuple[list[OffreConcurrente], list[AvisIndexe]]:
    """Construit les offres Amazon retenues et indexe leurs avis.

    Args:
        entrees: Entrées chargées.
        exclusions: Compteurs d'exclusion, enrichis sur place.

    Returns:
        Le couple `(offres, avis_indexes)`.
    """
    if entrees.amazon is None:
        return [], []

    offres: list[OffreConcurrente] = []
    avis_indexes: list[AvisIndexe] = []

    for produit in entrees.amazon.produits:
        correspondance = (produit.correspondance or "").strip()
        if correspondance == CORRESPONDANCE_HORS_SUJET:
            exclusions[MOTIF_HORS_SUJET] = exclusions.get(MOTIF_HORS_SUJET, 0) + 1
            continue
        if not correspondance:
            exclusions[MOTIF_NON_CLASSE] = exclusions.get(MOTIF_NON_CLASSE, 0) + 1
        if not _pertinence_acceptee(produit.pertinence):
            exclusions[MOTIF_PERTINENCE] = exclusions.get(MOTIF_PERTINENCE, 0) + 1
            continue

        est_accessoire = correspondance == CORRESPONDANCE_ACCESSOIRE
        badges: list[str] = []
        if produit.choix_amazon:
            badges.append("choix_amazon")
        if produit.rang_best_seller is not None:
            badges.append(f"best_seller_rang_{produit.rang_best_seller}")

        identifiant = f"amz-{produit.asin}"
        offres.append(
            OffreConcurrente(
                id_offre=identifiant,
                source=SOURCE_AMAZON,
                titre=produit.titre,
                marque=(produit.marque or "").strip() or None,
                prix=produit.prix,
                devise=normaliser_devise(produit.devise),
                prix_barre=produit.prix_barre,
                note=produit.note,
                nb_avis_ou_evaluations=produit.nb_avis,
                volume_ventes=_volume_plancher(produit.volume_achats_mensuel),
                badges=badges,
                url=produit.url,
                correspondance=correspondance or None,
                est_accessoire=est_accessoire,
                asin=produit.asin,
            )
        )
        for index, avis in enumerate(produit.avis[:MAX_AVIS_PREUVE_PAR_OFFRE]):
            texte = "\n".join(p for p in (avis.titre or "", avis.texte or "") if p).strip()
            if not texte:
                continue
            avis_indexes.append(
                AvisIndexe(
                    id_avis=f"amz-{produit.asin}-avis-{index}",
                    id_offre=identifiant,
                    note=avis.note,
                    titre=avis.titre,
                    texte=texte,
                )
            )
    return offres, avis_indexes


def _annonces(
    entrees: EntreesChargees, exclusions: dict[str, int]
) -> list[AnnonceConcurrente]:
    """Construit les annonces retenues, texte publicitaire concaténé.

    `description_lien` porte souvent l'argumentaire complet là où `titre` se
    réduit à une accroche : les quatre champs sont concaténés.

    Args:
        entrees: Entrées chargées.
        exclusions: Compteurs d'exclusion, enrichis sur place.

    Returns:
        Les annonces du référentiel.
    """
    if entrees.meta_ads is None:
        return []

    annonces: list[AnnonceConcurrente] = []
    for annonce in entrees.meta_ads.annonces:
        correspondance = (annonce.correspondance or "").strip()
        if correspondance == CORRESPONDANCE_HORS_SUJET:
            exclusions[MOTIF_HORS_SUJET] = exclusions.get(MOTIF_HORS_SUJET, 0) + 1
            continue
        if correspondance and correspondance not in (
            CORRESPONDANCES_COEUR_META | {CORRESPONDANCE_ACCESSOIRE}
        ):
            exclusions[MOTIF_NON_CLASSE] = exclusions.get(MOTIF_NON_CLASSE, 0) + 1
        if not _pertinence_acceptee(annonce.pertinence):
            exclusions[MOTIF_PERTINENCE] = exclusions.get(MOTIF_PERTINENCE, 0) + 1
            continue

        texte = "\n".join(
            part
            for part in (
                annonce.titre or "",
                annonce.texte or "",
                annonce.description_lien or "",
                annonce.legende or "",
            )
            if part and part.strip()
        ).strip()
        if not texte:
            exclusions[MOTIF_SANS_TEXTE] = exclusions.get(MOTIF_SANS_TEXTE, 0) + 1
            continue

        annonces.append(
            AnnonceConcurrente(
                id_annonce=f"ads-{annonce.id_annonce}",
                annonceur=(annonce.annonceur or "").strip() or "annonceur inconnu",
                texte_complet=texte,
                cta=annonce.cta,
                plateformes=list(annonce.plateformes),
                active=annonce.active,
                duree_diffusion_jours=annonce.duree_diffusion_jours,
                nb_declinaisons=annonce.nb_declinaisons,
            )
        )
    return annonces


def _pages(entrees: EntreesChargees, exclusions: dict[str, int]) -> list[PageConcurrence]:
    """Construit les pages web servant l'axe concurrentiel.

    Args:
        entrees: Entrées chargées.
        exclusions: Compteurs d'exclusion, enrichis sur place.

    Returns:
        Les pages du référentiel, bornées par `MAX_PAGES_REFERENTIEL`.
    """
    if entrees.web is None:
        return []

    pages: list[PageConcurrence] = []
    for index, page in enumerate(entrees.web.pages):
        if AXE_CONCURRENCE not in (page.axes_servis or []):
            exclusions[MOTIF_AXE] = exclusions.get(MOTIF_AXE, 0) + 1
            continue
        if not _pertinence_acceptee(page.pertinence):
            exclusions[MOTIF_PERTINENCE] = exclusions.get(MOTIF_PERTINENCE, 0) + 1
            continue
        contenu = (page.contenu_markdown or "").strip()
        if not contenu:
            exclusions[MOTIF_SANS_TEXTE] = exclusions.get(MOTIF_SANS_TEXTE, 0) + 1
            continue
        pages.append(
            PageConcurrence(
                id_page=f"web-{index}",
                url=page.url,
                domaine=page.domaine or "",
                titre=page.titre,
                type_source=page.type_source,
                marques_detectees=[m.strip() for m in page.marques_detectees if m.strip()],
                extrait=contenu[:MAX_CARACTERES_EXTRAIT_PAGE],
            )
        )
    return pages[:MAX_PAGES_REFERENTIEL]


def construire_referentiel(entrees: EntreesChargees) -> Referentiel:
    """Construit le référentiel complet à partir des entrées chargées.

    Args:
        entrees: Entrées validées.

    Returns:
        Le référentiel et ses statistiques d'exclusion.
    """
    exclusions: dict[str, int] = {}
    offres_ax = _offres_aliexpress(entrees, exclusions)
    offres_amz, avis = _offres_amazon(entrees, exclusions)
    offres = offres_ax + offres_amz
    annonces = _annonces(entrees, exclusions)
    pages = _pages(entrees, exclusions)

    nb_coeur = sum(
        1
        for o in offres
        if not o.est_accessoire
        and (
            o.source == SOURCE_ALIEXPRESS
            or (o.correspondance or "") in CORRESPONDANCES_COEUR_AMAZON
        )
    )
    stats = ReferentielStats(
        nb_offres_par_source={
            SOURCE_ALIEXPRESS: len(offres_ax),
            SOURCE_AMAZON: len(offres_amz),
        },
        nb_offres_coeur=nb_coeur,
        nb_offres_accessoires=sum(1 for o in offres if o.est_accessoire),
        nb_annonces=len(annonces),
        nb_pages=len(pages),
        nb_avis_indexes=len(avis),
        exclusions=exclusions,
    )

    limites: list[str] = []
    if offres_ax and not any(o.correspondance for o in offres_ax):
        limites.append(
            "Les offres AliExpress ne portent aucune qualification de correspondance "
            "amont : contrairement à Amazon et Meta Ads, le collecteur AliExpress ne "
            "classe pas ses résultats. Elles sont toutes considérées comme relevant "
            "du cœur du benchmark, ce qui peut y faire entrer des produits éloignés."
        )
    if offres and all(o.volume_ventes is None for o in offres):
        limites.append(
            "Aucune offre ne porte de volume de ventes exploitable : la concentration "
            "et les classements par volume ne peuvent pas être calculés."
        )
    elif any(o.volume_ventes is not None for o in offres_amz):
        limites.append(
            "Les volumes de ventes Amazon sont des mentions par paliers "
            "(« 1K+ achetés le mois dernier ») : la valeur retenue est le PLANCHER du "
            "palier. Tout cumul de volumes est donc une borne inférieure, et les parts "
            "de concentration qui en découlent sont approximatives."
        )

    logger.debug(
        "référentiel : %d offres (%d cœur, %d accessoires), %d annonces, %d pages, "
        "%d avis ; exclusions %s",
        len(offres),
        nb_coeur,
        stats.nb_offres_accessoires,
        len(annonces),
        len(pages),
        len(avis),
        exclusions,
    )
    return Referentiel(
        offres=offres, annonces=annonces, pages=pages, avis=avis, stats=stats, limites=limites
    )
