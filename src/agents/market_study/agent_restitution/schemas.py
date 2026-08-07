"""Contrats Pydantic v2 : consommation des sorties F3/F4/F5/F6, données
injectables construites par le code, sortie des chaînes de rédaction et
résultat final.

**Principe impératif des schémas de consommation** : re-déclaration minimale des
seuls champs consommés, `extra="ignore"`, et *aucun import du code des agents
amont*. Le couplage se fait par contrat JSON uniquement.

Les **données injectables** sont la pièce centrale : construites par le code,
elles portent tout ce qui est chiffré, tabulaire ou structurel. Les chaînes de
rédaction n'en voient qu'une tranche — celle de leur section — et n'ont donc
aucun moyen d'inventer un chiffre appartenant à une autre.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from config import (
    CONFIANCE_FAIBLE,
    VERDICT_INDETERMINE,
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
    """Marché de l'étude."""

    geo: str
    langue: str
    devise: str | None = None


class ConfianceHeritee(SchemaConsomme):
    """Confiance globale déclarée par un agent amont."""

    niveau: str = CONFIANCE_FAIBLE
    justification: str = ""
    facteurs: list[str] = Field(default_factory=list)


class SourceUtiliseeEntree(SchemaConsomme):
    """Compte rendu de chargement publié par un agent amont."""

    source: str = ""
    donnees_disponibles: bool = False
    nb_items_charges: int = 0
    nb_items_exploites: int = 0


class ElementDossierEntree(SchemaConsomme):
    """Élément citable d'un dossier amont, avec sa ref stable."""

    ref: str = ""
    libelle: str = ""
    valeur: str = ""
    detail: str = ""


class FondementEntree(SchemaConsomme):
    """Fondement d'une affirmation amont."""

    type: str = ""
    ref: str | None = None
    detail: str = ""


# --- F5 : recommandations stratégiques (requise) --------------------------- #


class NoteCritereEntree(SchemaConsomme):
    """Note d'un critère de la grille de potentiel."""

    critere: str = ""
    score: int | None = None
    non_evaluable: bool = False
    justification: str = ""
    plafonnement_applique: str | None = None


class VerdictPotentielEntree(SchemaConsomme):
    """Verdict de potentiel calculé par F5 — recopié tel quel, jamais recalculé."""

    verdict: str = VERDICT_INDETERMINE
    declenche_plc: bool = False
    score_total: int = 0
    nb_criteres_evalues: int = 0
    grille: list[NoteCritereEntree] = Field(default_factory=list)
    regle_appliquee: str = ""
    statut_regle: str = ""
    confiance: str = CONFIANCE_FAIBLE
    conditions_reexamen: list[str] = Field(default_factory=list)


class SignauxDemandeEntree(SchemaConsomme):
    """Écho Tendances du dossier de synthèse F5."""

    terme_pivot: str = ""
    fallback_applique: bool = False
    indicateurs: list[ElementDossierEntree] = Field(default_factory=list)
    requetes_emergentes: list[ElementDossierEntree] = Field(default_factory=list)
    effet_de_mode: bool = False
    motif_effet_de_mode: str = ""


class SignauxConsommateurEntree(SchemaConsomme):
    """Écho F3 du dossier de synthèse F5 — support du mode dégradé."""

    pain_points: list[ElementDossierEntree] = Field(default_factory=list)
    besoins: list[ElementDossierEntree] = Field(default_factory=list)
    attentes: list[ElementDossierEntree] = Field(default_factory=list)
    signaux_positifs: list[ElementDossierEntree] = Field(default_factory=list)
    sentiment: ElementDossierEntree | None = None
    sensibilite_prix: ElementDossierEntree | None = None
    divergences_sources: list[str] = Field(default_factory=list)
    confiance_f3: str = CONFIANCE_FAIBLE


