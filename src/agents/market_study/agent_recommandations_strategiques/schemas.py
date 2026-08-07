"""Contrats Pydantic v2 : consommation des sorties F3/F4/Tendances, dossier de
synthèse, sorties structurées des chaînes LLM et résultat final.

**Principe impératif des schémas de consommation** : re-déclaration minimale des
seuls champs consommés, `extra="ignore"`, et *aucun import du code des agents
amont*. Le couplage se fait par contrat JSON uniquement.

Le **dossier de synthèse** est la pièce centrale : il est construit par le code,
borné, et constitue le SEUL contenu qui atteint les chaînes LLM. Chaque élément
y porte une `ref` stable — c'est le vocabulaire de citation, et donc la
condition de traçabilité de tout fondement.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from config import (
    CONFIANCE_FAIBLE,
    STATUT_REGLE,
    TYPE_HYPOTHESE,
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


# --- F3 : insights consommateurs ------------------------------------------- #


class RepartitionSentimentEntree(SchemaConsomme):
    """Répartition de sentiment héritée de F3."""

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
    commentaire: str = ""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class PainPointEntree(SchemaConsomme):
    """Pain point hiérarchisé par F3."""

    libelle: str
    description: str = ""
    frequence_pct: float = 0.0
    intensite_moyenne: float = 0.0
    score_priorite: float = 0.0
    portee: str = ""
    confiance: str = CONFIANCE_FAIBLE


class BesoinEntree(SchemaConsomme):
    """Besoin ou signal positif documenté par F3."""

    libelle: str
    description: str = ""
    type: str = ""
    confiance: str = CONFIANCE_FAIBLE


class AttenteEntree(SchemaConsomme):
    """Attente documentée par F3."""

    libelle: str
    description: str = ""
    niveau_exigence: str = ""


class SensibilitePrixEntree(SchemaConsomme):
    """Lecture de la sensibilité au prix par F3."""

    niveau: str = "indeterminee"
    commentaire: str = ""


class ComportementsAchatEntree(SchemaConsomme):
    """Comportements d'achat de F3."""

    sensibilite_prix: SensibilitePrixEntree | None = None


class StatsCorpusEntree(SchemaConsomme):
    """Volumétrie du corpus F3."""

    nb_unites_analysees: int = 0
    nb_documents_analyses: int = 0
    nb_unites_par_source: dict[str, int] = Field(default_factory=dict)


class EntreeInsights(SchemaConsomme):
    """Sortie de `agent_insights_consommateurs` (F3)."""

    produit: FicheProduit
    marche: ParametresMarche
    horodatage_utc: str | None = None
    sentiment: SentimentEntree | None = None
    pain_points: list[PainPointEntree] = Field(default_factory=list)
    besoins: list[BesoinEntree] = Field(default_factory=list)
    attentes: list[AttenteEntree] = Field(default_factory=list)
    comportements_achat: ComportementsAchatEntree | None = None
    signaux_positifs: list[BesoinEntree] = Field(default_factory=list)
    divergences_sources: list[str] = Field(default_factory=list)
    stats_corpus: StatsCorpusEntree | None = None
    donnees_suffisantes: bool = False
    confiance_globale: ConfianceHeritee | None = None
    limites: list[str] = Field(default_factory=list)


# --- F4 : analyse concurrentielle ------------------------------------------ #


class StatsConcurrentEntree(SchemaConsomme):
    """Statistiques d'un concurrent, calculées par F4."""

    fourchette_prix_par_devise: dict[str, str] = Field(default_factory=dict)
    note_moyenne: float | None = None
    nb_offres: int = 0
    volume_ventes_cumule: int | None = None
    nb_annonces_actives: int = 0


class AnalyseConcurrentEntree(SchemaConsomme):
    """Analyse qualitative d'un concurrent par F4."""

    proposition_valeur: str = ""
    niveau_menace: str = ""
    justification_menace: str = ""


class ConcurrentIdentiteEntree(SchemaConsomme):
    """Identité d'un concurrent consolidé."""

    nom_canonique: str
    type: str = ""
    presence: dict[str, int] = Field(default_factory=dict)


class FicheConcurrentEntree(SchemaConsomme):
    """Fiche concurrent complète de F4."""

    concurrent: ConcurrentIdentiteEntree
    stats: StatsConcurrentEntree | None = None
    analyse: AnalyseConcurrentEntree | None = None


class SegmentPrixEntree(SchemaConsomme):
    """Segment de prix d'une source."""

    nom: str = ""
    borne_basse: float = 0.0
    borne_haute: float = 0.0


