"""Contrats Pydantic v2 : consommation des sorties collecteurs, référentiel
interne, sorties structurées des chaînes LLM et résultat final.

**Principe impératif des schémas de consommation** : re-déclaration minimale des
seuls champs consommés, `extra="ignore"`, et *aucun import du code des
collecteurs*. Le couplage se fait par contrat JSON uniquement.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from config import (
    CERTITUDE_PROBABLE,
    CONFIANCE_FAIBLE,
    MENACE_FAIBLE,
    SEGMENT_COEUR,
    SEGMENT_ENTREE,
    SEGMENT_PREMIUM,
    STATUT_HYPOTHESE,
    TYPE_SANS_MARQUE,
)

# =========================================================================== #
# 1. Schémas de consommation
# =========================================================================== #


class SchemaConsomme(BaseModel):
    """Base tolérante : tout champ non listé est ignoré."""

    model_config = ConfigDict(extra="ignore")


class FicheProduit(SchemaConsomme):
    """Fiche du produit étudié."""

    nom: str
    description: str = ""
    categorie: str | None = None


class ParametresMarche(SchemaConsomme):
    """Marché de l'étude. `devise` n'est renseignée que par AliExpress."""

    geo: str
    langue: str
    devise: str | None = None


class SocleCollecteur(SchemaConsomme):
    """Champs communs à toute sortie de collecteur."""

    produit: FicheProduit
    marche: ParametresMarche
    donnees_disponibles: bool = True
    limites: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)


# --- AliExpress ------------------------------------------------------------ #


class ContexteRegional(SchemaConsomme):
    """Contexte régional recopié dans chaque ligne de prix AliExpress."""

    pays_livraison: str | None = None
    pays_livraison_confirme: str | None = None
    devise: str | None = None
    horodatage_utc: str | None = None


class SkuAliExpress(SchemaConsomme):
    """Déclinaison d'un produit AliExpress."""

    sku_id: str | None = None
    attributs_lisibles: dict[str, str] = Field(default_factory=dict)
    prix_vente: float | None = None
    devise: str | None = None
    stock_disponible: int | None = None


class ProduitAliExpress(SchemaConsomme):
    """Offre AliExpress issue de la phase de recherche."""

    item_id: str
    titre: str = ""
    prix_vente: float | None = None
    prix_original: float | None = None
    devise: str | None = None
    note: float | None = None
    taux_evaluation: float | None = None
    nb_commandes: int | None = None
    url_produit: str | None = None
    requete_origine: str | None = None
    contexte: ContexteRegional | None = None


class ProduitDetailleAliExpress(SchemaConsomme):
    """Fiche détaillée d'une offre AliExpress, avec ses SKU."""

    item_id: str
    titre: str = ""
    nb_ventes: int | None = None
    note_moyenne: float | None = None
    nb_evaluations: int | None = None
    delai_livraison_jours: int | None = None
    skus: list[SkuAliExpress] = Field(default_factory=list)
    contexte: ContexteRegional | None = None


class StatsAliExpress(SchemaConsomme):
    """Statistiques de collecte AliExpress."""

    devise: str | None = None
    nb_produits_recherche: int = 0
    nb_produits_detailles: int = 0


class EntreeAliExpress(SocleCollecteur):
    """Sortie de `agent_aliexpress`."""

    produits: list[ProduitAliExpress] = Field(default_factory=list)
    produits_detailles: list[ProduitDetailleAliExpress] = Field(default_factory=list)
    stats: StatsAliExpress | None = None


# --- Amazon ---------------------------------------------------------------- #


class AvisAmazon(SchemaConsomme):
    """Avis client rattaché à une offre Amazon.

    `votes_utiles` est reçu sous forme de mention textuelle brute, pas d'entier.
    """

    note: float | None = None
    titre: str | None = None
    texte: str = ""
    date: str | None = None
    achat_verifie: bool | None = None
    votes_utiles: str | int | None = None


class ProduitAmazon(SchemaConsomme):
    """Offre Amazon concurrente."""

    asin: str
    titre: str = ""
    marque: str | None = None
    prix: float | None = None
    devise: str | None = None
    prix_barre: float | None = None
    note: float | None = None
    nb_avis: int | None = None
    volume_achats_mensuel: str | int | None = Field(
        default=None,
        description=(
            "Amazon publie une MENTION PAR PALIERS (« 1K+ bought in past month »), "
            "pas un entier : constaté sur les sorties réelles. Le référentiel la "
            "convertit en plancher de fourchette."
        ),
    )
    rang_best_seller: int | None = None
    choix_amazon: bool | None = None
    url: str | None = None
    correspondance: str | None = None
    pertinence: float | None = None
    avis: list[AvisAmazon] = Field(default_factory=list)


