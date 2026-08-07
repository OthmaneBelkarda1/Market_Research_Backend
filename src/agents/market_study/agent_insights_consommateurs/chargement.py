"""Lecture des fichiers d'entrée, validation tolérante et contrôle de cohérence.

Règle centrale : **aucune exception n'est propagée pour une source**. Un fichier
absent, illisible, mal encodé ou non conforme écarte sa source avec un
avertissement tracé ; l'analyse continue sur les autres. Seule l'incohérence de
produit entre deux fichiers est bloquante, parce qu'elle signifie qu'on est en
train de mélanger deux études.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from config import (
    SOURCE_AMAZON,
    SOURCE_REDDIT,
    SOURCE_WEB,
    logger,
)
from schemas import (
    AlerteCoherence,
    EntreeAmazon,
    EntreeRechercheWeb,
    EntreeReddit,
    EntreesChargees,
    ErreurCoherenceProduit,
    SourceUtilisee,
)

ENCODAGES_TESTES: tuple[str, ...] = ("utf-8-sig", "utf-16", "utf-8", "cp1252")
"""Encodages essayés dans l'ordre.

Les collecteurs qui émettent sur `stdout` sont redirigés vers un fichier par le
shell : sous PowerShell, cette redirection produit de l'UTF-16 avec BOM, jamais
de l'UTF-8. Constaté sur les sorties réelles du projet.
"""


def _normaliser_nom(valeur: str) -> str:
    """Normalise un nom de produit pour la comparaison inter-fichiers.

    Args:
        valeur: Nom brut.

    Returns:
        Le nom en minuscules, sans accents ni espaces superflus.
    """
    sans_accent = unicodedata.normalize("NFKD", valeur)
    sans_accent = "".join(c for c in sans_accent if not unicodedata.combining(c))
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
    derniere_erreur: Exception | None = None
    for encodage in ENCODAGES_TESTES:
        try:
            return json.loads(brut.decode(encodage))
        except (UnicodeDecodeError, UnicodeError, json.JSONDecodeError) as erreur:
            derniere_erreur = erreur
    raise ValueError(
        f"contenu non décodable en JSON (encodages testés : "
        f"{', '.join(ENCODAGES_TESTES)}) — {derniere_erreur}"
    )


def _charger_source(
    chemin: str | None,
    modele: type,
    nom_source: str,
) -> tuple[Any | None, SourceUtilisee]:
    """Charge et valide un fichier de collecteur, sans jamais lever.

    Args:
        chemin: Chemin du fichier, ou `None` si la source n'est pas fournie.
        modele: Classe Pydantic du schéma de consommation.
        nom_source: Nom court de la source, pour la traçabilité.

    Returns:
        Le couple `(entree_validee_ou_None, compte_rendu)`.
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
        compte_rendu.avertissements.append(
            "le fichier ne contient pas un objet JSON à la racine"
        )
        return None, compte_rendu

    try:
        entree = modele.model_validate(brut)
    except ValidationError as erreur:
        resume = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            for e in erreur.errors()[:5]
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
        logger.warning("[%s] données déclarées indisponibles, source écartée", nom_source)
        return None, compte_rendu

    return entree, compte_rendu


def _compter_items(entree: Any) -> int:
    """Compte les items bruts d'une entrée de collecteur.

    Args:
        entree: Entrée validée.

    Returns:
        Le nombre d'items du corpus brut (posts + commentaires, produits, pages).
    """
    if isinstance(entree, EntreeReddit):
        return len(entree.posts) + len(entree.commentaires)
    if isinstance(entree, EntreeAmazon):
        return sum(len(p.avis) for p in entree.produits)
    if isinstance(entree, EntreeRechercheWeb):
        return len(entree.pages)
    return 0


