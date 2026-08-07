"""Contrats Pydantic v2 : consommation des sorties F5/F4/F3, dossier PLC,
sorties structurées des chaînes LLM et résultat final.

**Principe impératif des schémas de consommation** : re-déclaration minimale des
seuls champs consommés, `extra="ignore"`, et *aucun import du code des agents
amont*. Le couplage se fait par contrat JSON uniquement.

Le **dossier PLC** est la pièce centrale : construit par le code depuis les
entrées, il est le SEUL contenu qui atteint les chaînes LLM. Chaque indicateur y
porte une `ref` stable — c'est le vocabulaire de citation, et donc la condition
de traçabilité de toute orientation et de toute recommandation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from config import (
    CONFIANCE_FAIBLE,
    DOMAINE_PLC,
    INCERTITUDE_ELEVEE,
    MODE_NON_DECLENCHE,
    STATUT_REGLE,
    TYPE_HYPOTHESE,
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


# --- F5 : recommandations stratégiques (entrée requise) --------------------- #


class ElementDossierEntree(SchemaConsomme):
    """Élément citable du dossier de synthèse de F5, avec sa ref stable."""

    ref: str
    libelle: str = ""
    valeur: str = ""
    detail: str = ""


class VerdictPotentielEntree(SchemaConsomme):
    """Verdict de potentiel calculé par F5 — jamais recalculé ici."""

    verdict: str = ""
    declenche_plc: bool = False
    score_total: int = 0
    nb_criteres_evalues: int = 0
    confiance: str = CONFIANCE_FAIBLE
    conditions_reexamen: list[str] = Field(default_factory=list)


class DynamiquePublicitaireEntree(SchemaConsomme):
    """Dynamique publicitaire — exigence D4, absente des sorties F4 actuelles."""

    date_reference: str | None = None
    repartition_lancements_mensuels: dict[str, int] = Field(default_factory=dict)
    nb_lancements_recents: int | None = None
    part_lancements_recents: float | None = None
    anciennete_mediane_actives_jours: float | None = None
    anciennete_max_actives_jours: float | None = None
    part_annonces_actives: float | None = None
    nb_arrets_recents: int | None = None
    avertissement_date_fin: str = ""

    def renseignee(self) -> bool:
        """Indique si le bloc porte au moins un indicateur exploitable.

        Returns:
            Vrai si un indicateur au moins est renseigné.
        """
        return any(
            valeur is not None
            for valeur in (
                self.nb_lancements_recents,
                self.part_lancements_recents,
                self.anciennete_mediane_actives_jours,
                self.anciennete_max_actives_jours,
                self.part_annonces_actives,
                self.nb_arrets_recents,
            )
        ) or bool(self.repartition_lancements_mensuels)


class SignauxDemandeEntree(SchemaConsomme):
    """Écho Tendances du dossier de synthèse F5."""

    terme_pivot: str = ""
    fallback_applique: bool = False
    indicateurs: list[ElementDossierEntree] = Field(default_factory=list)
    effet_de_mode: bool = False
    motif_effet_de_mode: str = ""


class SignauxConcurrenceEntree(SchemaConsomme):
    """Écho F4 du dossier de synthèse F5."""

    intensite: list[ElementDossierEntree] = Field(default_factory=list)
    menaces: list[ElementDossierEntree] = Field(default_factory=list)
    confiance_f4: str = CONFIANCE_FAIBLE
    dynamique_publicitaire: DynamiquePublicitaireEntree | None = Field(
        default=None,
        description="Écho D4 — absent des sorties F5 actuelles, prévu pour plus tard.",
    )


class QualiteEntreeAmont(SchemaConsomme):
    """Qualité d'une entrée telle que qualifiée par F5."""

    entree: str = ""
    presente: bool = False
    donnees_suffisantes: bool = False
    confiance_heritee: str | None = None


class QualiteDonneesEntree(SchemaConsomme):
    """Qualité des entrées amont, telle que publiée par F5."""

    entrees: list[QualiteEntreeAmont] = Field(default_factory=list)
    nb_entrees_presentes: int = 0
    nb_entrees_degradees: int = 0


class DossierSyntheseEntree(SchemaConsomme):
    """Dossier de synthèse de F5, consommé en écho."""

    demande: SignauxDemandeEntree | None = None
    concurrence: SignauxConcurrenceEntree | None = None
    qualite_donnees: QualiteDonneesEntree | None = None