class MarketplaceAmazon(SchemaConsomme):
    """Site Amazon interrogé."""

    domaine: str | None = None
    code_pays: str | None = None


class EntreeAmazon(SocleCollecteur):
    """Sortie de `agent_amazon`."""

    region_couverte: bool = True
    marketplace: MarketplaceAmazon | None = None
    produits: list[ProduitAmazon] = Field(default_factory=list)


# --- Meta Ads -------------------------------------------------------------- #


class AnnonceMeta(SchemaConsomme):
    """Annonce de la bibliothèque publicitaire Meta.

    `description_lien` porte souvent l'argumentaire complet, là où `texte` se
    réduit à un titre : les deux sont concaténés dans le référentiel.
    """

    id_annonce: str
    annonceur: str = ""
    titre: str | None = None
    texte: str | None = None
    description_lien: str | None = None
    legende: str | None = None
    cta: str | None = None
    lien: str | None = None
    plateformes: list[str] = Field(default_factory=list)
    active: bool | None = None
    duree_diffusion_jours: int | None = None
    nb_declinaisons: int | None = None
    id_collation: str | None = None
    correspondance: str | None = None
    pertinence: float | None = None


class PaysMeta(SchemaConsomme):
    """Pays de diffusion ciblé."""

    code_pays: str | None = None


class EntreeMetaAds(SocleCollecteur):
    """Sortie de `agent_meta_ads`."""

    region_couverte: bool = True
    pays: PaysMeta | None = None
    annonces: list[AnnonceMeta] = Field(default_factory=list)


# --- Recherche web --------------------------------------------------------- #


class PageWeb(SchemaConsomme):
    """Page web collectée."""

    url: str
    domaine: str | None = None
    titre: str | None = None
    contenu_markdown: str | None = None
    axes_servis: list[str] = Field(default_factory=list)
    type_source: str | None = None
    portee_regionale: bool | None = None
    pertinence: float | None = None
    marques_detectees: list[str] = Field(default_factory=list)


class EntreeRechercheWeb(SocleCollecteur):
    """Sortie de `agent_recherche_web`."""

    pages: list[PageWeb] = Field(default_factory=list)


# =========================================================================== #
# 2. Référentiel interne
# =========================================================================== #


class ClaimsAnnonce(BaseModel):
    """Argumentaire extrait d'une annonce."""

    promesse_principale: str | None = None
    angle: str | None = Field(
        default=None,
        description=(
            "« prix », « qualite », « innovation », « statut », « praticite », "
            "« urgence », « preuve_sociale » ou « autre »."
        ),
    )
    offre_commerciale: str | None = Field(
        default=None, description="Remise, bundle, livraison offerte… ou null."
    )
    cible_suggeree: str | None = None


class OffreConcurrente(BaseModel):
    """Offre marchande normalisée, toutes sources de prix confondues."""

    id_offre: str = Field(description="« ax-{item_id} » ou « amz-{asin} ».")
    source: str
    titre: str
    marque: str | None = None
    prix: float | None = None
    devise: str | None = Field(
        default=None, description="Code normalisé ; jamais converti."
    )
    prix_barre: float | None = None
    note: float | None = None
    nb_avis_ou_evaluations: int | None = None
    volume_ventes: int | None = None
    badges: list[str] = Field(default_factory=list)
    url: str | None = None
    attributs_extraits: list[str] = Field(default_factory=list)
    correspondance: str | None = None
    est_accessoire: bool = False
    prix_skus: list[float] = Field(
        default_factory=list, description="Prix des SKU, pour les fourchettes."
    )
    asin: str | None = Field(default=None, description="Clé de rattachement des avis.")


class AnnonceConcurrente(BaseModel):
    """Annonce publicitaire normalisée."""

    id_annonce: str = Field(description="« ads-{id_annonce} ».")
    annonceur: str
    texte_complet: str
    cta: str | None = None
    plateformes: list[str] = Field(default_factory=list)
    active: bool | None = None
    duree_diffusion_jours: int | None = None
    nb_declinaisons: int | None = None
    claims: ClaimsAnnonce | None = None


