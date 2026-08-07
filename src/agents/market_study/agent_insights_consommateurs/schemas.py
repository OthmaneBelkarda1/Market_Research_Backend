"""Contrats Pydantic v2 : consommation des sorties collecteurs, modèles
internes, sorties structurées des chaînes LLM et résultat final.

**Principe impératif des schémas de consommation** : re-déclaration minimale
des seuls champs consommés, `extra="ignore"`, et *aucun import du code des
collecteurs*. Le couplage se fait par contrat JSON, jamais par dépendance de
code : un collecteur peut évoluer sans casser cet agent tant que les champs
listés ici restent présents.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from config import (
    CONFIANCE_FAIBLE,
    INTENSITE_MAX,
    INTENSITE_MIN,
    PORTEE_GLOBALE,
    PORTEE_INCONNUE,
    PORTEE_MIXTE,
    PORTEE_REGIONALE,
    SENTIMENT_NON_APPLICABLE,
    UNITE_AVIS,
    UNITE_COMMENTAIRE,
    UNITE_POST,
)

# =========================================================================== #
# 1. Schémas de consommation — sorties des collecteurs
# =========================================================================== #


class SchemaConsomme(BaseModel):
    """Base tolérante : tout champ non listé est ignoré silencieusement."""

    model_config = ConfigDict(extra="ignore")


class FicheProduit(SchemaConsomme):
    """Fiche du produit étudié, rappelée par chaque collecteur."""

    nom: str
    description: str = ""
    categorie: str | None = None


class ParametresMarche(SchemaConsomme):
    """Marché sur lequel porte l'étude."""

    geo: str
    langue: str


class SocleCollecteur(SchemaConsomme):
    """Champs communs à toute sortie de collecteur."""

    produit: FicheProduit
    marche: ParametresMarche
    donnees_disponibles: bool = True
    limites: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)


# --- Reddit ---------------------------------------------------------------- #


class PostRedditConsomme(SchemaConsomme):
    """Post Reddit tel que consommé par l'analyse."""

    id: str
    titre: str = ""
    texte: str | None = None
    subreddit: str | None = None
    url: str | None = None
    date_creation: str | None = None
    score: int | None = None
    portee: str = PORTEE_INCONNUE
    pertinence: float | None = None


class CommentaireRedditConsomme(SchemaConsomme):
    """Commentaire Reddit ; sa portée et sa pertinence sont héritées du parent."""

    id: str
    id_post: str | None = None
    texte: str = ""
    date_creation: str | None = None
    score: int | None = None


class EntreeReddit(SocleCollecteur):
    """Sortie de `agent_reddit`."""

    posts: list[PostRedditConsomme] = Field(default_factory=list)
    commentaires: list[CommentaireRedditConsomme] = Field(default_factory=list)


# --- Amazon ---------------------------------------------------------------- #


class AvisAmazonConsomme(SchemaConsomme):
    """Avis client rattaché à un produit Amazon.

    `votes_utiles` est déclaré en `str | None` : le collecteur y place la
    mention brute d'Amazon (« 2 personne(s) ont trouvé cet avis utile »), pas
    un entier. Le poids social est extrait par le code du corpus.
    """

    note: float | None = None
    titre: str | None = None
    texte: str = ""
    date: str | None = None
    achat_verifie: bool | None = None
    votes_utiles: str | int | None = None


class ProduitAmazonConsomme(SchemaConsomme):
    """Produit Amazon porteur de ses avis."""

    asin: str
    titre: str = ""
    note: float | None = None
    nb_avis: int | None = None
    marque: str | None = None
    avis: list[AvisAmazonConsomme] = Field(default_factory=list)


class MarketplaceAmazon(SchemaConsomme):
    """Site Amazon effectivement interrogé."""

    domaine: str | None = None
    code_pays: str | None = None


class EntreeAmazon(SocleCollecteur):
    """Sortie de `agent_amazon`."""

    region_couverte: bool = True
    marketplace: MarketplaceAmazon | None = None
    produits: list[ProduitAmazonConsomme] = Field(default_factory=list)


# --- Recherche web --------------------------------------------------------- #


class PageWebConsommee(SchemaConsomme):
    """Page web éditoriale collectée."""

    url: str
    domaine: str | None = None
    titre: str | None = None
    contenu_markdown: str | None = None
    axes_servis: list[str] = Field(default_factory=list)
    type_source: str | None = None
    portee_regionale: bool | None = None
    pertinence: float | None = None


class EntreeRechercheWeb(SocleCollecteur):
    """Sortie de `agent_recherche_web`."""

    pages: list[PageWebConsommee] = Field(default_factory=list)


# =========================================================================== #
# 2. Modèles internes — corpus
# =========================================================================== #