class BenchmarkSourceEntree(SchemaConsomme):
    """Benchmark de prix d'une source et d'une devise."""

    source: str
    devise: str
    nb_offres_avec_prix: int = 0
    prix_min: float = 0.0
    prix_mediane: float = 0.0
    prix_max: float = 0.0
    dispersion: float = 0.0
    segments: list[SegmentPrixEntree] = Field(default_factory=list)


class PositionPrixEntree(SchemaConsomme):
    """Position du prix envisagé, calculée par F4."""

    prix: float | None = None
    devise: str | None = None
    source_comparable: str | None = None
    percentile: float | None = None
    segment: str | None = None
    ecart_mediane_pct: float | None = None
    commentaire: str = ""


class IntensiteEntree(SchemaConsomme):
    """Intensité concurrentielle mesurée par F4."""

    nb_concurrents_identifies: int = 0
    nb_offres_coeur: int = 0
    nb_annonceurs: int = 0
    nb_annonces_actives: int = 0
    duree_diffusion_mediane_jours: float | None = None
    plateformes_dominantes: list[str] = Field(default_factory=list)
    concentration_volumes_top3_pct: float | None = None
    lecture: str = ""
    confiance: str = CONFIANCE_FAIBLE


class PointEtayeEntree(SchemaConsomme):
    """Constat étayé produit par F4."""

    point: str
    statut: str = TYPE_HYPOTHESE


class PositionnementEntree(SchemaConsomme):
    """Positionnement observé par F4."""

    axes_observes: list[str] = Field(default_factory=list)
    messages_dominants: list[PointEtayeEntree] = Field(default_factory=list)
    angles_peu_exploites: list[PointEtayeEntree] = Field(default_factory=list)
    facteurs_cles_succes: list[PointEtayeEntree] = Field(default_factory=list)
    normes_marche: list[PointEtayeEntree] = Field(default_factory=list)


class DifferenciationEntree(SchemaConsomme):
    """Différenciation établie par F4."""

    attributs_partages: list[PointEtayeEntree] = Field(default_factory=list)
    attributs_distinctifs_potentiels: list[PointEtayeEntree] = Field(default_factory=list)
    desavantages_apparents: list[PointEtayeEntree] = Field(default_factory=list)


class ValiditeRegionaleEntree(SchemaConsomme):
    """Portée régionale d'une source, qualifiée par F4."""

    source: str
    portee: str = ""
    commentaire: str = ""


class EntreeConcurrence(SchemaConsomme):
    """Sortie de `agent_analyse_concurrentielle` (F4)."""

    produit: FicheProduit
    marche: ParametresMarche
    horodatage_utc: str | None = None
    concurrents: list[FicheConcurrentEntree] = Field(default_factory=list)
    benchmark_prix: list[BenchmarkSourceEntree] = Field(default_factory=list)
    position_prix_envisage: PositionPrixEntree | None = None
    intensite_concurrentielle: IntensiteEntree | None = None
    positionnement: PositionnementEntree | None = None
    differenciation: DifferenciationEntree | None = None
    validite_regionale: list[ValiditeRegionaleEntree] = Field(default_factory=list)
    donnees_suffisantes: bool = False
    confiance_globale: ConfianceHeritee | None = None
    limites: list[str] = Field(default_factory=list)


# --- Tendances -------------------------------------------------------------- #


class MotsClesEntree(SchemaConsomme):
    """Jeu de mots-clés effectivement interrogé."""

    terme_pivot: str = ""
    fallback_applique: bool = False
    niveau_repli: int = 0


class SaisonnaliteEntree(SchemaConsomme):
    """Profil saisonnier calculé par le collecteur Tendances."""

    mois_pic: int | None = None
    mois_creux: int | None = None
    amplitude: float | None = None


class IndicateursEntree(SchemaConsomme):
    """Indicateurs quantitatifs de tendance."""

    indice_moyen_12m: float | None = None
    momentum_90j: float | None = None
    pente_annuelle_5ans: float | None = None
    volatilite: float | None = None
    saisonnalite: SaisonnaliteEntree | None = None
    nb_breakout: int = 0
    signal_effet_de_mode: bool = False
    profil_courbe: str = ""
    concentration_geo: list[dict] = Field(default_factory=list)


class RequeteEmergenteEntree(SchemaConsomme):
    """Requête associée en progression."""

    requete: str
    variation: str = ""
    est_breakout: bool = False


class StatutCollecteEntree(SchemaConsomme):
    """Compte rendu d'un appel du collecteur Tendances."""

    horizon: str = ""
    succes: bool = False
    message_erreur: str | None = None