class PageConcurrence(BaseModel):
    """Page web normalisée."""

    id_page: str = Field(description="« web-{i} ».")
    url: str
    domaine: str
    titre: str | None = None
    type_source: str | None = None
    marques_detectees: list[str] = Field(default_factory=list)
    extrait: str


class AvisIndexe(BaseModel):
    """Avis client rattaché à une offre, adressable par identifiant."""

    id_avis: str = Field(description="« amz-{asin}-avis-{i} ».")
    id_offre: str
    note: float | None = None
    titre: str | None = None
    texte: str


class ConcurrentConsolide(BaseModel):
    """Entité concurrente rapprochée à travers les sources."""

    nom_canonique: str
    alias: list[str] = Field(default_factory=list)
    type: str = Field(
        default=TYPE_SANS_MARQUE,
        description=(
            "« marque_etablie », « marque_marketplace », « annonceur_seul » "
            "ou « offres_sans_marque »."
        ),
    )
    presence: dict[str, int] = Field(default_factory=dict)
    ids_offres: list[str] = Field(default_factory=list)
    ids_annonces: list[str] = Field(default_factory=list)
    ids_pages: list[str] = Field(default_factory=list)
    niveau_certitude_rapprochement: str = CERTITUDE_PROBABLE


class LotConcurrents(BaseModel):
    """Enveloppe de sortie de la chaîne de consolidation."""

    concurrents: list[ConcurrentConsolide] = Field(default_factory=list)


class ReferentielStats(BaseModel):
    """Décompte du référentiel et des exclusions, par motif."""

    nb_offres_par_source: dict[str, int] = Field(default_factory=dict)
    nb_offres_coeur: int = 0
    nb_offres_accessoires: int = 0
    nb_annonces: int = 0
    nb_pages: int = 0
    nb_avis_indexes: int = 0
    exclusions: dict[str, int] = Field(
        default_factory=dict,
        description="Motif → nombre d'éléments écartés (hors_sujet, pertinence…).",
    )


# =========================================================================== #
# 3. Sorties structurées des chaînes LLM
# =========================================================================== #


class AttributsOffre(BaseModel):
    """Attributs objectifs lus dans le titre d'une offre."""

    id_offre: str
    attributs: list[str] = Field(
        default_factory=list, description="2 à 5 attributs normalisés courts."
    )


class LotAttributs(BaseModel):
    """Enveloppe de lot d'extraction d'attributs."""

    offres: list[AttributsOffre] = Field(default_factory=list)


class ClaimsAvecId(ClaimsAnnonce):
    """Claims rattachés à l'identifiant de leur annonce."""

    id_annonce: str


class LotClaims(BaseModel):
    """Enveloppe de lot d'extraction de claims."""

    annonces: list[ClaimsAvecId] = Field(default_factory=list)


class Preuve(BaseModel):
    """Référence vérifiable à un élément du référentiel."""

    id_reference: str = Field(
        description="id_offre, id_annonce, id_page ou « amz-{asin}-avis-{i} »."
    )
    type: str = Field(description="« offre », « annonce », « page » ou « avis ».")
    extrait: str | None = Field(
        default=None, description="Extrait textuel borné, si la preuve est textuelle."
    )


class PointEtaye(BaseModel):
    """Constat accompagné de ses preuves."""

    point: str
    preuves: list[Preuve] = Field(default_factory=list)
    statut: str = Field(
        default=STATUT_HYPOTHESE, description="« fait » ou « hypothese »."
    )


class AnalyseConcurrent(BaseModel):
    """Analyse qualitative d'un concurrent."""

    proposition_valeur: str = ""
    arguments_marketing: list[str] = Field(default_factory=list)
    forces: list[PointEtaye] = Field(default_factory=list)
    faiblesses: list[PointEtaye] = Field(default_factory=list)
    segment_prix_par_source: dict[str, str] = Field(default_factory=dict)
    niveau_menace: str = Field(
        default=MENACE_FAIBLE, description="« fort », « moyen » ou « faible »."
    )
    justification_menace: str = ""


