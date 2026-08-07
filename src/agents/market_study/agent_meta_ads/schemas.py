"""Contrats d'entrée et de sortie du module, en Pydantic v2.

Les modèles d'entrée (`FicheProduit`, `ParametresMarche`) sont identiques à ceux
des agents Tendances, Reddit, Recherche web, AliExpress et Amazon du projet,
afin qu'un orchestrateur amont puisse alimenter tous les collecteurs avec le
même objet.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from config import STATUTS, TYPES_CORRESPONDANCE, TYPES_RECHERCHE

_DESCRIPTION_STATUT = "« " + " », « ".join(STATUTS) + " »."
_DESCRIPTION_TYPE_RECHERCHE = "« " + " », « ".join(TYPES_RECHERCHE) + " »."
_DESCRIPTION_CORRESPONDANCE = "« " + " », « ".join(TYPES_CORRESPONDANCE) + " »."

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
    """Région d'étude.

    `geo` accepte le code ISO-2 attendu par les autres collecteurs du projet,
    mais aussi un lieu en texte libre — « Maroc », « Casablanca », « UAE » — ou
    « ALL » pour viser tous les pays à la fois. Le code pays retenu est reporté
    dans `PaysCible.code_pays`.
    """

    geo: str = Field(
        description=(
            "Code pays ISO-2 en majuscules — ex. « FR », « MA » —, nom de lieu en "
            "texte libre, ou « ALL » pour tous les pays."
        )
    )
    langue: str = Field(description="Code langue ISO-2 en minuscules, ex. « fr ».")


# --------------------------------------------------------------------------- #
# Pays retenu
# --------------------------------------------------------------------------- #


class PaysCible(BaseModel):
    """Pays effectivement interrogé dans la bibliothèque publicitaire.

    Il désigne le pays de DIFFUSION des annonces, et non celui de l'annonceur ni
    celui d'expédition du produit (voir `config.MOTIF_CIBLAGE_PAYS`).
    """

    code_pays: str = Field(
        description="Code ISO-2 en majuscules, ou « ALL » pour tous les pays."
    )
    explication: str = Field(description="Motif du choix, en français.")


class RegionResolue(BaseModel):
    """Sortie structurée de la résolution LLM d'un lieu en texte libre."""

    code_pays: str = Field(description="Code ISO 3166-1 alpha-2, en majuscules.")


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
# Plan de recherches
# --------------------------------------------------------------------------- #


class RechercheProposee(BaseModel):
    """Recherche proposée par le LLM, avant contrôle de conformité par le code.

    Modèle distinct de `RecherchePlanifiee` : ni l'URL, ni le pays, ni `est_repli`
    ne sont décidés par le modèle.
    """

    mots_cles: str = Field(
        description=(
            "Mots tapés dans la barre de recherche de la bibliothèque "
            "publicitaire, dans la langue des annonces visées. La recherche porte "
            "sur le TEXTE des annonces, pas sur un catalogue produit."
        )
    )
    type_recherche: str = Field(
        description=(
            f"Mode d'appariement : {_DESCRIPTION_TYPE_RECHERCHE} "
            "« expression_exacte » pour une marque ou un nom de produit, "
            "« mots_cles » pour une formulation catégorielle."
        )
    )
    statut_diffusion: str = Field(
        description=f"Annonces visées selon leur diffusion : {_DESCRIPTION_STATUT}"
    )
    justification: str = Field(description="Angle de recherche visé, en une phrase.")


class PlanRecherches(BaseModel):
    """Sortie structurée de la chaîne de génération du plan de recherches."""

    recherches: list[RechercheProposee] = Field(
        default_factory=list, description="Recherches proposées."
    )