class EntreeTendances(SchemaConsomme):
    """Sortie du collecteur Tendances."""

    produit: FicheProduit
    marche: ParametresMarche
    mots_cles: MotsClesEntree | None = None
    indicateurs: IndicateursEntree | None = None
    requetes_emergentes: list[RequeteEmergenteEntree] = Field(default_factory=list)
    statuts_collecte: list[StatutCollecteEntree] = Field(default_factory=list)
    donnees_disponibles: bool = False
    limites: list[str] = Field(default_factory=list)


# =========================================================================== #
# 2. Dossier de synthèse — construit par le code
# =========================================================================== #


class ElementDossier(BaseModel):
    """Élément citable du dossier, porteur de sa référence stable."""

    ref: str = Field(description="Référence stable, ex. « insights.pain_points[2] ».")
    libelle: str
    valeur: str = Field(default="", description="Valeur exacte, recopiée du dossier.")
    detail: str = ""


class SignauxDemande(BaseModel):
    """Extraits Tendances retenus pour l'analyse."""

    terme_pivot: str = ""
    fallback_applique: bool = False
    indicateurs: list[ElementDossier] = Field(default_factory=list)
    requetes_emergentes: list[ElementDossier] = Field(default_factory=list)
    effet_de_mode: bool = Field(
        default=False,
        description="Drapeau posé par le code : signal fort ou profil « effet_de_mode ».",
    )
    motif_effet_de_mode: str = ""


class SignauxConsommateur(BaseModel):
    """Extraits F3 retenus pour l'analyse."""

    pain_points: list[ElementDossier] = Field(default_factory=list)
    besoins: list[ElementDossier] = Field(default_factory=list)
    attentes: list[ElementDossier] = Field(default_factory=list)
    signaux_positifs: list[ElementDossier] = Field(default_factory=list)
    sentiment: ElementDossier | None = None
    sensibilite_prix: ElementDossier | None = None
    divergences_sources: list[str] = Field(default_factory=list)
    confiance_f3: str = CONFIANCE_FAIBLE


class SignauxConcurrence(BaseModel):
    """Extraits F4 retenus pour l'analyse."""

    intensite: list[ElementDossier] = Field(default_factory=list)
    benchmark: list[ElementDossier] = Field(default_factory=list)
    position_prix: ElementDossier | None = None
    angles_peu_exploites: list[ElementDossier] = Field(default_factory=list)
    facteurs_cles_succes: list[ElementDossier] = Field(default_factory=list)
    differenciation: list[ElementDossier] = Field(default_factory=list)
    menaces: list[ElementDossier] = Field(default_factory=list)
    validite_regionale: list[str] = Field(default_factory=list)
    devises_benchmark: list[str] = Field(default_factory=list)
    bornes_benchmark: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description="Devise → {min, max} sur toutes sources : garde-fou des fourchettes.",
    )
    confiance_f4: str = CONFIANCE_FAIBLE


class QualiteEntree(BaseModel):
    """Qualité d'une entrée : présence, fraîcheur, confiance héritée."""

    entree: str
    presente: bool = False
    donnees_suffisantes: bool = False
    confiance_heritee: str | None = None
    horodatage: str | None = None
    age_jours: int | None = None
    fraicheur_qualifiable: bool = False
    avertissements: list[str] = Field(default_factory=list)


class QualiteDonnees(BaseModel):
    """Qualité de l'ensemble des entrées."""

    entrees: list[QualiteEntree] = Field(default_factory=list)
    nb_entrees_presentes: int = 0
    nb_entrees_degradees: int = 0

    def presente(self, nom: str) -> bool:
        """Indique si une entrée donnée est présente et exploitable.

        Args:
            nom: Nom de l'entrée (« tendances », « insights », « concurrence »).

        Returns:
            Vrai si l'entrée a été chargée.
        """
        return any(e.entree == nom and e.presente for e in self.entrees)