class Positionnement(BaseModel):
    """Lecture transversale du positionnement du marché observé."""

    axes_observes: list[str] = Field(default_factory=list)
    messages_dominants: list[PointEtaye] = Field(default_factory=list)
    angles_peu_exploites: list[PointEtaye] = Field(
        default_factory=list,
        description=(
            "Absences CONSTATÉES dans le corpus, formulées comme telles avec ses "
            "volumes en référence — jamais « n'existe pas sur le marché »."
        ),
    )
    facteurs_cles_succes: list[PointEtaye] = Field(default_factory=list)
    normes_marche: list[PointEtaye] = Field(default_factory=list)


class SortieLectureTransversale(BaseModel):
    """Sortie de la chaîne de lecture transversale."""

    positionnement: Positionnement = Field(default_factory=Positionnement)
    lecture_intensite: str = ""


class Differenciation(BaseModel):
    """Position du produit étudié face aux offres observées."""

    attributs_partages: list[PointEtaye] = Field(default_factory=list)
    attributs_distinctifs_potentiels: list[PointEtaye] = Field(default_factory=list)
    desavantages_apparents: list[PointEtaye] = Field(default_factory=list)


class SortieSynthese(BaseModel):
    """Sortie de la chaîne de synthèse exécutive."""

    synthese_executive: str = Field(default="", description="12 lignes maximum.")


# =========================================================================== #
# 4. Socle commun des agents d'analyse
# =========================================================================== #


class SourceUtilisee(BaseModel):
    """Compte rendu de chargement d'un fichier d'entrée."""

    source: str
    fichier: str | None = None
    donnees_disponibles: bool = False
    nb_items_charges: int = 0
    nb_items_exploites: int = 0
    avertissements: list[str] = Field(default_factory=list)


class AlerteCoherence(BaseModel):
    """Écart constaté entre fichiers d'entrée, non bloquant."""

    type: str
    detail: str


class StatutAnalyse(BaseModel):
    """Compte rendu d'une phase d'analyse."""

    phase: str
    succes: bool
    message_erreur: str | None = None
    nb_elements: int = 0
    nb_tentatives: int = 0


class ConfianceGlobale(BaseModel):
    """Niveau de confiance de l'analyse et sa justification."""

    niveau: str = CONFIANCE_FAIBLE
    justification: str = ""
    facteurs: list[str] = Field(default_factory=list)


# =========================================================================== #
# 5. Sortie finale
# =========================================================================== #


class StatsConcurrent(BaseModel):
    """Statistiques d'un concurrent, calculées par le code."""

    fourchette_prix_par_devise: dict[str, str] = Field(default_factory=dict)
    prix_min_par_devise: dict[str, float] = Field(default_factory=dict)
    prix_max_par_devise: dict[str, float] = Field(default_factory=dict)
    note_moyenne: float | None = None
    nb_offres: int = 0
    volume_ventes_cumule: int | None = None
    nb_annonces: int = 0
    nb_annonces_actives: int = 0
    longevite_max_jours: int | None = None
    nb_pages_mentionnant: int = 0


class FicheConcurrent(BaseModel):
    """Concurrent consolidé, chiffré et — pour le top N — analysé."""

    concurrent: ConcurrentConsolide
    stats: StatsConcurrent = Field(default_factory=StatsConcurrent)
    analyse: AnalyseConcurrent | None = None


class SegmentPrix(BaseModel):
    """Tercile de prix d'une source."""

    nom: str = Field(
        description=f"« {SEGMENT_ENTREE} », « {SEGMENT_COEUR} » ou « {SEGMENT_PREMIUM} »."
    )
    borne_basse: float
    borne_haute: float
    nb_offres: int = 0


class BenchmarkSource(BaseModel):
    """Benchmark de prix d'une source et d'une devise. Jamais d'agrégat inter-devises."""

    source: str
    devise: str
    nb_offres_avec_prix: int = 0
    prix_min: float = 0.0
    prix_mediane: float = 0.0
    prix_max: float = 0.0
    dispersion: float = 0.0
    segments: list[SegmentPrix] = Field(default_factory=list)
    commentaire: str = ""


class PositionPrixEnvisage(BaseModel):
    """Position du prix envisagé dans un benchmark de même devise."""

    prix: float
    devise: str
    source_comparable: str | None = None
    percentile: float | None = None
    segment: str | None = None
    ecart_mediane_pct: float | None = None
    commentaire: str = ""