class SignauxConcurrenceEntree(SchemaConsomme):
    """Écho F4 du dossier de synthèse F5 — support du mode dégradé."""

    intensite: list[ElementDossierEntree] = Field(default_factory=list)
    benchmark: list[ElementDossierEntree] = Field(default_factory=list)
    position_prix: ElementDossierEntree | None = None
    angles_peu_exploites: list[ElementDossierEntree] = Field(default_factory=list)
    facteurs_cles_succes: list[ElementDossierEntree] = Field(default_factory=list)
    differenciation: list[ElementDossierEntree] = Field(default_factory=list)
    menaces: list[ElementDossierEntree] = Field(default_factory=list)
    validite_regionale: list[str] = Field(default_factory=list)
    confiance_f4: str = CONFIANCE_FAIBLE


class DossierSyntheseEntree(SchemaConsomme):
    """Dossier de synthèse de F5, consommé intégralement."""

    demande: SignauxDemandeEntree | None = None
    consommateur: SignauxConsommateurEntree | None = None
    concurrence: SignauxConcurrenceEntree | None = None


class RecommandationEntree(SchemaConsomme):
    """Recommandation produite par un agent amont."""

    id_reco: str = ""
    domaine: str = ""
    enonce: str = ""
    justification: str = ""
    fondements: list[FondementEntree] = Field(default_factory=list)
    priorite: str = "P3"
    horizon: str = ""
    impact_attendu: str = ""
    effort_estime: str = ""
    risques_associes: list[str] = Field(default_factory=list)
    indicateurs_suivi: list[str] = Field(default_factory=list)


class FourchettePrixEntree(SchemaConsomme):
    """Fourchette de prix recommandée par F5."""

    devise: str = ""
    min: float = 0.0
    max: float = 0.0
    logique_ancrage: str = ""


class RecommandationPrixEntree(SchemaConsomme):
    """Recommandation de positionnement prix de F5."""

    strategie: str = ""
    fourchettes: list[FourchettePrixEntree] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)


class OpportuniteEntree(SchemaConsomme):
    """Opportunité identifiée par F5."""

    libelle: str = ""
    description: str = ""
    conditions_de_capture: list[str] = Field(default_factory=list)


class RisqueEntree(SchemaConsomme):
    """Risque identifié par F5."""

    libelle: str = ""
    type: str = ""
    gravite: str = ""
    attenuation: str = ""


class FaitCleEntree(SchemaConsomme):
    """Fait clé publié par un agent amont."""

    enonce: str = ""
    ref: str = ""
    valeur: str = ""


class ContradictionEntree(SchemaConsomme):
    """Contradiction exposée par le diagnostic de F5."""

    constat: str = ""
    lecture_prudente: str = ""


class PointDiagnosticEntree(SchemaConsomme):
    """Convergence constatée par le diagnostic de F5."""

    constat: str = ""


class DiagnosticEntree(SchemaConsomme):
    """Diagnostic croisé de F5."""

    convergences: list[PointDiagnosticEntree] = Field(default_factory=list)
    contradictions: list[ContradictionEntree] = Field(default_factory=list)
    lecture_marche: str = ""
    fenetre_opportunite: str | None = None


class EntreeRecommandations(SchemaConsomme):
    """Sortie de `agent_recommandations_strategiques` (F5) — requise."""

    produit: FicheProduit
    marche: ParametresMarche
    horodatage_utc: str | None = None
    sources_utilisees: list[SourceUtiliseeEntree] = Field(default_factory=list)
    dossier_synthese: DossierSyntheseEntree | None = None
    diagnostic: DiagnosticEntree | None = None
    verdict_potentiel: VerdictPotentielEntree = Field(
        default_factory=VerdictPotentielEntree
    )
    recommandations_produit: list[RecommandationEntree] = Field(default_factory=list)
    recommandation_prix: RecommandationPrixEntree | None = None
    recommandation_positionnement: RecommandationEntree | None = None
    recommandations_marketing: list[RecommandationEntree] = Field(default_factory=list)
    opportunites: list[OpportuniteEntree] = Field(default_factory=list)
    risques: list[RisqueEntree] = Field(default_factory=list)
    donnees_a_completer: list[str] = Field(default_factory=list)
    faits_cles: list[FaitCleEntree] = Field(default_factory=list)
    synthese_executive: str = ""
    donnees_suffisantes: bool = False
    confiance_globale: ConfianceHeritee | None = None
    limites: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)