class UniteConsommateur(BaseModel):
    """Unité courte porteuse d'une opinion consommateur."""

    id_unite: str = Field(description="Identifiant déterministe, ex. « rd-p-t3_1v8 ».")
    source: str = Field(
        description=f"« {UNITE_POST} », « {UNITE_COMMENTAIRE} » ou « {UNITE_AVIS} »."
    )
    texte: str
    titre: str | None = None
    note_sur_5: float | None = Field(default=None, description="Avis Amazon uniquement.")
    date: str | None = None
    portee: str = Field(
        default=PORTEE_INCONNUE,
        description=f"« {PORTEE_REGIONALE} », « {PORTEE_GLOBALE} » ou « {PORTEE_INCONNUE} ».",
    )
    poids_social: int = Field(
        default=0, description="Score Reddit ou votes utiles Amazon ; 0 par défaut."
    )
    pertinence_amont: float | None = None
    tronque: bool = False


class DocumentWeb(BaseModel):
    """Page web traitée comme un document, non comme une opinion individuelle."""

    id_unite: str = Field(description="Identifiant déterministe « web-{i} ».")
    url: str
    domaine: str | None = None
    titre: str | None = None
    extrait: str
    type_source: str | None = None
    portee: str = PORTEE_INCONNUE
    tronque: bool = False


# =========================================================================== #
# 3. Sorties structurées des chaînes LLM
# =========================================================================== #


class PainPointDetecte(BaseModel):
    """Problème vécu ou craint, repéré dans une unité."""

    libelle: str = Field(description="Formulation courte du problème, 2 à 6 mots.")
    intensite: int = Field(
        default=INTENSITE_MIN,
        ge=INTENSITE_MIN,
        le=INTENSITE_MAX,
        description="1 = gêne, 2 = problème net, 3 = rédhibitoire.",
    )


class SignauxAchat(BaseModel):
    """Signaux de comportement d'achat relevés dans une unité."""

    critere_choix: str | None = None
    frein: str | None = None
    declencheur: str | None = None
    occasion_usage: str | None = None
    mention_prix: str | None = None


class AnalyseUnite(BaseModel):
    """Cartographie d'une unité par le modèle d'extraction."""

    id_unite: str = Field(description="Identifiant exact fourni dans le lot.")
    sentiment: str = Field(
        default=SENTIMENT_NON_APPLICABLE,
        description=(
            "« positif », « negatif », « neutre », « mixte » ou « non_applicable » "
            "— du point de vue du consommateur vis-à-vis du type de produit."
        ),
    )
    themes: list[str] = Field(
        default_factory=list, description="0 à 3 libellés courts et libres."
    )
    pain_points: list[PainPointDetecte] = Field(default_factory=list)
    besoins: list[str] = Field(default_factory=list)
    attentes: list[str] = Field(default_factory=list)
    signaux_achat: SignauxAchat = Field(default_factory=SignauxAchat)
    verbatim_cle: bool = Field(
        default=False, description="Vrai si l'unité est citable telle quelle."
    )


class LotAnalysesUnites(BaseModel):
    """Enveloppe de lot — `with_structured_output` exige un objet racine."""

    analyses: list[AnalyseUnite] = Field(default_factory=list)


class AnalyseDocument(BaseModel):
    """Cartographie d'une page web par le modèle d'extraction."""

    id_unite: str
    retours_rapportes: list[PainPointDetecte] = Field(default_factory=list)
    besoins_rapportes: list[str] = Field(default_factory=list)
    elements_positifs: list[str] = Field(default_factory=list)
    position_editoriale: str | None = Field(
        default=None, description="« recommande », « mitige », « deconseille » ou null."
    )


class LotAnalysesDocuments(BaseModel):
    """Enveloppe de lot des analyses de documents."""

    analyses: list[AnalyseDocument] = Field(default_factory=list)


class EntreeNormalisation(BaseModel):
    """Regroupement d'un libellé normalisé et des libellés bruts qu'il absorbe."""

    libelle_normalise: str = Field(description="Libellé retenu, court et explicite.")
    libelles_source: list[str] = Field(
        default_factory=list, description="Libellés bruts exacts regroupés sous celui-ci."
    )


class TableNormalisation(BaseModel):
    """Table de correspondance produite par la chaîne de normalisation."""

    entrees: list[EntreeNormalisation] = Field(default_factory=list)


# --- Sorties des chaînes de synthèse --------------------------------------- #


class DescriptionPainPoint(BaseModel):
    """Rédaction associée à un pain point déjà chiffré par le code."""

    libelle: str = Field(description="Libellé normalisé, recopié à l'identique.")
    description: str = Field(description="2 à 3 phrases décrivant le problème constaté.")