class IntensiteConcurrentielle(BaseModel):
    """Indicateurs d'intensité concurrentielle et publicitaire."""

    nb_concurrents_identifies: int = 0
    nb_offres_coeur: int = 0
    nb_annonceurs: int = 0
    nb_annonces_actives: int = 0
    duree_diffusion_mediane_jours: float | None = Field(
        default=None, description="Indicateur de longévité, JAMAIS de rentabilité."
    )
    duree_diffusion_max_jours: int | None = None
    plateformes_dominantes: list[str] = Field(default_factory=list)
    concentration_volumes_top3_pct: float | None = None
    lecture: str = ""
    confiance: str = CONFIANCE_FAIBLE


class LigneComparatif(BaseModel):
    """Ligne du tableau comparatif, régénérée par le code."""

    concurrent: str
    presence_sources: list[str] = Field(default_factory=list)
    fourchette_prix_par_devise: dict[str, str] = Field(default_factory=dict)
    note_moyenne: float | None = None
    volume_ventes_cumule: int | None = None
    argument_principal: str | None = None
    force_principale: str | None = None
    faiblesse_principale: str | None = None


class ValiditeRegionaleSource(BaseModel):
    """Portée régionale réelle d'une source."""

    source: str
    portee: str
    commentaire: str


class ResultatAnalyseConcurrentielle(BaseModel):
    """Objet de sortie complet de l'agent d'analyse de l'axe 2."""

    produit: FicheProduit
    marche: ParametresMarche
    horodatage_utc: str = Field(
        description=(
            "Horodatage ISO 8601 UTC de production de l'analyse. Enrichissement "
            "propre à cet agent : aucun contrat amont n'en fournit, et l'agent de "
            "recommandations en a besoin pour qualifier la fraîcheur."
        )
    )
    sources_utilisees: list[SourceUtilisee] = Field(default_factory=list)
    alertes_coherence: list[AlerteCoherence] = Field(default_factory=list)
    referentiel_stats: ReferentielStats = Field(default_factory=ReferentielStats)
    concurrents: list[FicheConcurrent] = Field(default_factory=list)
    benchmark_prix: list[BenchmarkSource] = Field(default_factory=list)
    position_prix_envisage: PositionPrixEnvisage | None = None
    intensite_concurrentielle: IntensiteConcurrentielle | None = None
    positionnement: Positionnement | None = None
    differenciation: Differenciation | None = None
    tableau_comparatif: list[LigneComparatif] = Field(default_factory=list)
    validite_regionale: list[ValiditeRegionaleSource] = Field(default_factory=list)
    synthese_executive: str = ""
    statuts_analyse: list[StatutAnalyse] = Field(default_factory=list)
    donnees_suffisantes: bool = False
    confiance_globale: ConfianceGlobale = Field(default_factory=ConfianceGlobale)
    limites: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)


# =========================================================================== #
# 6. Conteneurs de travail
# =========================================================================== #


class EntreesChargees(BaseModel):
    """Fichiers d'entrée validés et en-tête retenu."""

    aliexpress: EntreeAliExpress | None = None
    amazon: EntreeAmazon | None = None
    meta_ads: EntreeMetaAds | None = None
    web: EntreeRechercheWeb | None = None
    produit: FicheProduit | None = None
    marche: ParametresMarche | None = None
    limites_amont: list[str] = Field(default_factory=list)


class Referentiel(BaseModel):
    """Référentiel complet, base de toute preuve citable."""

    offres: list[OffreConcurrente] = Field(default_factory=list)
    annonces: list[AnnonceConcurrente] = Field(default_factory=list)
    pages: list[PageConcurrence] = Field(default_factory=list)
    avis: list[AvisIndexe] = Field(default_factory=list)
    stats: ReferentielStats = Field(default_factory=ReferentielStats)
    limites: list[str] = Field(default_factory=list)

    def est_vide(self) -> bool:
        """Indique si le référentiel ne contient aucun élément analysable."""
        return not (self.offres or self.annonces or self.pages)


class SortieBenchmark(BaseModel):
    """Résultats chiffrés produits par `benchmark.py`."""

    benchmarks: list[BenchmarkSource] = Field(default_factory=list)
    position_prix: PositionPrixEnvisage | None = None
    intensite: IntensiteConcurrentielle | None = None
    stats_par_concurrent: dict[str, StatsConcurrent] = Field(default_factory=dict)
    segment_par_offre: dict[str, str] = Field(default_factory=dict)


class ErreurCoherenceProduit(Exception):
    """Produits différents entre deux fichiers d'entrée — mélange d'études interdit."""