# --- F3 : insights consommateurs (optionnelle) ----------------------------- #


class RepartitionSentimentEntree(SchemaConsomme):
    """Répartition de sentiment d'une source ou du corpus entier."""

    positif: int = 0
    negatif: int = 0
    neutre: int = 0
    mixte: int = 0
    base_nb: int = 0


class SentimentEntree(SchemaConsomme):
    """Bloc sentiment de F3."""

    global_: RepartitionSentimentEntree | None = Field(
        default=None, validation_alias="global", serialization_alias="global"
    )
    par_source: dict[str, RepartitionSentimentEntree] = Field(default_factory=dict)
    commentaire: str = ""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class VerbatimEntree(SchemaConsomme):
    """Extrait de corpus attaché à une difficulté rapportée."""

    id_unite: str = ""
    source: str = ""
    extrait: str = ""


class PainPointEntree(SchemaConsomme):
    """Difficulté rapportée, hiérarchisée par F3."""

    libelle: str = ""
    description: str = ""
    frequence_nb: int = 0
    frequence_pct: float = 0.0
    intensite_moyenne: float = 0.0
    score_priorite: float = 0.0
    portee: str = ""
    sources: list[str] = Field(default_factory=list)
    verbatims: list[VerbatimEntree] = Field(default_factory=list)
    confiance: str = CONFIANCE_FAIBLE


class BesoinEntree(SchemaConsomme):
    """Besoin ou signal positif documenté par F3."""

    libelle: str = ""
    description: str = ""
    type: str = ""
    confiance: str = CONFIANCE_FAIBLE


class AttenteEntree(SchemaConsomme):
    """Attente documentée par F3."""

    libelle: str = ""
    description: str = ""
    niveau_exigence: str = ""


class ThemeEntree(SchemaConsomme):
    """Thème cartographié par F3."""

    libelle: str = ""
    frequence_nb: int = 0
    frequence_pct: float = 0.0
    sentiment_dominant: str = ""


class PeriodeCouverteEntree(SchemaConsomme):
    """Période couverte par le corpus consommateur."""

    min: str | None = None
    max: str | None = None


class StatsCorpusEntree(SchemaConsomme):
    """Volumétrie du corpus F3."""

    nb_unites_analysees: int = 0
    nb_documents_analyses: int = 0
    nb_unites_par_source: dict[str, int] = Field(default_factory=dict)
    periode_couverte: PeriodeCouverteEntree | None = None


class EntreeInsights(SchemaConsomme):
    """Sortie de `agent_insights_consommateurs` (F3) — optionnelle."""

    produit: FicheProduit
    marche: ParametresMarche
    horodatage_utc: str | None = None
    stats_corpus: StatsCorpusEntree | None = None
    sentiment: SentimentEntree | None = None
    themes: list[ThemeEntree] = Field(default_factory=list)
    pain_points: list[PainPointEntree] = Field(default_factory=list)
    besoins: list[BesoinEntree] = Field(default_factory=list)
    attentes: list[AttenteEntree] = Field(default_factory=list)
    signaux_positifs: list[BesoinEntree] = Field(default_factory=list)
    divergences_sources: list[str] = Field(default_factory=list)
    donnees_suffisantes: bool = False
    confiance_globale: ConfianceHeritee | None = None
    limites: list[str] = Field(default_factory=list)


# --- F4 : analyse concurrentielle (optionnelle) ---------------------------- #


class SegmentPrixEntree(SchemaConsomme):
    """Segment de prix d'une source et d'une devise."""

    nom: str = ""
    borne_basse: float = 0.0
    borne_haute: float = 0.0
    nb_offres: int = 0