class EntreeRecommandations(SchemaConsomme):
    """Sortie de `agent_recommandations_strategiques` (F5) — requise."""

    produit: FicheProduit
    marche: ParametresMarche
    horodatage_utc: str | None = None
    verdict_potentiel: VerdictPotentielEntree = Field(
        default_factory=VerdictPotentielEntree
    )
    dossier_synthese: DossierSyntheseEntree | None = None
    donnees_suffisantes: bool = False
    confiance_globale: ConfianceHeritee | None = None
    limites: list[str] = Field(default_factory=list)


# --- F4 : analyse concurrentielle (optionnelle) ---------------------------- #


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
    dynamique_publicitaire: DynamiquePublicitaireEntree | None = Field(
        default=None,
        description=(
            "Exigence D4. Absente des sorties F4 au 06/08/2026 : la famille de "
            "signaux correspondante est alors non évaluable, jamais reconstituée."
        ),
    )


class ReferentielStatsEntree(SchemaConsomme):
    """Volumétrie du référentiel consolidé par F4."""

    nb_offres_par_source: dict[str, int] = Field(default_factory=dict)
    nb_offres_coeur: int = 0
    nb_annonces: int = 0
    nb_pages: int = 0
    nb_avis_indexes: int = 0


class StatsConcurrentEntree(SchemaConsomme):
    """Statistiques agrégées d'un concurrent."""

    nb_offres: int = 0
    nb_annonces_actives: int = 0


class ConcurrentIdentiteEntree(SchemaConsomme):
    """Identité d'un concurrent consolidé."""

    nom_canonique: str = ""
    type: str = ""


class FicheConcurrentEntree(SchemaConsomme):
    """Fiche concurrent de F4 — statistiques agrégées uniquement."""

    concurrent: ConcurrentIdentiteEntree = Field(
        default_factory=ConcurrentIdentiteEntree
    )
    stats: StatsConcurrentEntree | None = None


class EntreeConcurrence(SchemaConsomme):
    """Sortie de `agent_analyse_concurrentielle` (F4) — optionnelle."""

    produit: FicheProduit
    marche: ParametresMarche
    horodatage_utc: str | None = None
    intensite_concurrentielle: IntensiteEntree | None = None
    referentiel_stats: ReferentielStatsEntree | None = None
    concurrents: list[FicheConcurrentEntree] = Field(default_factory=list)
    donnees_suffisantes: bool = False
    confiance_globale: ConfianceHeritee | None = None
    limites: list[str] = Field(default_factory=list)


# --- F3 : insights consommateurs (optionnelle) ----------------------------- #


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
    donnees_suffisantes: bool = False
    confiance_globale: ConfianceHeritee | None = None
    limites: list[str] = Field(default_factory=list)


# =========================================================================== #
# 2. Dossier PLC — construit par le code
# =========================================================================== #


class IndicateurSignal(BaseModel):
    """Indicateur citable du dossier PLC, porteur de sa référence stable."""

    ref: str = Field(
        description="Référence stable, ex. « tendances.indicateurs.momentum_90j »."
    )
    libelle: str
    valeur: str = Field(default="", description="Valeur exacte, formatée par le code.")
    detail: str = Field(
        default="", description="Rappel d'interprétation opposable à toute conclusion."
    )


class FamilleSignaux(BaseModel):
    """Une des quatre familles de signaux temporels."""

    famille: str
    intitule: str = ""
    disponible: bool = False
    source_effective: str | None = None
    indicateurs: list[IndicateurSignal] = Field(default_factory=list)
    avertissements: list[str] = Field(default_factory=list)


class DossierPLC(BaseModel):
    """Dossier compact — SEUL contenu transmis aux chaînes LLM."""

    familles: list[FamilleSignaux] = Field(default_factory=list)
    verdict_amont: str = ""
    confiances_amont: dict[str, str | None] = Field(default_factory=dict)

    def references(self) -> set[str]:
        """Collecte toutes les références citables du dossier.

        Returns:
            L'ensemble des `ref` valides.
        """
        return {
            indicateur.ref
            for famille in self.familles
            for indicateur in famille.indicateurs
        }

    def valeurs(self) -> dict[str, str]:
        """Associe chaque référence à sa valeur exacte.

        Returns:
            Le dictionnaire `ref → valeur`, servant à écraser les valeurs LLM.
        """
        return {
            indicateur.ref: (indicateur.valeur or indicateur.libelle)
            for famille in self.familles
            for indicateur in famille.indicateurs
        }

    def famille(self, identifiant: str) -> FamilleSignaux | None:
        """Retourne une famille par son identifiant.

        Args:
            identifiant: Identifiant de la famille.

        Returns:
            La famille, ou `None` si elle est absente du dossier.
        """
        for famille in self.familles:
            if famille.famille == identifiant:
                return famille
        return None

    def familles_disponibles(self) -> list[str]:
        """Liste les familles effectivement exploitables.

        Returns:
            Les identifiants des familles disponibles.
        """
        return [f.famille for f in self.familles if f.disponible]


