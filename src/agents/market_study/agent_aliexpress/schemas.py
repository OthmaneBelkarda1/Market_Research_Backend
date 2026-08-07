"""Contrats d'entrée et de sortie du module, en Pydantic v2.

`FicheProduit` est identique à celle des agents Reddit, Tendances et Recherche
web, afin qu'un orchestrateur amont puisse alimenter tous les modules avec le
même objet. `ParametresMarche` y ajoute un champ `devise` : c'est une exigence
propre à cette source, la seule du projet qui renvoie des montants.

Aucun modèle n'expose de valeur par défaut pour la région d'étude. Un triplet
{pays, devise, langue} absent ou mal formé fait échouer la validation, et c'est
la seule erreur bloquante du module.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from config import ETAPE_DETAIL, ETAPE_RECHERCHE

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
    """Région d'étude : pays de livraison, langue et devise d'affichage.

    Les trois champs sont obligatoires et validés par expression régulière. Ce
    triplet est propagé tel quel à chaque appel API et recopié dans chaque ligne
    de prix produite.
    """

    geo: str = Field(
        pattern=r"^[A-Z]{2}$",
        description="Pays de livraison, ISO-2 en majuscules, ex. « MA ».",
    )
    langue: str = Field(
        pattern=r"^[a-z]{2}$",
        description="Langue du marché, ISO-2 en minuscules, ex. « fr ».",
    )
    devise: str = Field(
        pattern=r"^[A-Z]{3}$",
        description="Devise d'affichage des prix, ISO-4217 en majuscules, ex. « MAD ».",
    )

    @property
    def local(self) -> str:
        """Valeur du paramètre `local` de la recherche, ex. « fr_MA ».

        Returns:
            La locale composée de la langue du marché et du pays de livraison.
        """
        return f"{self.langue}_{self.geo}"


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
    """Enveloppe de sortie structurée de la chaîne de contrôle qualité.

    Un objet racine est nécessaire : `with_structured_output` ne sait pas
    produire directement une liste.
    """

    alertes: list[AlerteQualiteInput] = Field(
        default_factory=list,
        description="Anomalies détectées, vide si la fiche est saine.",
    )


class RequetesMarketplace(BaseModel):
    """Sortie structurée de la chaîne de dérivation des requêtes."""

    requetes: list[str] = Field(
        default_factory=list,
        description="2 à 4 requêtes catalogue dans la langue du marché.",
    )
    justification: str = Field(
        default="", description="Explication du choix des requêtes en français."
    )


# --------------------------------------------------------------------------- #
# Contexte régional — porté par chaque ligne de prix
# --------------------------------------------------------------------------- #


class ContexteRegional(BaseModel):
    """Conditions exactes dans lesquelles un prix a été relevé.

    Un prix sans ce contexte n'a pas de sens : le même produit vaut 10,99 EUR
    livré en France et 226,40 MAD livré au Maroc, avec des profondeurs de remise
    et des stocks différents.
    """

    pays_livraison: str = Field(description="Pays de livraison demandé, ISO-2.")
    devise: str = Field(description="Devise demandée, ISO-4217.")
    langue: str = Field(description="Langue demandée, ISO-2.")
    horodatage_utc: str = Field(description="Date du relevé, ISO 8601 UTC.")
    methode_api: str = Field(
        description=f"Méthode d'origine : « {ETAPE_RECHERCHE} » ou « {ETAPE_DETAIL} »."
    )
    pays_livraison_confirme: str | None = Field(
        default=None,
        description=(
            "Pays de livraison renvoyé par l'API, quand elle le confirme. "
            "Différent du pays demandé = ciblage régional non garanti."
        ),
    )


# --------------------------------------------------------------------------- #
# Corpus — phase A (recherche)
# --------------------------------------------------------------------------- #


class ProduitRecherche(BaseModel):
    """Produit remonté par la recherche, prix d'annonce dans la devise d'étude.

    Le prix porté ici est celui du SKU le moins cher du produit ; les prix par
    SKU relèvent de la phase B.
    """

    item_id: str
    titre: str
    url_produit: str | None = None
    image: str | None = None
    prix_vente: float = Field(description="Prix d'annonce dans la devise d'étude.")
    prix_original: float | None = Field(
        default=None, description="Prix barré dans la devise d'étude."
    )
    devise: str = Field(description="Devise du prix, vérifiée contre la demande.")
    prix_formate: str | None = Field(
        default=None, description="Prix tel que la passerelle le met en forme."
    )
    remise_pourcentage: float | None = Field(
        default=None, description="Remise recalculée à partir des prix cibles."
    )
    note: float | None = Field(default=None, description="Note produit sur 5.")
    taux_evaluation: float | None = Field(
        default=None, description="Pourcentage d'évaluations positives."
    )
    nb_commandes: int | None = Field(
        default=None,
        description="Commandes annoncées ; « 3,000+ » est lu comme 3000 (plancher).",
    )
    ids_categories: list[str] = Field(default_factory=list)
    requete_origine: str = Field(
        description="Requête ayant fait remonter le produit en premier."
    )
    contexte: ContexteRegional


# --------------------------------------------------------------------------- #
# Corpus — phase B (détail par SKU)
# --------------------------------------------------------------------------- #


class PrixSku(BaseModel):
    """Ligne de prix d'un SKU, dans la devise d'étude."""

    sku_id: str | None = None
    attributs_sku: str = Field(description="Identifiant d'attributs, ex. « 14:…;491:… ».")
    attributs_lisibles: dict[str, str] = Field(
        default_factory=dict, description="Attributs traduits, ex. {« Taille »: « XL »}."
    )
    prix_base: float | None = Field(default=None, description="Prix barré du SKU.")
    prix_vente: float = Field(description="Prix de vente du SKU.")
    devise: str = Field(description="Devise renvoyée par l'API, vérifiée contre la demande.")
    remise_pourcentage: float | None = None
    stock_disponible: int | None = None


class ProduitDetaille(BaseModel):
    """Détail d'un produit : prix par SKU, stocks et informations de base."""

    item_id: str
    titre: str
    nb_ventes: int | None = Field(default=None, description="Ventes annoncées par l'API.")
    note_moyenne: float | None = None
    nb_evaluations: int | None = None
    statut_produit: str | None = Field(
        default=None, description="Ex. « onSelling »."
    )
    delai_livraison_jours: int | None = None
    skus: list[PrixSku] = Field(default_factory=list)
    contexte: ContexteRegional


