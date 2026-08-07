"""Contrats d'entrée et de sortie du module, en Pydantic v2.

Les modèles d'entrée (`FicheProduit`, `ParametresMarche`) sont identiques à ceux
de l'agent Tendances, afin qu'un orchestrateur amont puisse alimenter les deux
modules avec le même objet.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from config import (
    ORIGINE_RECHERCHE_GLOBALE,
    ORIGINE_SUBREDDIT_CIBLE,
    PHASE_COMMENTAIRES,
    PHASE_PROSPECTION_GLOBALE,
    PHASE_PROSPECTION_SUBREDDIT,
    PORTEE_GLOBALE,
    PORTEE_REGIONALE,
)

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
    """Marché sur lequel porte l'étude."""

    geo: str = Field(description="Code pays ISO-2 en majuscules, ex. « FR ».")
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
    """Enveloppe de sortie structurée de la chaîne de contrôle qualité.

    Un objet racine est nécessaire : `with_structured_output` ne sait pas
    produire directement une liste.
    """

    alertes: list[AlerteQualiteInput] = Field(
        default_factory=list, description="Anomalies détectées, vide si la fiche est saine."
    )


# --------------------------------------------------------------------------- #
# Stratégie de recherche
# --------------------------------------------------------------------------- #


class StrategieRecherche(BaseModel):
    """Plan de collecte dérivé de la fiche produit et du marché."""

    requetes_marche: list[str] = Field(
        default_factory=list, description="Requêtes consommateur dans la langue du marché."
    )
    requetes_globales: list[str] = Field(
        default_factory=list, description="Requêtes consommateur en anglais."
    )
    subreddits_regionaux: list[str] = Field(
        default_factory=list,
        description="Subreddits liés au pays ou à la langue du marché, ex. « r/france ».",
    )
    subreddits_thematiques: list[str] = Field(
        default_factory=list, description="Subreddits liés à la catégorie produit."
    )
    justification: str = Field(
        default="", description="Explication du choix des requêtes et des subreddits."
    )


# --------------------------------------------------------------------------- #
# Corpus
# --------------------------------------------------------------------------- #


class SubredditRegional(BaseModel):
    """Subreddit généraliste d'un pays, demandé en rattrapage.

    Sert uniquement lorsque la chaîne de stratégie a omis l'ancrage régional
    pourtant imposé : un appel dédié, à l'entrée différente, est le seul moyen
    d'obtenir une autre réponse à température nulle.
    """

    nom: str = Field(description="Subreddit sous la forme « r/nom ».")


class PostReddit(BaseModel):
    """Post Reddit normalisé et anonymisé."""

    id: str = Field(description="Identifiant Reddit du post, ex. « t3_1twgy7k ».")
    titre: str
    texte: str | None = Field(default=None, description="Corps du post, nul si post-lien.")
    subreddit: str = Field(description="Nom du subreddit, préfixé « r/ ».")
    url: str
    date_creation: str = Field(description="Date de création ISO 8601 UTC.")
    score: int | None = None
    nb_commentaires: int | None = None
    portee: str = Field(description=f"« {PORTEE_REGIONALE} » ou « {PORTEE_GLOBALE} ».")
    origine: str = Field(
        description=f"« {ORIGINE_RECHERCHE_GLOBALE} » ou « {ORIGINE_SUBREDDIT_CIBLE} »."
    )
    pertinence: float | None = Field(
        default=None,
        description="Score LLM de 0 à 1 ; nul si le scoring était indisponible.",
    )
    auteur_pseudonymise: str = Field(
        description="Empreinte tronquée de l'auteur — jamais le pseudonyme en clair."
    )
    requete_source: str | None = Field(
        default=None, description="Requête ayant fait remonter le post, si connue."
    )


class CommentaireReddit(BaseModel):
    """Commentaire Reddit normalisé et anonymisé."""

    id: str
    id_post: str = Field(description="Identifiant du post parent, ex. « t3_1twgy7k ».")
    texte: str
    date_creation: str = Field(description="Date de création ISO 8601 UTC.")
    score: int | None = None
    profondeur: int | None = Field(
        default=None, description="Niveau dans le fil, 0 pour une réponse directe au post."
    )
    auteur_pseudonymise: str = Field(
        description="Empreinte tronquée de l'auteur — jamais le pseudonyme en clair."
    )


class StatsCorpus(BaseModel):
    """Statistiques descriptives du corpus collecté."""

    nb_posts_collectes: int = Field(description="Posts dédoublonnés, avant filtrage.")
    nb_posts_retenus: int = Field(description="Posts après filtrage de pertinence.")
    nb_posts_approfondis: int = Field(description="Posts soumis à la collecte de commentaires.")
    nb_commentaires: int
    repartition_par_subreddit: dict[str, int] = Field(default_factory=dict)
    repartition_par_portee: dict[str, int] = Field(default_factory=dict)
    date_plus_ancienne: str | None = None
    date_plus_recente: str | None = None


class StatutCollecte(BaseModel):
    """Compte rendu d'un run Apify."""

    phase: str = Field(
        description=(
            f"« {PHASE_PROSPECTION_GLOBALE} », « {PHASE_PROSPECTION_SUBREDDIT} » "
            f"ou « {PHASE_COMMENTAIRES} »."
        )
    )
    cible: str = Field(
        description="Requêtes jointes, nom du subreddit interrogé, ou « N URLs »."
    )
    succes: bool
    message_erreur: str | None = None
    nb_items: int = 0
    nb_tentatives: int = 0


# --------------------------------------------------------------------------- #
# Scoring de pertinence
# --------------------------------------------------------------------------- #


class ScorePertinencePost(BaseModel):
    """Score attribué à un post d'un lot de scoring."""

    index: int = Field(description="Index du post dans le lot soumis.")
    score: float = Field(description="Pertinence de 0 (hors sujet) à 1 (au cœur du sujet).")


class LotScoresPertinence(BaseModel):
    """Sortie structurée d'un appel de scoring par lot."""

    scores: list[ScorePertinencePost] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Résultat
# --------------------------------------------------------------------------- #


class ResultatCollecteReddit(BaseModel):
    """Objet retourné par l'agent : corpus qualifié et son appareil critique."""

    produit: FicheProduit
    marche: ParametresMarche
    alertes_qualite_input: list[AlerteQualiteInput] = Field(default_factory=list)
    strategie: StrategieRecherche
    posts: list[PostReddit] = Field(default_factory=list)
    commentaires: list[CommentaireReddit] = Field(default_factory=list)
    stats: StatsCorpus
    statuts_collecte: list[StatutCollecte] = Field(default_factory=list)
    donnees_disponibles: bool = Field(
        description="Faux si aucun post n'a pu être collecté."
    )
    limites: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