class BenchmarkSourceEntree(SchemaConsomme):
    """Benchmark de prix d'une source et d'une devise."""

    source: str = ""
    devise: str = ""
    nb_offres_avec_prix: int = 0
    prix_min: float = 0.0
    prix_mediane: float = 0.0
    prix_max: float = 0.0
    dispersion: float = 0.0
    segments: list[SegmentPrixEntree] = Field(default_factory=list)


class IntensiteEntree(SchemaConsomme):
    """Intensité concurrentielle mesurée par F4."""

    nb_concurrents_identifies: int = 0
    nb_offres_coeur: int = 0
    nb_annonceurs: int = 0
    nb_annonces_actives: int = 0
    duree_diffusion_mediane_jours: float | None = None
    duree_diffusion_max_jours: float | None = None
    concentration_volumes_top3_pct: float | None = None
    lecture: str = ""
    confiance: str = CONFIANCE_FAIBLE


class PointEtayeEntree(SchemaConsomme):
    """Constat étayé produit par F4."""

    point: str = ""
    statut: str = ""


class PositionnementEntree(SchemaConsomme):
    """Positionnement observé par F4."""

    messages_dominants: list[PointEtayeEntree] = Field(default_factory=list)
    angles_peu_exploites: list[PointEtayeEntree] = Field(default_factory=list)
    facteurs_cles_succes: list[PointEtayeEntree] = Field(default_factory=list)
    normes_marche: list[PointEtayeEntree] = Field(default_factory=list)


class LigneComparatifEntree(SchemaConsomme):
    """Ligne du tableau comparatif des concurrents de F4."""

    concurrent: str = ""
    presence_sources: list[str] = Field(default_factory=list)
    fourchette_prix_par_devise: dict[str, str] = Field(default_factory=dict)
    note_moyenne: float | None = None
    volume_ventes_cumule: int | None = None
    argument_principal: str | None = None
    force_principale: str | None = None
    faiblesse_principale: str | None = None


class ValiditeRegionaleEntree(SchemaConsomme):
    """Portée régionale d'une source, qualifiée par F4."""

    source: str = ""
    portee: str = ""
    commentaire: str = ""


class ReferentielStatsEntree(SchemaConsomme):
    """Volumétrie du référentiel consolidé par F4."""

    nb_offres_par_source: dict[str, int] = Field(default_factory=dict)
    nb_offres_coeur: int = 0
    nb_annonces: int = 0
    nb_pages: int = 0
    nb_avis_indexes: int = 0


class EntreeConcurrence(SchemaConsomme):
    """Sortie de `agent_analyse_concurrentielle` (F4) — optionnelle."""

    produit: FicheProduit
    marche: ParametresMarche
    horodatage_utc: str | None = None
    referentiel_stats: ReferentielStatsEntree | None = None
    benchmark_prix: list[BenchmarkSourceEntree] = Field(default_factory=list)
    intensite_concurrentielle: IntensiteEntree | None = None
    positionnement: PositionnementEntree | None = None
    tableau_comparatif: list[LigneComparatifEntree] = Field(default_factory=list)
    validite_regionale: list[ValiditeRegionaleEntree] = Field(default_factory=list)
    donnees_suffisantes: bool = False
    confiance_globale: ConfianceHeritee | None = None
    limites: list[str] = Field(default_factory=list)


# --- F6 : cycle de vie (optionnelle) --------------------------------------- #


class DeclenchementEntree(SchemaConsomme):
    """Condition de déclenchement de la classification de phase."""

    declenche_plc_amont: bool = False
    mode: str = ""
    motif: str = ""


class ClassificationEntree(SchemaConsomme):
    """Classification de phase calculée par F6 — recopiée telle quelle."""

    phase_probable: str | None = None
    incertitude: str = ""
    scores_par_phase: dict[str, float] = Field(default_factory=dict)
    nb_familles_evaluees: int = 0
    regle_appliquee: str = ""
    statut_regle: str = ""
    confiance: str = CONFIANCE_FAIBLE


