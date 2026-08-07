"""Lecture des fichiers d'entrée, validation tolérante et contrôle de cohérence.

Règle centrale : **aucune exception n'est propagée pour une source**. Seule
l'incohérence de produit entre deux fichiers est bloquante.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from config import (
    PORTEE_DIFFUSION_PAYS,
    PORTEE_MARKETPLACE_PAYS,
    PORTEE_MIXTE,
    PORTEE_REGION_ETUDE,
    SOURCE_ALIEXPRESS,
    SOURCE_AMAZON,
    SOURCE_META_ADS,
    SOURCE_WEB,
    logger,
)
from schemas import (
    AlerteCoherence,
    EntreeAliExpress,
    EntreeAmazon,
    EntreeMetaAds,
    EntreeRechercheWeb,
    EntreesChargees,
    ErreurCoherenceProduit,
    SourceUtilisee,
    ValiditeRegionaleSource,
)

ENCODAGES_TESTES: tuple[str, ...] = ("utf-8-sig", "utf-16", "utf-8", "cp1252")
"""Encodages essayés dans l'ordre.

Les collecteurs qui émettent sur `stdout` sont redirigés vers un fichier par le
shell : sous PowerShell, cette redirection produit de l'UTF-16 avec BOM.
Constaté sur les sorties réelles d'AliExpress.
"""


def _normaliser_nom(valeur: str) -> str:
    """Normalise un nom de produit pour la comparaison inter-fichiers.

    Args:
        valeur: Nom brut.

    Returns:
        Le nom sans accents, en minuscules, espaces normalisés.
    """
    decompose = unicodedata.normalize("NFKD", valeur)
    sans_accent = "".join(c for c in decompose if not unicodedata.combining(c))
    return " ".join(sans_accent.lower().split())


def _lire_json(chemin: Path) -> Any:
    """Lit un fichier JSON quel que soit son encodage.

    Args:
        chemin: Chemin du fichier.

    Returns:
        L'objet Python décodé.

    Raises:
        ValueError: Si aucun encodage testé ne permet de décoder le contenu.
        OSError: Si le fichier est illisible.
    """
    brut = chemin.read_bytes()
    derniere: Exception | None = None
    for encodage in ENCODAGES_TESTES:
        try:
            return json.loads(brut.decode(encodage))
        except (UnicodeDecodeError, UnicodeError, json.JSONDecodeError) as erreur:
            derniere = erreur
    raise ValueError(
        f"contenu non décodable en JSON (encodages testés : "
        f"{', '.join(ENCODAGES_TESTES)}) — {derniere}"
    )


def _compter_items(entree: Any) -> int:
    """Compte les items bruts d'une entrée de collecteur.

    Args:
        entree: Entrée validée.

    Returns:
        Le nombre d'items du corpus brut.
    """
    if isinstance(entree, EntreeAliExpress):
        return len(entree.produits)
    if isinstance(entree, EntreeAmazon):
        return len(entree.produits)
    if isinstance(entree, EntreeMetaAds):
        return len(entree.annonces)
    if isinstance(entree, EntreeRechercheWeb):
        return len(entree.pages)
    return 0


def _charger_source(
    chemin: str | None, modele: type, nom_source: str
) -> tuple[Any | None, SourceUtilisee]:
    """Charge et valide un fichier de collecteur, sans jamais lever.

    Args:
        chemin: Chemin du fichier, ou `None`.
        modele: Classe Pydantic du schéma de consommation.
        nom_source: Nom court de la source.

    Returns:
        Le couple `(entree_ou_None, compte_rendu)`.
    """
    compte_rendu = SourceUtilisee(source=nom_source, fichier=chemin)
    if not chemin:
        return None, compte_rendu

    fichier = Path(chemin)
    if not fichier.is_file():
        compte_rendu.avertissements.append("fichier introuvable")
        logger.warning("[%s] fichier introuvable : %s", nom_source, chemin)
        return None, compte_rendu

    try:
        brut = _lire_json(fichier)
    except (OSError, ValueError) as erreur:
        compte_rendu.avertissements.append(f"lecture impossible : {erreur}")
        logger.warning("[%s] lecture impossible : %s", nom_source, erreur)
        return None, compte_rendu

    if not isinstance(brut, dict):
        compte_rendu.avertissements.append("racine JSON non objet")
        return None, compte_rendu

    try:
        entree = modele.model_validate(brut)
    except ValidationError as erreur:
        resume = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in erreur.errors()[:5]
        )
        compte_rendu.avertissements.append(f"structure non conforme : {resume}")
        logger.warning("[%s] structure non conforme : %s", nom_source, resume)
        return None, compte_rendu

    compte_rendu.nb_items_charges = _compter_items(entree)
    compte_rendu.donnees_disponibles = bool(entree.donnees_disponibles)
    if not entree.donnees_disponibles:
        compte_rendu.avertissements.append(
            "le collecteur déclare `donnees_disponibles=false` — source écartée"
        )
        return None, compte_rendu
    return entree, compte_rendu


def _controler_coherence(entrees: list[tuple[str, Any]]) -> list[AlerteCoherence]:
    """Compare les en-têtes produit/marché des fichiers chargés.

    Args:
        entrees: Couples `(nom_source, entree)` dans l'ordre de priorité.

    Returns:
        Les alertes non bloquantes.

    Raises:
        ErreurCoherenceProduit: Si deux fichiers portent des produits différents.
    """
    alertes: list[AlerteCoherence] = []
    if not entrees:
        return alertes
    source_ref, ref = entrees[0]
    nom_ref = _normaliser_nom(ref.produit.nom)

    for source, entree in entrees[1:]:
        if _normaliser_nom(entree.produit.nom) != nom_ref:
            raise ErreurCoherenceProduit(
                f"produits différents entre les fichiers d'entrée : "
                f"[{source_ref}] « {ref.produit.nom} » vs [{source}] "
                f"« {entree.produit.nom} ». Mélanger deux études est interdit."
            )
        if entree.produit.description.strip() != ref.produit.description.strip():
            alertes.append(
                AlerteCoherence(
                    type="produit_divergent",
                    detail=(
                        f"la description produit de [{source}] diffère de celle de "
                        f"[{source_ref}], qui fait foi."
                    ),
                )
            )
        if entree.marche.geo.strip().upper() != ref.marche.geo.strip().upper():
            alertes.append(
                AlerteCoherence(
                    type="marche_divergent",
                    detail=(
                        f"marché différent : [{source}] porte sur "
                        f"« {entree.marche.geo} », [{source_ref}] sur "
                        f"« {ref.marche.geo} ». Le benchmark mélange deux marchés."
                    ),
                )
            )
    return alertes


def _validite_regionale(
    entrees: EntreesChargees, alertes: list[AlerteCoherence]
) -> list[ValiditeRegionaleSource]:
    """Qualifie la portée régionale réelle de chaque source exploitée.

    Args:
        entrees: Entrées chargées.
        alertes: Liste d'alertes, enrichie en cas de ciblage non confirmé.

    Returns:
        Une entrée par source exploitée.
    """
    pays_etude = (entrees.marche.geo if entrees.marche else "").strip().upper()
    validites: list[ValiditeRegionaleSource] = []

    if entrees.aliexpress is not None:
        confirmes = {
            (p.contexte.pays_livraison_confirme or "").strip().upper()
            for p in entrees.aliexpress.produits
            if p.contexte and p.contexte.pays_livraison_confirme
        }
        demandes = {
            (p.contexte.pays_livraison or "").strip().upper()
            for p in entrees.aliexpress.produits
            if p.contexte and p.contexte.pays_livraison
        }
        divergent = bool(confirmes and demandes and confirmes != demandes)
        validites.append(
            ValiditeRegionaleSource(
                source=SOURCE_ALIEXPRESS,
                portee=PORTEE_MIXTE if divergent else PORTEE_REGION_ETUDE,
                commentaire=(
                    f"prix AliExpress pour une livraison en "
                    f"{', '.join(sorted(confirmes)) or 'pays non confirmé'}"
                    + (
                        f" alors que {', '.join(sorted(demandes))} était demandé : "
                        f"le ciblage régional n'est pas garanti."
                        if divergent
                        else "."
                    )
                ),
            )
        )
        if divergent:
            alertes.append(
                AlerteCoherence(
                    type="portee_regionale",
                    detail=(
                        "AliExpress a confirmé un pays de livraison différent de celui "
                        "demandé : les prix conservés ne décrivent pas nécessairement "
                        "la région d'étude."
                    ),
                )
            )

    if entrees.amazon is not None:
        pays_marketplace = (
            (entrees.amazon.marketplace.code_pays or "").strip().upper()
            if entrees.amazon.marketplace
            else ""
        )
        validites.append(
            ValiditeRegionaleSource(
                source=SOURCE_AMAZON,
                portee=PORTEE_MARKETPLACE_PAYS,
                commentaire=(
                    f"les prix et avis décrivent le marché de "
                    f"{entrees.amazon.marketplace.domaine if entrees.amazon.marketplace else 'la marketplace interrogée'}"
                    + (
                        f", soit {pays_marketplace}"
                        + (
                            " — c'est bien la région d'étude."
                            if pays_marketplace == pays_etude
                            else f", et non la région d'étude ({pays_etude})."
                        )
                        if pays_marketplace
                        else "."
                    )
                ),
            )
        )

    if entrees.meta_ads is not None:
        code = (
            (entrees.meta_ads.pays.code_pays or "").strip().upper()
            if entrees.meta_ads.pays
            else ""
        )
        validites.append(
            ValiditeRegionaleSource(
                source=SOURCE_META_ADS,
                portee=PORTEE_DIFFUSION_PAYS,
                commentaire=(
                    f"annonces diffusées en {code or 'pays non résolu'}, quel que soit "
                    f"le pays de l'annonceur : la présence publicitaire n'implique ni "
                    f"disponibilité produit, ni volume de vente."
                ),
            )
        )

    if entrees.web is not None:
        validites.append(
            ValiditeRegionaleSource(
                source=SOURCE_WEB,
                portee=PORTEE_MIXTE,
                commentaire=(
                    "pages web de portées hétérogènes (régionales et globales) : le "
                    "champ `portee_regionale` de chaque page fait foi, pas la source."
                ),
            )
        )
    return validites


def charger_entrees(
    chemin_aliexpress: str | None,
    chemin_amazon: str | None,
    chemin_meta_ads: str | None,
    chemin_web: str | None,
) -> tuple[
    EntreesChargees,
    list[SourceUtilisee],
    list[AlerteCoherence],
    list[ValiditeRegionaleSource],
]:
    """Charge, valide et confronte les quatre fichiers d'entrée.

    Args:
        chemin_aliexpress: Sortie de `agent_aliexpress`, ou `None`.
        chemin_amazon: Sortie de `agent_amazon`, ou `None`.
        chemin_meta_ads: Sortie de `agent_meta_ads`, ou `None`.
        chemin_web: Sortie de `agent_recherche_web`, ou `None`.

    Returns:
        Le quadruplet `(entrees, sources, alertes, validite_regionale)`.

    Raises:
        ErreurCoherenceProduit: Si les fichiers portent des produits différents.
    """
    aliexpress, cr_ax = _charger_source(chemin_aliexpress, EntreeAliExpress, SOURCE_ALIEXPRESS)
    amazon, cr_amz = _charger_source(chemin_amazon, EntreeAmazon, SOURCE_AMAZON)
    meta_ads, cr_meta = _charger_source(chemin_meta_ads, EntreeMetaAds, SOURCE_META_ADS)
    web, cr_web = _charger_source(chemin_web, EntreeRechercheWeb, SOURCE_WEB)

    sources = [cr_ax, cr_amz, cr_meta, cr_web]
    alertes: list[AlerteCoherence] = []

    if amazon is not None and not amazon.region_couverte:
        alertes.append(
            AlerteCoherence(
                type="portee_regionale",
                detail=(
                    "Amazon non couvert pour ce pays — benchmark limité aux autres "
                    "sources. Aucune offre ni aucun avis Amazon n'entre dans l'analyse."
                ),
            )
        )
        amazon = None
        cr_amz.avertissements.append("region_couverte=false — source écartée")

    if meta_ads is not None and not meta_ads.region_couverte:
        alertes.append(
            AlerteCoherence(
                type="portee_regionale",
                detail=(
                    "Meta Ads : région non résolue — aucune annonce n'entre dans "
                    "l'analyse de la pression publicitaire."
                ),
            )
        )
        meta_ads = None
        cr_meta.avertissements.append("region_couverte=false — source écartée")

    presentes: list[tuple[str, Any]] = [
        (nom, entree)
        for nom, entree in (
            (SOURCE_ALIEXPRESS, aliexpress),
            (SOURCE_AMAZON, amazon),
            (SOURCE_META_ADS, meta_ads),
            (SOURCE_WEB, web),
        )
        if entree is not None
    ]
    alertes.extend(_controler_coherence(presentes))

    limites_amont = [
        f"[{nom}] {limite}" for nom, entree in presentes for limite in entree.limites
    ]

    entrees = EntreesChargees(
        aliexpress=aliexpress,
        amazon=amazon,
        meta_ads=meta_ads,
        web=web,
        produit=presentes[0][1].produit if presentes else None,
        marche=presentes[0][1].marche if presentes else None,
        limites_amont=limites_amont,
    )
    validite = _validite_regionale(entrees, alertes)
    return entrees, sources, alertes, validite
