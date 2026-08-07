"""Contrats d'entrée et de sortie du module, en Pydantic v2.

Les modèles d'entrée (`FicheProduit`, `ParametresMarche`) sont identiques à ceux
des agents Tendances et Reddit du projet, afin qu'un orchestrateur amont puisse
alimenter tous les collecteurs avec le même objet.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from config import (
    AXE_CONCURRENCE,
    AXE_CONSOMMATEURS,
    AXE_MIXTE,
    CIBLAGE_GEO_KEYWORDS,
    CIBLAGE_OUVERT,
    CIBLAGE_TLD,
    TYPES_SOURCE,
)

_DESCRIPTION_AXE = (
    f"« {AXE_CONSOMMATEURS} » (consommateurs), « {AXE_CONCURRENCE} » "
    f"(concurrence) ou « {AXE_MIXTE} »."
)
_DESCRIPTION_CIBLAGE = (
    f"« {CIBLAGE_TLD} », « {CIBLAGE_GEO_KEYWORDS} » ou « {CIBLAGE_OUVERT} »."
)
_DESCRIPTION_TYPE_SOURCE = "« " + " », « ".join(TYPES_SOURCE) + " »."

# --------------------------------------------------------------------------- #
# Entrée
# --------------------------------------------------------------------------- #


class FicheProduit(BaseModel):
    """Fiche produit soumise à l'étude."""

    nom: str = Field(description="Titre commercial du produit.")
    description: str = Field(description="Description libre du produit.")
    categorie: str | None = Field(
        default=None, description="Catégorie e-commerce, optionnelle sur ce module."
    )


class ParametresMarche(BaseModel):
    """Région d'étude."""

    geo: str = Field(description="Code pays ISO-2 en majuscules, ex. « FR », « MA ».")
    langue: str = Field(description="Code langue ISO-2 en minuscules, ex. « fr ».")


# --------------------------------------------------------------------------- #
# Contrôle qualité de la fiche
# --------------------------------------------------------------------------- #


class AlerteQualiteInput(BaseModel):
    """Anomalie détectée dans la fiche produit, signalée sans être corrigée."""

    type: str = Field(
        description=(
            "« contradiction », « langue_inattendue », "
            "« description_insuffisante » ou « autre »."
        )
    )
    detail: str = Field(description="Constat factuel citant les éléments en cause.")


class RapportQualiteInput(BaseModel):
    """Enveloppe de sortie de la chaîne de contrôle qualité.

    Un objet racine est nécessaire : `with_structured_output` ne sait pas
    produire directement une liste.
    """

    alertes: list[AlerteQualiteInput] = Field(
        default_factory=list,
        description="Anomalies détectées, vide si la fiche est saine.",
    )


# --------------------------------------------------------------------------- #
# Plan de requêtes
# --------------------------------------------------------------------------- #


class RequetePlanifiee(BaseModel):
    """Requête telle qu'elle sera effectivement envoyée à l'actor."""

    texte: str = Field(description="Requête finale, opérateurs Google inclus.")
    axe: str = Field(description=_DESCRIPTION_AXE)
    ciblage: str = Field(description=_DESCRIPTION_CIBLAGE)
    justification: str = Field(description="Intention de recherche visée par la requête.")
    est_repli: bool = Field(
        default=False,
        description="Vrai si la requête a été générée pour combler un axe sous-couvert.",
    )


class RequeteProposee(BaseModel):
    """Requête proposée par le LLM, avant contrôle de conformité par le code.

    Modèle distinct de `RequetePlanifiee` : `est_repli` est décidé par le code
    appelant, jamais par le modèle.
    """

    texte: str = Field(description="Requête rédigée dans la langue du marché.")
    axe: str = Field(description=_DESCRIPTION_AXE)
    ciblage: str = Field(description=_DESCRIPTION_CIBLAGE)
    justification: str = Field(description="Intention de recherche visée.")


class PlanRequetes(BaseModel):
    """Sortie structurée de la chaîne de génération du plan de requêtes."""

    nom_pays_marche: str = Field(
        description=(
            "Nom du pays du marché en toutes lettres, dans la langue du marché "
            "— ex. « France », « Maroc ». Sert au contrôle mécanique des "
            "requêtes à ciblage géographique."
        )
    )
    requetes: list[RequeteProposee] = Field(
        default_factory=list, description="Requêtes proposées, tous axes confondus."
    )


# --------------------------------------------------------------------------- #
# Corpus
# --------------------------------------------------------------------------- #