class RecherchePlanifiee(BaseModel):
    """Recherche telle qu'elle sera effectivement exécutée par l'actor."""

    mots_cles: str = Field(
        description=(
            "Mots-clés de la recherche, ou libellé de l'annonceur pour une "
            "collecte lancée depuis une URL de Page."
        )
    )
    type_recherche: str = Field(description=_DESCRIPTION_TYPE_RECHERCHE)
    statut_diffusion: str = Field(description=_DESCRIPTION_STATUT)
    justification: str = Field(description="Angle de recherche visé.")
    url: str = Field(description="URL transmise à l'actor dans `startUrls`.")
    filtres_url: bool = Field(
        description=(
            "Vrai si l'URL restreint le statut de diffusion ou la langue du "
            "créatif. Faux sur une relance élargie après une recherche restée "
            "vide, et sur une collecte par URL d'annonceur."
        )
    )
    est_annonceur: bool = Field(
        default=False,
        description=(
            "Vrai si la recherche cible une Page Facebook précise plutôt qu'un "
            "jeu de mots-clés."
        ),
    )
    est_repli: bool = Field(
        default=False,
        description="Vrai si la recherche a été générée pour combler un corpus trop court.",
    )


# --------------------------------------------------------------------------- #
# Corpus
# --------------------------------------------------------------------------- #


class Annonce(BaseModel):
    """Annonce collectée, normalisée et qualifiée."""

    id_annonce: str | None = Field(
        default=None, description="Identifiant d'archive de l'annonce chez Meta."
    )
    url_bibliotheque: str | None = Field(
        default=None, description="Lien direct vers la fiche publique de l'annonce."
    )

    annonceur: str | None = Field(default=None, description="Nom de la Page annonceur.")
    id_annonceur: str | None = None

    titre: str | None = None
    texte: str | None = Field(
        default=None, description="Corps du créatif, tel que publié."
    )
    description_lien: str | None = Field(
        default=None,
        description=(
            "Texte affiché sous le lien. Porte souvent l'argumentaire complet, "
            "là où `texte` se réduit à un titre."
        ),
    )
    legende: str | None = Field(
        default=None, description="Domaine ou accroche affiché sous le créatif."
    )
    cta: str | None = Field(default=None, description="Libellé du bouton d'appel à l'action.")
    lien: str | None = Field(
        default=None, description="Destination du clic — souvent une page produit."
    )

    image: str | None = None
    video: str | None = Field(
        default=None,
        description=(
            "URL de la vidéo ou, à défaut, de son aperçu. **Signée et "
            "éphémère** : elle expire en quelques heures."
        ),
    )
    type_media: str = Field(description="« image », « video » ou « inconnu ».")

    id_collation: str | None = Field(
        default=None,
        description=(
            "Identifiant du groupe de déclinaisons du créatif, calculé par Meta. "
            "Clé de dédoublonnage privilégiée."
        ),
    )
    nb_declinaisons: int | None = Field(
        default=None,
        description=(
            "Déclinaisons du créatif dans le groupe, selon Meta — indicateur "
            "d'intensité de campagne."
        ),
    )

    plateformes: list[str] = Field(
        default_factory=list,
        description="Plateformes Meta de diffusion : facebook, instagram, messenger…",
    )
    active: bool | None = Field(
        default=None, description="Diffusion en cours au moment du run."
    )
    date_debut: str | None = Field(default=None, description="Date ISO de début de diffusion.")
    date_fin: str | None = Field(
        default=None,
        description=(
            "Date ISO de fin. ⚠️ Sur une annonce encore diffusée, Meta y met la "
            "date du jour : ce n'est pas une date d'arrêt. Seul `active` dit si "
            "la diffusion se poursuit."
        ),
    )
    duree_diffusion_jours: int | None = Field(
        default=None,
        description=(
            "Jours écoulés entre le début et la fin de diffusion — ou la date du "
            "run si l'annonce est encore active. Indicateur de LONGÉVITÉ, jamais "
            "de rentabilité."
        ),
    )

    portee_estimee: str | None = Field(
        default=None,
        description="Fourchette de portée. Publiée par Meta pour les seules annonces politiques.",
    )
    depense: str | None = Field(
        default=None,
        description="Fourchette de dépense. Publiée pour les seules annonces politiques.",
    )
    devise: str | None = None

    recherche_origine: str = Field(description="Recherche ayant remonté l'annonce.")
    rang_collecte: int = Field(description="Rang de l'annonce dans le dataset de sa recherche.")

    correspondance: str | None = Field(
        default=None,
        description=f"Attribué par la classification : {_DESCRIPTION_CORRESPONDANCE}",
    )
    pertinence: float | None = Field(
        default=None,
        description="Score de 0 à 1 ; nul si la classification était indisponible.",
    )