# =========================================================================== #
# 3. Sorties structurées des chaînes LLM
# =========================================================================== #


class Fondement(BaseModel):
    """Élément justifiant une affirmation, typé et référencé."""

    type: str = Field(
        default=TYPE_HYPOTHESE,
        description="« fait » (adossé à une ref du dossier PLC) ou « hypothese ».",
    )
    ref: str | None = Field(
        default=None,
        description="Référence du dossier PLC — OBLIGATOIRE si type vaut « fait ».",
    )
    detail: str = ""


class OrientationSignal(BaseModel):
    """Orientation d'une famille de signaux — proposée par le modèle."""

    famille: str = Field(description="Identifiant issu de FAMILLES_SIGNAUX.")
    non_evaluable: bool = False
    orientation_phase: str | None = Field(
        default=None, description="Une valeur de PHASES, ou « neutre »."
    )
    force: str | None = Field(default=None, description="faible | moyenne | forte.")
    justification: str = ""
    fondements: list[Fondement] = Field(default_factory=list)


class SortieOrientations(BaseModel):
    """Enveloppe de sortie de la chaîne d'orientation."""

    orientations: list[OrientationSignal] = Field(default_factory=list)


class Recommandation(BaseModel):
    """Recommandation actionnable, dédiée à la phase classée."""

    id_reco: str = Field(description="Identifiant stable, ex. « reco-plc-1 ».")
    domaine: str = DOMAINE_PLC
    enonce: str = ""
    justification: str = ""
    fondements: list[Fondement] = Field(default_factory=list)
    priorite: str = "P3"
    horizon: str = "moyen_terme"
    impact_attendu: str = ""
    effort_estime: str = "moyen"
    risques_associes: list[str] = Field(default_factory=list)
    indicateurs_suivi: list[str] = Field(default_factory=list)


class SortieRecommandationsPhase(BaseModel):
    """Sortie de la chaîne de recommandations de phase."""

    recommandations: list[Recommandation] = Field(default_factory=list)
    conditions_reexamen: list[str] = Field(default_factory=list)
    synthese_executive: str = Field(default="", description="10 lignes maximum.")


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


class FaitCle(BaseModel):
    """Donnée déterminante du dossier PLC, recopiée par le code."""

    enonce: str
    ref: str
    valeur: str = ""


# =========================================================================== #
# 5. Classification et sortie finale
# =========================================================================== #


class Declenchement(BaseModel):
    """Condition de déclenchement de la classification."""

    declenche_plc_amont: bool = False
    mode: str = MODE_NON_DECLENCHE
    motif: str = ""


class Classification(BaseModel):
    """Classification de phase — calculée PAR LE CODE."""

    phase_probable: str | None = None
    incertitude: str = INCERTITUDE_ELEVEE
    scores_par_phase: dict[str, float] = Field(default_factory=dict)
    nb_familles_evaluees: int = 0
    regle_appliquee: str = ""
    statut_regle: str = STATUT_REGLE
    confiance: str = CONFIANCE_FAIBLE


class ResultatPLC(BaseModel):
    """Objet de sortie complet de l'agent PLC."""

    produit: FicheProduit
    marche: ParametresMarche
    horodatage_utc: str = ""
    sources_utilisees: list[SourceUtilisee] = Field(default_factory=list)
    alertes_coherence: list[AlerteCoherence] = Field(default_factory=list)
    declenchement: Declenchement = Field(default_factory=Declenchement)
    dossier_plc: DossierPLC | None = None
    signaux: list[OrientationSignal] = Field(default_factory=list)
    classification: Classification | None = None
    recommandations_phase: list[Recommandation] = Field(default_factory=list)
    conditions_reexamen: list[str] = Field(default_factory=list)
    faits_cles: list[FaitCle] = Field(default_factory=list)
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

    recommandations: EntreeRecommandations | None = None
    concurrence: EntreeConcurrence | None = None
    insights: EntreeInsights | None = None
    produit: FicheProduit | None = None
    marche: ParametresMarche | None = None
    limites_amont: list[str] = Field(default_factory=list)


class ErreurCoherenceProduit(Exception):
    """Produits différents entre deux fichiers d'entrée — mélange d'études interdit."""