class OrientationSignalEntree(SchemaConsomme):
    """Orientation d'une famille de signaux, publiée par F6."""

    famille: str = ""
    non_evaluable: bool = False
    orientation_phase: str | None = None
    force: str | None = None
    justification: str = ""


class EntreePLC(SchemaConsomme):
    """Sortie de `agent_plc` (F6) — optionnelle."""

    produit: FicheProduit
    marche: ParametresMarche
    horodatage_utc: str | None = None
    declenchement: DeclenchementEntree = Field(default_factory=DeclenchementEntree)
    classification: ClassificationEntree | None = None
    signaux: list[OrientationSignalEntree] = Field(default_factory=list)
    recommandations_phase: list[RecommandationEntree] = Field(default_factory=list)
    conditions_reexamen: list[str] = Field(default_factory=list)
    donnees_suffisantes: bool = False
    confiance_globale: ConfianceHeritee | None = None
    limites: list[str] = Field(default_factory=list)


# =========================================================================== #
# 2. Données injectables — construites par le code
# =========================================================================== #


class Bascule(BaseModel):
    """Mutation mono-critère qui change réellement le verdict.

    Produite par la SIMULATION de la règle, jamais recopiée d'un texte libre.
    """

    critere: str
    score_actuel: str
    score_requis: int
    verdict_obtenu: str
    enonce: str


class Verbatim(BaseModel):
    """Extrait de corpus sélectionné PAR LE CODE, langue d'origine conservée."""

    texte: str
    id_unite: str
    source: str
    tronque: bool = False


class Injectables(BaseModel):
    """Tout ce que le code injecte dans le rapport.

    Chaque clé est consommée par une section précise. Le narratif d'une section
    ne reçoit que la tranche qui la concerne : une chaîne de rédaction n'a donc
    matériellement pas accès aux chiffres d'une autre section.
    """

    entete: dict[str, str] = Field(default_factory=dict)
    faits_cles: list[str] = Field(default_factory=list)
    recommandations_majeures: list[str] = Field(default_factory=list)
    risque_principal: str = ""
    verdict_lisible: str = ""
    verdict_brut: str = ""
    confiance_verdict: str = ""
    tableau_grille: str = ""
    regle_litterale: str = ""
    bascules: list[Bascule] = Field(default_factory=list)
    donnees_a_completer: list[str] = Field(default_factory=list)
    phase_lisible: str | None = None
    phase_brute: str | None = None
    incertitude_phase: str = ""
    tableau_signaux_plc: str = ""
    encart_plc: str = ""
    recommandations_phase: str = ""
    tableau_demande: str = ""
    tableau_besoins: str = ""
    tableau_attentes: str = ""
    pain_points: list[dict[str, str]] = Field(default_factory=list)
    verbatims: dict[str, Verbatim] = Field(default_factory=dict)
    tableau_sentiment: str = ""
    divergences: list[str] = Field(default_factory=list)
    tableau_intensite: str = ""
    tableau_concurrents: str = ""
    tableau_benchmark: str = ""
    portee_regionale: list[str] = Field(default_factory=list)
    normes_marche: list[str] = Field(default_factory=list)
    angles_peu_exploites: list[str] = Field(default_factory=list)
    tableaux_recommandations: dict[str, str] = Field(default_factory=dict)
    recommandation_prix: str = ""
    tableau_opportunites: str = ""
    tableau_risques: str = ""
    annexe_sources: str = ""
    annexe_periode: str = ""
    limites_par_famille: list[tuple[str, list[str]]] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    badges: dict[str, str] = Field(default_factory=dict)
    refs_par_section: dict[str, list[str]] = Field(default_factory=dict)
    mentions_partielles: dict[str, str] = Field(default_factory=dict)
    sections_degradees: list[str] = Field(default_factory=list)
    sections_absentes: list[str] = Field(default_factory=list)


