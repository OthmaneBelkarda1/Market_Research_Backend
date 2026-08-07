"""Contrats d'entrée et de sortie du module, en Pydantic v2.

Les modèles d'entrée (`FicheProduit`, `ParametresMarche`) sont identiques à ceux
des agents Tendances, Reddit, Recherche web et AliExpress du projet, afin qu'un
orchestrateur amont puisse alimenter tous les collecteurs avec le même objet.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from config import (
    MARKETPLACES,
    TRIS,
    TYPES_CORRESPONDANCE,
)

_DESCRIPTION_TRI = "« " + " », « ".join(TRIS) + " »."
_DESCRIPTION_CORRESPONDANCE = "« " + " », « ".join(TYPES_CORRESPONDANCE) + " »."
_DESCRIPTION_MARKETPLACE = "Une marketplace parmi : " + ", ".join(MARKETPLACES) + "."

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
    mais aussi un lieu en texte libre — « Maroc », « Casablanca », « UAE ». La
    résolution en marketplace est faite par le module, et le code pays retenu
    est reporté dans `Marketplace.code_pays`.
    """

    geo: str = Field(
        description=(
            "Code pays ISO-2 en majuscules — ex. « FR », « MA » — ou nom de lieu "
            "en texte libre."
        )
    )
    langue: str = Field(description="Code langue ISO-2 en minuscules, ex. « fr ».")


# --------------------------------------------------------------------------- #
# Marketplace retenue
# --------------------------------------------------------------------------- #


class Marketplace(BaseModel):
    """Site Amazon effectivement interrogé.

    C'est TOUJOURS le site du pays étudié : l'agent s'arrête plutôt que de se
    rabattre sur une marketplace étrangère. Volontairement réduit au domaine —
    aucune adresse de livraison n'accompagne ce choix, de sorte qu'Amazon expose
    le catalogue complet du site dans sa propre devise (voir
    `config.MOTIF_ABSENCE_LIVRAISON`).
    """

    domaine: str = Field(description=_DESCRIPTION_MARKETPLACE)
    code_pays: str = Field(
        description="Code ISO-2 du pays étudié, tel que résolu par le module."
    )
    explication: str = Field(description="Motif du choix, en français.")