class BesoinRedige(BaseModel):
    """Besoin consommateur structuré."""

    libelle: str
    description: str
    type: str = Field(
        default="fonctionnel",
        description="« fonctionnel », « emotionnel », « economique » ou « service ».",
    )
    preuves_id: list[str] = Field(
        default_factory=list, description="Identifiants d'unités fournis en entrée."
    )


class AttenteRedigee(BaseModel):
    """Attente consommateur structurée."""

    libelle: str
    description: str
    niveau_exigence: str = Field(
        default="standard",
        description="« standard » (attendu de tous) ou « differenciant ».",
    )
    preuves_id: list[str] = Field(default_factory=list)


class CommentaireSensibilitePrix(BaseModel):
    """Lecture de la sensibilité au prix."""

    niveau: str = Field(
        default="indeterminee",
        description="« forte », « moderee », « faible » ou « indeterminee ».",
    )
    commentaire: str = ""
    preuves_id: list[str] = Field(default_factory=list)


class SortieSyntheseInsights(BaseModel):
    """Sortie de la première chaîne de synthèse."""

    descriptions_pain_points: list[DescriptionPainPoint] = Field(default_factory=list)
    besoins: list[BesoinRedige] = Field(default_factory=list)
    attentes: list[AttenteRedigee] = Field(default_factory=list)
    signaux_positifs: list[BesoinRedige] = Field(
        default_factory=list, description="Ce que les consommateurs louent explicitement."
    )
    sensibilite_prix: CommentaireSensibilitePrix = Field(
        default_factory=CommentaireSensibilitePrix
    )
    commentaire_sentiment: str = ""
    divergences_sources: list[str] = Field(
        default_factory=list,
        description="Écarts factuels constatés entre sources ou entre portées.",
    )


class SortieLectureCritique(BaseModel):
    """Sortie de la seconde chaîne de synthèse."""

    biais_probables: list[str] = Field(default_factory=list)
    facteurs_confiance: list[str] = Field(default_factory=list)
    niveau_confiance: str = Field(
        default=CONFIANCE_FAIBLE, description="« elevee », « moyenne » ou « faible »."
    )
    justification_confiance: str = ""
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

    niveau: str = Field(default=CONFIANCE_FAIBLE)
    justification: str = ""
    facteurs: list[str] = Field(default_factory=list)


# =========================================================================== #
# 5. Sortie finale
# =========================================================================== #


class Verbatim(BaseModel):
    """Citation vérifiée, rattachée à une unité du corpus."""

    id_unite: str
    source: str
    extrait: str = Field(description="Sous-chaîne exacte du texte source, bornée.")


class PainPoint(BaseModel):
    """Problème consommateur hiérarchisé. Tous les nombres viennent du code."""

    libelle: str
    description: str = ""
    frequence_nb: int = 0
    frequence_pct: float = 0.0
    intensite_moyenne: float = 0.0
    score_priorite: float = 0.0
    sources: list[str] = Field(default_factory=list)
    portee: str = PORTEE_MIXTE
    verbatims: list[Verbatim] = Field(default_factory=list)
    confiance: str = CONFIANCE_FAIBLE


class Theme(BaseModel):
    """Thème récurrent du corpus."""

    libelle: str
    frequence_nb: int = 0
    frequence_pct: float = 0.0
    sentiment_dominant: str = SENTIMENT_NON_APPLICABLE
    sources: list[str] = Field(default_factory=list)
    portee: str = PORTEE_MIXTE
    exemples_id_unites: list[str] = Field(default_factory=list)


class Besoin(BaseModel):
    """Besoin consommateur documenté."""

    libelle: str
    description: str = ""
    type: str = "fonctionnel"
    preuves_id: list[str] = Field(default_factory=list)
    confiance: str = CONFIANCE_FAIBLE


class Attente(BaseModel):
    """Attente consommateur documentée."""

    libelle: str
    description: str = ""
    niveau_exigence: str = "standard"
    preuves_id: list[str] = Field(default_factory=list)


class ElementFrequence(BaseModel):
    """Élément de comportement d'achat et sa fréquence, calculée par le code."""

    libelle: str
    frequence_nb: int = 0
    preuves_id: list[str] = Field(default_factory=list)


class SensibilitePrix(BaseModel):
    """Lecture de la sensibilité au prix du corpus."""

    niveau: str = "indeterminee"
    commentaire: str = ""
    preuves_id: list[str] = Field(default_factory=list)


class ComportementsAchat(BaseModel):
    """Agrégats de comportement d'achat."""

    criteres_choix: list[ElementFrequence] = Field(default_factory=list)
    freins: list[ElementFrequence] = Field(default_factory=list)
    declencheurs: list[ElementFrequence] = Field(default_factory=list)
    occasions_usage: list[ElementFrequence] = Field(default_factory=list)
    sensibilite_prix: SensibilitePrix = Field(default_factory=SensibilitePrix)