class DossierSynthese(BaseModel):
    """Dossier compact — SEUL contenu transmis aux chaînes LLM."""

    demande: SignauxDemande | None = None
    consommateur: SignauxConsommateur | None = None
    concurrence: SignauxConcurrence | None = None
    qualite_donnees: QualiteDonnees = Field(default_factory=QualiteDonnees)

    def references(self) -> set[str]:
        """Collecte toutes les références citables du dossier.

        Returns:
            L'ensemble des `ref` valides.
        """
        refs: set[str] = set()

        def ajouter(elements) -> None:
            for element in elements:
                if isinstance(element, ElementDossier):
                    refs.add(element.ref)

        if self.demande:
            ajouter(self.demande.indicateurs)
            ajouter(self.demande.requetes_emergentes)
        if self.consommateur:
            ajouter(self.consommateur.pain_points)
            ajouter(self.consommateur.besoins)
            ajouter(self.consommateur.attentes)
            ajouter(self.consommateur.signaux_positifs)
            ajouter([e for e in (self.consommateur.sentiment, self.consommateur.sensibilite_prix) if e])
        if self.concurrence:
            ajouter(self.concurrence.intensite)
            ajouter(self.concurrence.benchmark)
            ajouter(self.concurrence.angles_peu_exploites)
            ajouter(self.concurrence.facteurs_cles_succes)
            ajouter(self.concurrence.differenciation)
            ajouter(self.concurrence.menaces)
            ajouter([e for e in (self.concurrence.position_prix,) if e])
        return refs

    def valeurs(self) -> dict[str, str]:
        """Associe chaque référence à sa valeur exacte.

        Returns:
            Le dictionnaire `ref → valeur`, servant à écraser les valeurs LLM.
        """
        table: dict[str, str] = {}

        def ajouter(elements) -> None:
            for element in elements:
                if isinstance(element, ElementDossier):
                    table[element.ref] = element.valeur or element.libelle

        if self.demande:
            ajouter(self.demande.indicateurs)
            ajouter(self.demande.requetes_emergentes)
        if self.consommateur:
            ajouter(self.consommateur.pain_points)
            ajouter(self.consommateur.besoins)
            ajouter(self.consommateur.attentes)
            ajouter(self.consommateur.signaux_positifs)
            ajouter([e for e in (self.consommateur.sentiment, self.consommateur.sensibilite_prix) if e])
        if self.concurrence:
            ajouter(self.concurrence.intensite)
            ajouter(self.concurrence.benchmark)
            ajouter(self.concurrence.angles_peu_exploites)
            ajouter(self.concurrence.facteurs_cles_succes)
            ajouter(self.concurrence.differenciation)
            ajouter(self.concurrence.menaces)
            ajouter([e for e in (self.concurrence.position_prix,) if e])
        return table


# =========================================================================== #
# 3. Sorties structurées et modèles de résultat
# =========================================================================== #


class Fondement(BaseModel):
    """Élément justifiant une affirmation, typé et référencé."""

    type: str = Field(
        default=TYPE_HYPOTHESE,
        description="« fait » (adossé à une ref du dossier) ou « hypothese ».",
    )
    ref: str | None = Field(
        default=None,
        description="Référence du dossier — OBLIGATOIRE si type vaut « fait ».",
    )
    detail: str = ""


class NoteCritere(BaseModel):
    """Note d'un critère de la grille de potentiel."""

    critere: str = Field(description="Identifiant issu de GRILLE_CRITERES.")
    score: int | None = Field(default=None, description="0, 1 ou 2 ; null si non évaluable.")
    non_evaluable: bool = False
    justification: str = ""
    fondements: list[Fondement] = Field(default_factory=list)
    plafonnement_applique: str | None = Field(
        default=None, description="Motif de plafonnement posé PAR LE CODE."
    )


class GrilleNotee(BaseModel):
    """Enveloppe de sortie de la chaîne de notation."""

    notes: list[NoteCritere] = Field(default_factory=list)


class VerdictPotentiel(BaseModel):
    """Verdict de potentiel — calculé par le code, jamais par le modèle."""

    verdict: str = VERDICT_INDETERMINE
    declenche_plc: bool = Field(
        default=False,
        description=(
            "Porte d'entrée du futur module de cycle de vie. Vrai UNIQUEMENT si le "
            "verdict est positif. Aucune classification de phase n'est produite ici."
        ),
    )
    score_total: int = 0
    nb_criteres_evalues: int = 0
    grille: list[NoteCritere] = Field(default_factory=list)
    regle_appliquee: str = ""
    statut_regle: str = STATUT_REGLE
    confiance: str = CONFIANCE_FAIBLE
    conditions_reexamen: list[str] = Field(default_factory=list)


class PointDiagnostic(BaseModel):
    """Convergence constatée entre sources."""

    constat: str
    fondements: list[Fondement] = Field(default_factory=list)


class Contradiction(BaseModel):
    """Contradiction entre sources, exposée sans être tranchée."""

    constat: str
    fondements: list[Fondement] = Field(default_factory=list)
    lecture_prudente: str = Field(
        default="",
        description=(
            "Explications possibles de la contradiction, exposées SANS trancher "
            "arbitrairement en faveur de l'une d'elles."
        ),
    )


class Diagnostic(BaseModel):
    """Diagnostic croisé des trois faces du marché."""

    convergences: list[PointDiagnostic] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    lecture_marche: str = ""
    fenetre_opportunite: str | None = None