class RegionResolue(BaseModel):
    """Sortie structurée de la résolution LLM d'un lieu en texte libre.

    Le modèle n'identifie QUE le pays : c'est la table `MARKETPLACE_PAR_PAYS` qui
    décide ensuite s'il existe un site Amazon, et lequel.
    """

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

    Modèle distinct de `RecherchePlanifiee` : ni l'URL ni `est_repli` ne sont
    décidés par le modèle.
    """

    mots_cles: str = Field(
        description=(
            "Mots-clés tapés dans la barre de recherche Amazon, dans la langue "
            "de la marketplace. Ni phrase, ni référence commerciale complète."
        )
    )
    tri: str = Field(description=f"Ordre de tri : {_DESCRIPTION_TRI}")
    prix_min: float | None = Field(
        default=None,
        description="Prix plancher dans la devise de la marketplace, sinon null.",
    )
    prix_max: float | None = Field(
        default=None,
        description="Prix plafond dans la devise de la marketplace, sinon null.",
    )
    note_min: float | None = Field(
        default=None,
        description=(
            "Note moyenne minimale de 1 à 5. Null si la fiche n'implique aucune "
            "exigence de qualité."
        ),
    )
    nb_avis_min: int = Field(
        default=0,
        ge=0,
        description=(
            "Plancher de nombre d'avis, comme preuve de demande. 0 lorsque rien "
            "ne l'impose."
        ),
    )
    justification: str = Field(description="Angle de recherche visé, en une phrase.")


class PlanRecherches(BaseModel):
    """Sortie structurée de la chaîne de génération du plan de recherches."""

    recherches: list[RechercheProposee] = Field(
        default_factory=list, description="Recherches proposées."
    )


class RecherchePlanifiee(BaseModel):
    """Recherche telle qu'elle sera effectivement exécutée par l'actor."""

    mots_cles: str
    tri: str = Field(description=_DESCRIPTION_TRI)
    prix_min: float | None = None
    prix_max: float | None = None
    note_min: float | None = None
    nb_avis_min: int = 0
    justification: str = Field(description="Angle de recherche visé.")
    url: str = Field(description="URL de recherche Amazon transmise à l'actor.")
    filtres_url: bool = Field(
        description=(
            "Vrai si l'URL porte la facette de prix d'Amazon. Faux sur une "
            "relance sans filtres après une recherche restée vide."
        )
    )
    est_repli: bool = Field(
        default=False,
        description="Vrai si la recherche a été générée pour combler un corpus trop court.",
    )


# --------------------------------------------------------------------------- #
# Corpus
# --------------------------------------------------------------------------- #


class Avis(BaseModel):
    """Avis client collecté sur une fiche produit.

    Le nom du relecteur est volontairement absent : c'est une donnée personnelle
    que l'actor ne renvoie qu'avec `includeGdprSensitive=True`, option laissée à
    faux.
    """

    note: int | None = Field(default=None, description="Note attribuée, de 1 à 5.")
    titre: str | None = None
    texte: str | None = None
    date: str | None = Field(
        default=None, description="Date telle que publiée par Amazon, non normalisée."
    )
    achat_verifie: bool | None = Field(
        default=None, description="Mention « Achat vérifié » d'Amazon."
    )
    votes_utiles: str | None = Field(
        default=None, description="Nombre de lecteurs ayant trouvé l'avis utile."
    )


class ProduitAmazon(BaseModel):
    """Produit collecté, normalisé et qualifié."""

    asin: str | None = Field(default=None, description="Identifiant Amazon du produit.")
    titre: str
    url: str | None = None
    image: str | None = None

    prix: float | None = Field(
        default=None, description="Prix affiché, dans la devise de la marketplace."
    )
    devise: str | None = Field(
        default=None, description="Symbole ou code de devise renvoyé par l'actor."
    )
    prix_barre: float | None = Field(
        default=None, description="Prix de référence barré, s'il est affiché."
    )

    note: float | None = Field(default=None, description="Note moyenne sur 5.")
    nb_avis: int | None = Field(default=None, description="Nombre d'avis cumulés.")
    volume_achats_mensuel: str | None = Field(
        default=None,
        description="Mention « X achetés le mois dernier », signal de demande brut.",
    )

    marque: str | None = None
    vendeur: str | None = None
    note_vendeur: float | None = Field(
        default=None, description="Note globale du vendeur sur 5, si le profil a été scrapé."
    )
    nb_notes_vendeur: int | None = None

    choix_amazon: bool = Field(
        default=False, description="Badge « Amazon's Choice » sur la fiche."
    )
    rang_best_seller: int | None = Field(
        default=None, description="Meilleur rang Best Sellers affiché sur la fiche."
    )
    categorie_best_seller: str | None = None
    disponible: bool | None = Field(
        default=None, description="Faux si la fiche est signalée en rupture."
    )
    livraison: str | None = Field(
        default=None,
        description=(
            "Mention de livraison affichée par Amazon. Relevée sans adresse de "
            "livraison : elle vaut pour le marché de la marketplace, pas pour la "
            "région d'étude."
        ),
    )

    recherche_origine: str = Field(description="Mots-clés de la recherche ayant remonté le produit.")
    rang_collecte: int = Field(description="Rang du produit dans le dataset de sa recherche.")

    correspondance: str | None = Field(
        default=None, description=f"Attribué par la classification : {_DESCRIPTION_CORRESPONDANCE}"
    )
    pertinence: float | None = Field(
        default=None,
        description="Score de 0 à 1 ; nul si la classification était indisponible.",
    )

    avis: list[Avis] = Field(
        default_factory=list, description="Avis collectés, uniquement sur les produits les mieux classés."
    )


# --------------------------------------------------------------------------- #
# Classification LLM
# --------------------------------------------------------------------------- #


class ClassificationProduit(BaseModel):
    """Étiquetage d'un produit d'un lot de classification."""

    index: int = Field(description="Index du produit dans le lot soumis.")
    correspondance: str = Field(description=_DESCRIPTION_CORRESPONDANCE)
    pertinence: float = Field(
        description="Pertinence de 0 (hors sujet) à 1 (produit directement concurrent)."
    )


class LotClassification(BaseModel):
    """Sortie structurée d'un appel de classification par lot."""

    classifications: list[ClassificationProduit] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Statistiques et statuts