# --------------------------------------------------------------------------- #
# Classification LLM
# --------------------------------------------------------------------------- #


class ClassificationAnnonce(BaseModel):
    """Étiquetage d'une annonce d'un lot de classification."""

    index: int = Field(description="Index de l'annonce dans le lot soumis.")
    correspondance: str = Field(description=_DESCRIPTION_CORRESPONDANCE)
    pertinence: float = Field(
        description="Pertinence de 0 (hors sujet) à 1 (annonce directement concurrente)."
    )


class LotClassification(BaseModel):
    """Sortie structurée d'un appel de classification par lot."""

    classifications: list[ClassificationAnnonce] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Statistiques et statuts
# --------------------------------------------------------------------------- #


class StatsCollecte(BaseModel):
    """Statistiques descriptives du corpus livré."""

    nb_annonces_collectees: int = Field(
        description="Annonces renvoyées par l'ensemble des runs, avant tout filtrage."
    )
    nb_annonces_retenues: int = Field(description="Annonces du corpus final.")
    nb_annonceurs: int = Field(
        default=0, description="Annonceurs distincts présents au corpus final."
    )
    nb_annonces_actives: int = Field(
        default=0, description="Annonces encore diffusées au moment du run."
    )

    nb_doublons_ecartes: int = Field(
        default=0, description="Annonces vues plusieurs fois, dédoublonnées par identifiant."
    )
    nb_doublons_creatif: int = Field(
        default=0,
        description=(
            "Annonces écartées comme reprises d'un créatif déjà retenu chez le "
            "même annonceur."
        ),
    )
    nb_annonces_hors_criteres: int = Field(
        default=0,
        description="Annonces écartées par le statut demandé ou faute de contenu exploitable.",
    )
    nb_annonces_sous_seuil: int = Field(
        default=0, description="Annonces écartées par le seuil de pertinence."
    )
    nb_annonces_non_classifiees: int = Field(
        default=0,
        description="Annonces conservées sans étiquetage, la classification ayant échoué.",
    )

    duree_diffusion_mediane_jours: float | None = Field(
        default=None, description="Médiane des durées de diffusion connues."
    )
    duree_diffusion_max_jours: int | None = None
    repartition_par_correspondance: dict[str, int] = Field(default_factory=dict)
    repartition_par_annonceur: dict[str, int] = Field(default_factory=dict)
    repartition_par_plateforme: dict[str, int] = Field(default_factory=dict)
    repartition_par_cta: dict[str, int] = Field(default_factory=dict)
    repartition_par_recherche: dict[str, int] = Field(default_factory=dict)


class StatutCollecte(BaseModel):
    """Compte rendu d'un run Apify."""

    recherche: str = Field(description="Mots-clés, ou URL de Page pour une collecte annonceur.")
    url: str = Field(description="URL effectivement transmise à l'actor.")
    succes: bool
    message_erreur: str | None = None
    nb_items: int = 0
    nb_tentatives: int = 0
    plafond_atteint: bool = Field(
        default=False,
        description="Vrai si le run a rendu autant d'annonces que le plafond autorisé.",
    )


# --------------------------------------------------------------------------- #
# Résultat
# --------------------------------------------------------------------------- #


class ResultatRechercheMetaAds(BaseModel):
    """Objet retourné par l'agent : corpus qualifié et son appareil critique."""

    produit: FicheProduit
    marche: ParametresMarche
    region_couverte: bool = Field(
        description=(
            "Faux si la région d'étude n'a pas pu être résolue en un pays : rien "
            "n'a été collecté."
        )
    )
    pays: PaysCible | None = Field(
        default=None, description="Pays interrogé ; nul lorsque `region_couverte` est faux."
    )
    alertes_qualite_input: list[AlerteQualiteInput] = Field(default_factory=list)
    plan_recherches: list[RecherchePlanifiee] = Field(default_factory=list)
    annonces: list[Annonce] = Field(default_factory=list)
    stats: StatsCollecte
    statuts_collecte: list[StatutCollecte] = Field(default_factory=list)
    donnees_disponibles: bool = Field(
        description="Faux si aucune annonce n'a pu être collectée."
    )
    limites: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