class SortieNarratif(BaseModel):
    """Sortie d'une chaîne de rédaction — du texte, et rien d'autre."""

    paragraphes: list[str] = Field(
        default_factory=list, description="Paragraphes rédigés, sans titre ni puce."
    )
    puces: list[str] = Field(
        default_factory=list,
        description="Puces courtes ; utilisées uniquement pour les réserves majeures.",
    )


# =========================================================================== #
# 3. Socle commun des agents d'analyse
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
    """Compte rendu d'une phase de traitement."""

    phase: str
    succes: bool
    message_erreur: str | None = None
    nb_elements: int = 0
    nb_tentatives: int = 0


class ConfianceGlobale(BaseModel):
    """Niveau de confiance du rapport et sa justification."""

    niveau: str = CONFIANCE_FAIBLE
    justification: str = ""
    facteurs: list[str] = Field(default_factory=list)


# =========================================================================== #
# 4. Sortie finale
# =========================================================================== #


class SectionProduite(BaseModel):
    """Compte rendu d'une section du rapport."""

    id_section: str
    titre: str
    entrees_utilisees: list[str] = Field(default_factory=list)
    badge_confiance: str | None = None
    nb_mots_narratif: int = 0
    degradee: bool = Field(
        default=False,
        description="Vrai si la section est construite depuis l'écho de synthèse.",
    )
    refs_sources: list[str] = Field(default_factory=list)


class ControlesRestitution(BaseModel):
    """Résultat de la post-validation du rapport assemblé."""

    nb_nombres_verifies: int = 0
    nb_nombres_retires: int = Field(
        default=0, description="Nombres hors liste blanche retirés — 0 sur run sain."
    )
    verdict_conforme: bool = False
    phase_conforme: bool | None = None
    bascules_recalculees: bool = Field(
        default=True,
        description="Vrai : les bascules affichées viennent de la simulation du code.",
    )
    termes_interdits_retires: int = 0
    mentions_etude_partielle: list[str] = Field(default_factory=list)


class ResultatRestitution(BaseModel):
    """Objet de sortie complet de l'agent de restitution."""

    produit: FicheProduit
    marche: ParametresMarche
    horodatage_utc: str = ""
    sources_utilisees: list[SourceUtilisee] = Field(default_factory=list)
    alertes_coherence: list[AlerteCoherence] = Field(default_factory=list)
    sections_produites: list[SectionProduite] = Field(default_factory=list)
    controles: ControlesRestitution = Field(default_factory=ControlesRestitution)
    chemin_rapport: str | None = None
    chemin_resume: str | None = None
    synthese_executive: str = ""
    statuts_analyse: list[StatutAnalyse] = Field(default_factory=list)
    donnees_suffisantes: bool = False
    confiance_globale: ConfianceGlobale = Field(default_factory=ConfianceGlobale)
    limites: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)


# =========================================================================== #
# 5. Conteneurs de travail
# =========================================================================== #


class EntreesChargees(BaseModel):
    """Fichiers d'entrée validés et en-tête retenu."""

    recommandations: EntreeRecommandations | None = None
    insights: EntreeInsights | None = None
    concurrence: EntreeConcurrence | None = None
    plc: EntreePLC | None = None
    produit: FicheProduit | None = None
    marche: ParametresMarche | None = None
    limites_amont: list[str] = Field(default_factory=list)
    hypotheses_amont: list[str] = Field(default_factory=list)
    blocs_disponibles: dict[str, bool] = Field(default_factory=dict)

    def presente(self, nom: str) -> bool:
        """Indique si une entrée a été chargée.

        Args:
            nom: Nom court de l'entrée.

        Returns:
            Vrai si l'entrée est exploitable.
        """
        return bool(self.blocs_disponibles.get(nom, False))


class ErreurCoherenceProduit(Exception):
    """Produits différents entre deux fichiers d'entrée — mélange d'études interdit."""