# --------------------------------------------------------------------------- #
# Statistiques et statuts
# --------------------------------------------------------------------------- #


class StatsCollecte(BaseModel):
    """Statistiques descriptives de la collecte, dans la devise d'étude."""

    devise: str = Field(description="Devise de tous les montants ci-dessous.")
    nb_produits_recherche: int = Field(description="Produits dédoublonnés en phase A.")
    nb_produits_retenus: int = Field(description="Produits sélectionnés pour la phase B.")
    nb_produits_detailles: int = Field(description="Produits effectivement détaillés.")
    nb_skus: int = 0
    prix_vente_min: float | None = None
    prix_vente_median: float | None = None
    prix_vente_max: float | None = None
    prix_sku_min: float | None = Field(
        default=None, description="Minimum des prix de vente par SKU (phase B)."
    )
    prix_sku_median: float | None = None
    prix_sku_max: float | None = None
    total_annonce_par_requete: dict[str, int] = Field(
        default_factory=dict,
        description="`totalCount` annoncé par la passerelle pour chaque requête.",
    )
    nb_appels_api: int = Field(
        default=0, description="Appels métier réellement émis, nouvelles tentatives incluses."
    )


class StatutCollecte(BaseModel):
    """Compte rendu d'un appel API ou d'un contrôle."""

    etape: str = Field(description="« recherche », « detail », « controle_devise »…")
    cible: str = Field(description="Requête interrogée, itemId, ou objet du contrôle.")
    succes: bool
    message_erreur: str | None = None
    nb_items: int = 0
    nb_tentatives: int = 0
    total_annonce: int | None = Field(
        default=None,
        description="`totalCount` annoncé par la recherche pour cette requête.",
    )


# --------------------------------------------------------------------------- #
# Résultat
# --------------------------------------------------------------------------- #


class ResultatCollecteAliExpressAPI(BaseModel):
    """Objet retourné par l'agent : prix régionalisés et leur appareil critique."""

    produit: FicheProduit
    marche: ParametresMarche
    alertes_qualite_input: list[AlerteQualiteInput] = Field(default_factory=list)
    requetes: list[str] = Field(default_factory=list)
    justification_requetes: str = ""
    produits: list[ProduitRecherche] = Field(default_factory=list)
    produits_detailles: list[ProduitDetaille] = Field(default_factory=list)
    stats: StatsCollecte
    statuts_collecte: list[StatutCollecte] = Field(default_factory=list)
    donnees_disponibles: bool = Field(
        description="Faux si aucun produit n'a pu être collecté."
    )
    limites: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