class RepartitionSentiment(BaseModel):
    """Comptage de sentiments sur une base d'unités."""

    positif: int = 0
    negatif: int = 0
    neutre: int = 0
    mixte: int = 0
    base_nb: int = Field(default=0, description="Unités dont le sentiment est applicable.")


class Sentiment(BaseModel):
    """Répartitions de sentiment, globales et ventilées."""

    global_: RepartitionSentiment = Field(
        default_factory=RepartitionSentiment,
        serialization_alias="global",
        validation_alias="global",
    )
    par_source: dict[str, RepartitionSentiment] = Field(default_factory=dict)
    par_portee: dict[str, RepartitionSentiment] = Field(default_factory=dict)
    commentaire: str = ""

    model_config = ConfigDict(populate_by_name=True)


class StatsCorpus(BaseModel):
    """Description quantitative du corpus effectivement analysé."""

    nb_unites_par_source: dict[str, int] = Field(default_factory=dict)
    nb_unites_analysees: int = 0
    nb_documents_analyses: int = 0
    taux_echantillonnage: float = 1.0
    periode_couverte: dict[str, str | None] = Field(
        default_factory=lambda: {"min": None, "max": None}
    )
    repartition_portee: dict[str, int] = Field(default_factory=dict)
    langues_constatees: list[str] = Field(default_factory=list)


class ResultatInsightsConsommateurs(BaseModel):
    """Objet de sortie complet de l'agent d'analyse de l'axe 1."""

    produit: FicheProduit
    marche: ParametresMarche
    horodatage_utc: str = Field(
        description=(
            "Horodatage ISO 8601 UTC de production de l'analyse. Enrichissement "
            "propre à cet agent : aucun contrat amont n'en fournit, et l'agent "
            "de recommandations en a besoin pour qualifier la fraîcheur."
        )
    )
    sources_utilisees: list[SourceUtilisee] = Field(default_factory=list)
    alertes_coherence: list[AlerteCoherence] = Field(default_factory=list)
    stats_corpus: StatsCorpus = Field(default_factory=StatsCorpus)
    sentiment: Sentiment | None = None
    themes: list[Theme] = Field(default_factory=list)
    pain_points: list[PainPoint] = Field(default_factory=list)
    besoins: list[Besoin] = Field(default_factory=list)
    attentes: list[Attente] = Field(default_factory=list)
    comportements_achat: ComportementsAchat | None = None
    signaux_positifs: list[Besoin] = Field(default_factory=list)
    divergences_sources: list[str] = Field(default_factory=list)
    synthese_executive: str = ""
    statuts_analyse: list[StatutAnalyse] = Field(default_factory=list)
    donnees_suffisantes: bool = False
    confiance_globale: ConfianceGlobale = Field(default_factory=ConfianceGlobale)
    limites: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)


# =========================================================================== #
# 6. Conteneurs de travail internes
# =========================================================================== #


class EntreesChargees(BaseModel):
    """Fichiers d'entrée validés et en-tête retenu pour la sortie."""

    reddit: EntreeReddit | None = None
    amazon: EntreeAmazon | None = None
    web: EntreeRechercheWeb | None = None
    produit: FicheProduit | None = None
    marche: ParametresMarche | None = None
    limites_amont: list[str] = Field(default_factory=list)

    def au_moins_une_source(self) -> bool:
        """Indique si au moins un fichier exploitable a été chargé."""
        return any((self.reddit, self.amazon, self.web))


class CorpusPrepare(BaseModel):
    """Corpus prêt pour la cartographie."""

    unites: list[UniteConsommateur] = Field(default_factory=list)
    documents: list[DocumentWeb] = Field(default_factory=list)
    stats: StatsCorpus = Field(default_factory=StatsCorpus)
    limites: list[str] = Field(default_factory=list)


class Reduction(BaseModel):
    """Agrégats déterministes calculés à partir des analyses remappées."""

    themes: list[Theme] = Field(default_factory=list)
    pain_points: list[PainPoint] = Field(default_factory=list)
    sentiment: Sentiment | None = None
    comportements: ComportementsAchat | None = None
    verbatims_par_pain_point: dict[str, list[Verbatim]] = Field(default_factory=dict)
    besoins_bruts: list[ElementFrequence] = Field(default_factory=list)
    attentes_brutes: list[ElementFrequence] = Field(default_factory=list)
    elements_positifs_documents: list[str] = Field(default_factory=list)
    nb_unites_base: int = 0


class ErreurCoherenceProduit(Exception):
    """Produits différents entre deux fichiers d'entrée — mélange d'études interdit."""