class Recommandation(BaseModel):
    """Recommandation actionnable, priorisée et fondée."""

    id_reco: str = Field(description="Identifiant stable, ex. « reco-produit-1 ».")
    domaine: str
    enonce: str
    justification: str = ""
    fondements: list[Fondement] = Field(default_factory=list)
    priorite: str = "P3"
    horizon: str = "moyen_terme"
    impact_attendu: str = ""
    effort_estime: str = "moyen"
    risques_associes: list[str] = Field(default_factory=list)
    indicateurs_suivi: list[str] = Field(default_factory=list)


class FourchettePrix(BaseModel):
    """Fourchette de prix recommandée, dans une devise du benchmark."""

    devise: str
    min: float
    max: float
    logique_ancrage: str = ""


class RecommandationPrix(BaseModel):
    """Recommandation de positionnement prix."""

    strategie: str = ""
    fourchettes: list[FourchettePrix] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    fondements: list[Fondement] = Field(default_factory=list)


class Opportunite(BaseModel):
    """Opportunité ancrée dans le dossier."""

    libelle: str
    description: str = ""
    fondements: list[Fondement] = Field(default_factory=list)
    conditions_de_capture: list[str] = Field(default_factory=list)


class Risque(BaseModel):
    """Risque identifié et son atténuation."""

    libelle: str
    type: str = "marche"
    gravite: str = "moyenne"
    fondements: list[Fondement] = Field(default_factory=list)
    attenuation: str = ""


class FaitCle(BaseModel):
    """Donnée déterminante du dossier, recopiée par le code."""

    enonce: str
    ref: str
    valeur: str = ""


class SortieRecommandations(BaseModel):
    """Sortie de la chaîne de recommandations par domaine."""

    recommandations_produit: list[Recommandation] = Field(default_factory=list)
    recommandation_prix: RecommandationPrix | None = None
    recommandation_positionnement: Recommandation | None = None
    recommandations_marketing: list[Recommandation] = Field(default_factory=list)
    donnees_a_completer: list[str] = Field(default_factory=list)


class SortieOpportunitesRisques(BaseModel):
    """Sortie de la chaîne opportunités et risques."""

    opportunites: list[Opportunite] = Field(default_factory=list)
    risques: list[Risque] = Field(default_factory=list)


class SortieRestitution(BaseModel):
    """Sortie de la chaîne de faits clés et de synthèse exécutive."""

    faits_cles: list[FaitCle] = Field(default_factory=list)
    hypotheses_globales: list[str] = Field(default_factory=list)
    synthese_executive: str = Field(default="", description="15 lignes maximum.")


class SortieConditionsReexamen(BaseModel):
    """Sortie de la mini-chaîne de conditions de réexamen."""

    conditions: list[str] = Field(default_factory=list)


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


class ResultatRecommandations(BaseModel):
    """Objet de sortie complet de l'agent de l'axe 3."""

    produit: FicheProduit
    marche: ParametresMarche
    horodatage_utc: str
    sources_utilisees: list[SourceUtilisee] = Field(default_factory=list)
    alertes_coherence: list[AlerteCoherence] = Field(default_factory=list)
    dossier_synthese: DossierSynthese = Field(
        default_factory=DossierSynthese,
        description="Écho intégral — c'est lui qui rend les fondements vérifiables.",
    )
    diagnostic: Diagnostic | None = None
    verdict_potentiel: VerdictPotentiel = Field(default_factory=VerdictPotentiel)
    recommandations_produit: list[Recommandation] = Field(default_factory=list)
    recommandation_prix: RecommandationPrix | None = None
    recommandation_positionnement: Recommandation | None = None
    recommandations_marketing: list[Recommandation] = Field(default_factory=list)
    opportunites: list[Opportunite] = Field(default_factory=list)
    risques: list[Risque] = Field(default_factory=list)
    donnees_a_completer: list[str] = Field(default_factory=list)
    faits_cles: list[FaitCle] = Field(default_factory=list)
    hypotheses_globales: list[str] = Field(default_factory=list)
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

    insights: EntreeInsights | None = None
    concurrence: EntreeConcurrence | None = None
    tendances: EntreeTendances | None = None
    produit: FicheProduit | None = None
    marche: ParametresMarche | None = None
    limites_amont: list[str] = Field(default_factory=list)

    def au_moins_une(self) -> bool:
        """Indique si au moins une entrée exploitable a été chargée."""
        return any((self.insights, self.concurrence, self.tendances))


class ErreurCoherenceProduit(Exception):
    """Produits différents entre deux fichiers d'entrée — mélange d'études interdit."""