# --------------------------------------------------------------------------- #


class StatsCollecte(BaseModel):
    """Statistiques descriptives du corpus livré."""

    nb_produits_collectes: int = Field(
        description="Produits renvoyés par l'ensemble des runs, avant tout filtrage."
    )
    nb_produits_retenus: int = Field(description="Produits du corpus final.")
    nb_produits_avec_avis: int = Field(default=0)
    nb_avis_collectes: int = Field(default=0)

    nb_doublons_ecartes: int = Field(
        default=0, description="Produits vus plusieurs fois, dédoublonnés par ASIN puis par URL."
    )
    nb_produits_hors_criteres: int = Field(
        default=0,
        description="Produits écartés par les critères de prix, de note ou d'avis du plan.",
    )
    nb_produits_sous_seuil: int = Field(
        default=0, description="Produits écartés par le seuil de pertinence."
    )
    nb_produits_non_classifies: int = Field(
        default=0,
        description="Produits conservés sans étiquetage, la classification ayant échoué.",
    )
    nb_enregistrements_erreur: int = Field(
        default=0,
        description=(
            "Enregistrements `error` émis par l'actor à la place de produits — "
            "signal de blocage anti-bot ou de recherche sans résultat."
        ),
    )

    prix_min: float | None = Field(default=None, description="Prix le plus bas du corpus.")
    prix_median: float | None = None
    prix_max: float | None = Field(default=None, description="Prix le plus haut du corpus.")
    devise: str | None = Field(
        default=None, description="Devise dominante du corpus, non convertie."
    )
    note_moyenne: float | None = Field(
        default=None, description="Moyenne non pondérée des notes des produits retenus."
    )
    repartition_par_correspondance: dict[str, int] = Field(default_factory=dict)
    repartition_par_marque: dict[str, int] = Field(default_factory=dict)
    repartition_par_recherche: dict[str, int] = Field(default_factory=dict)


class StatutCollecte(BaseModel):
    """Compte rendu d'un run Apify, soit d'une recherche ou d'un produit."""

    recherche: str = Field(description="Mots-clés, ou URL du produit pour un run d'avis.")
    type_run: str = Field(description="« produits » ou « avis ».")
    succes: bool
    message_erreur: str | None = None
    nb_items: int = 0
    nb_tentatives: int = 0


# --------------------------------------------------------------------------- #
# Résultat
# --------------------------------------------------------------------------- #


class ResultatRechercheAmazon(BaseModel):
    """Objet retourné par l'agent : corpus qualifié et son appareil critique."""

    produit: FicheProduit
    marche: ParametresMarche
    region_couverte: bool = Field(
        description=(
            "Faux si le pays étudié n'a pas de site Amazon propre : l'agent ne "
            "s'applique pas à cette région et rien n'a été collecté."
        )
    )
    marketplace: Marketplace | None = Field(
        default=None,
        description="Site interrogé ; nul lorsque `region_couverte` est faux.",
    )
    alertes_qualite_input: list[AlerteQualiteInput] = Field(default_factory=list)
    plan_recherches: list[RecherchePlanifiee] = Field(default_factory=list)
    produits: list[ProduitAmazon] = Field(default_factory=list)
    stats: StatsCollecte
    statuts_collecte: list[StatutCollecte] = Field(default_factory=list)
    donnees_disponibles: bool = Field(
        description="Faux si aucun produit n'a pu être collecté."
    )
    limites: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