def _controler_coherence(
    entrees: list[tuple[str, Any]],
) -> list[AlerteCoherence]:
    """Compare les en-têtes produit/marché des fichiers chargés.

    Args:
        entrees: Couples `(nom_source, entree)` dans l'ordre de priorité.

    Returns:
        Les alertes non bloquantes constatées.

    Raises:
        ErreurCoherenceProduit: Si deux fichiers portent des produits différents.
    """
    alertes: list[AlerteCoherence] = []
    if not entrees:
        return alertes

    source_ref, ref = entrees[0]
    nom_ref = _normaliser_nom(ref.produit.nom)

    for source, entree in entrees[1:]:
        nom = _normaliser_nom(entree.produit.nom)
        if nom != nom_ref:
            raise ErreurCoherenceProduit(
                f"produits différents entre les fichiers d'entrée : "
                f"[{source_ref}] « {ref.produit.nom} » vs [{source}] "
                f"« {entree.produit.nom} ». Mélanger deux études est interdit ; "
                f"vérifie les fichiers fournis."
            )
        if entree.produit.description.strip() != ref.produit.description.strip():
            alertes.append(
                AlerteCoherence(
                    type="produit_divergent",
                    detail=(
                        f"la description produit de [{source}] diffère de celle de "
                        f"[{source_ref}] ; c'est celle de [{source_ref}] qui est retenue."
                    ),
                )
            )
        if (entree.produit.categorie or "") != (ref.produit.categorie or ""):
            alertes.append(
                AlerteCoherence(
                    type="produit_divergent",
                    detail=(
                        f"la catégorie produit de [{source}] "
                        f"(« {entree.produit.categorie} ») diffère de celle de "
                        f"[{source_ref}] (« {ref.produit.categorie} »)."
                    ),
                )
            )
        if entree.marche.geo.strip().upper() != ref.marche.geo.strip().upper():
            alertes.append(
                AlerteCoherence(
                    type="marche_divergent",
                    detail=(
                        f"marché différent : [{source}] porte sur "
                        f"« {entree.marche.geo} » et [{source_ref}] sur "
                        f"« {ref.marche.geo} ». Les insights agrègent deux marchés ; "
                        f"l'analyse se poursuit mais la lecture régionale est caduque."
                    ),
                )
            )
    return alertes


def charger_entrees(
    chemin_reddit: str | None,
    chemin_amazon: str | None,
    chemin_web: str | None,
) -> tuple[EntreesChargees, list[SourceUtilisee], list[AlerteCoherence]]:
    """Charge, valide et confronte les fichiers d'entrée.

    Args:
        chemin_reddit: Chemin de la sortie `agent_reddit`, ou `None`.
        chemin_amazon: Chemin de la sortie `agent_amazon`, ou `None`.
        chemin_web: Chemin de la sortie `agent_recherche_web`, ou `None`.

    Returns:
        Le triplet `(entrees, sources_utilisees, alertes_coherence)`.

    Raises:
        ErreurCoherenceProduit: Si les fichiers portent sur des produits différents.
    """
    reddit, cr_reddit = _charger_source(chemin_reddit, EntreeReddit, SOURCE_REDDIT)
    amazon, cr_amazon = _charger_source(chemin_amazon, EntreeAmazon, SOURCE_AMAZON)
    web, cr_web = _charger_source(chemin_web, EntreeRechercheWeb, SOURCE_WEB)

    sources = [cr_reddit, cr_amazon, cr_web]
    alertes: list[AlerteCoherence] = []

    if amazon is not None and not amazon.region_couverte:
        alertes.append(
            AlerteCoherence(
                type="portee_regionale",
                detail=(
                    "Amazon signale `region_couverte=false` : le pays d'étude n'a pas "
                    "de site Amazon propre, aucun avis client n'est disponible pour "
                    "ce marché."
                ),
            )
        )

    presentes: list[tuple[str, Any]] = [
        (nom, entree)
        for nom, entree in (
            (SOURCE_REDDIT, reddit),
            (SOURCE_AMAZON, amazon),
            (SOURCE_WEB, web),
        )
        if entree is not None
    ]
    alertes.extend(_controler_coherence(presentes))

    limites_amont: list[str] = []
    for nom, entree in presentes:
        for limite in entree.limites:
            limites_amont.append(f"[{nom}] {limite}")

    produit = presentes[0][1].produit if presentes else None
    marche = presentes[0][1].marche if presentes else None

    entrees = EntreesChargees(
        reddit=reddit,
        amazon=amazon,
        web=web,
        produit=produit,
        marche=marche,
        limites_amont=limites_amont,
    )
    return entrees, sources, alertes