class PageWeb(BaseModel):
    """Page web collectée, normalisée et étiquetée."""

    url: str
    domaine: str = Field(description="Nom d'hôte sans préfixe « www. ».")
    titre: str | None = None
    contenu_markdown: str = Field(description="Markdown de la page, tronqué si nécessaire.")
    contenu_tronque: bool = Field(description="Vrai si le Markdown a été coupé.")
    requete_origine: str = Field(description="Texte de la requête ayant remonté la page.")
    axe_cible: str = Field(description=f"Axe de la requête d'origine : {_DESCRIPTION_AXE}")
    ciblage: str = Field(description=_DESCRIPTION_CIBLAGE)
    type_source: str | None = Field(
        default=None, description=f"Attribué par la classification : {_DESCRIPTION_TYPE_SOURCE}"
    )
    axes_servis: list[str] = Field(
        default_factory=list,
        description="Axes réellement servis par la page, attribués par la classification.",
    )
    portee_regionale: bool | None = Field(
        default=None, description="La page concerne-t-elle la région d'étude ?"
    )
    pertinence: float | None = Field(
        default=None, description="Score de 0 à 1 ; nul si la classification était indisponible."
    )
    marques_detectees: list[str] = Field(
        default_factory=list,
        description="Marques et fabricants cités — signal brut, non analysé.",
    )
    type_resultat_serp: str | None = Field(
        default=None,
        description=(
            "Type de résultat Google : « ORGANIC », ou « SUGGESTED » lorsque la "
            "requête n'a produit aucun résultat organique."
        ),
    )
    rang_serp: int | None = Field(
        default=None, description="Rang du résultat dans la SERP, signal de biais SEO."
    )
    langue_page: str | None = Field(
        default=None, description="Code langue déclaré par la page, ex. « fr-FR »."
    )


# --------------------------------------------------------------------------- #
# Classification LLM
# --------------------------------------------------------------------------- #


class ClassificationPage(BaseModel):
    """Étiquetage d'une page d'un lot de classification."""

    index: int = Field(description="Index de la page dans le lot soumis.")
    type_source: str = Field(description=_DESCRIPTION_TYPE_SOURCE)
    axes_servis: list[str] = Field(
        default_factory=list,
        description=f"Sous-ensemble non vide de « {AXE_CONSOMMATEURS} », « {AXE_CONCURRENCE} ».",
    )
    portee_regionale: bool = Field(
        description="Vrai si la page concerne le marché étudié."
    )
    pertinence: float = Field(description="Pertinence de 0 (hors sujet) à 1 (au cœur du sujet).")
    marques_detectees: list[str] = Field(
        default_factory=list, description="Marques et fabricants cités, sans analyse."
    )


class LotClassification(BaseModel):
    """Sortie structurée d'un appel de classification par lot."""

    classifications: list[ClassificationPage] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Statistiques et statuts
# --------------------------------------------------------------------------- #


class StatsCouverture(BaseModel):
    """Statistiques descriptives du corpus et de sa couverture par axe."""

    nb_pages_collectees: int = Field(
        description="Pages renvoyées par l'ensemble des runs, avant tout filtrage."
    )
    nb_pages_retenues: int = Field(description="Pages du corpus final.")
    nb_pages_axe1: int = Field(description="Pages retenues servant l'axe consommateurs.")
    nb_pages_axe2: int = Field(description="Pages retenues servant l'axe concurrence.")
    repartition_par_ciblage: dict[str, int] = Field(default_factory=dict)
    repartition_par_type_source: dict[str, int] = Field(default_factory=dict)
    repartition_par_domaine: dict[str, int] = Field(default_factory=dict)
    axes_sous_couverts: list[str] = Field(
        default_factory=list,
        description="Axes restés sous le seuil de pages après le cycle de repli.",
    )
    nb_doublons_ecartes: int = Field(default=0, description="Pages vues plusieurs fois.")
    nb_pages_exclues_domaine: int = Field(
        default=0, description="Pages écartées par la liste de domaines exclus."
    )
    nb_pages_trop_courtes: int = Field(
        default=0, description="Pages sous le plancher de caractères, crawls échoués compris."
    )
    nb_pages_sous_seuil: int = Field(
        default=0, description="Pages écartées par le seuil de pertinence."
    )
    nb_pages_non_classifiees: int = Field(
        default=0, description="Pages conservées sans étiquetage, la classification ayant échoué."
    )


class StatutCollecte(BaseModel):
    """Compte rendu d'un run Apify, soit d'une requête."""

    requete: str
    succes: bool
    message_erreur: str | None = None
    nb_pages: int = 0
    nb_tentatives: int = 0


# --------------------------------------------------------------------------- #
# Résultat
# --------------------------------------------------------------------------- #


class ResultatRechercheWeb(BaseModel):
    """Objet retourné par l'agent : corpus qualifié et son appareil critique."""

    produit: FicheProduit
    marche: ParametresMarche
    alertes_qualite_input: list[AlerteQualiteInput] = Field(default_factory=list)
    plan_requetes: list[RequetePlanifiee] = Field(default_factory=list)
    pages: list[PageWeb] = Field(default_factory=list)
    stats: StatsCouverture
    statuts_collecte: list[StatutCollecte] = Field(default_factory=list)
    donnees_disponibles: bool = Field(
        description="Faux si aucune page n'a pu être collectée."
    )
    limites: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
