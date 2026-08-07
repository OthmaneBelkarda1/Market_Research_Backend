"""Contrats d'entrée et de sortie de l'agent (Pydantic v2)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from config import PROFIL_INDETERMINE

# --------------------------------------------------------------------------- #
# Entrée
# --------------------------------------------------------------------------- #


class FicheProduit(BaseModel):
    """Fiche produit brute fournie par l'appelant."""

    nom: str = Field(description="Titre commercial du produit, tel quel.")
    description: str = Field(description="Description commerciale du produit.")
    categorie: str = Field(description="Catégorie de rattachement du produit.")


class ParametresMarche(BaseModel):
    """Marché ciblé par l'analyse."""

    geo: str = Field(description="Code pays ISO-2 en majuscules, ex. « FR ».")
    langue: str = Field(description="Code langue ISO-2 en minuscules, ex. « fr ».")

    @field_validator("geo")
    @classmethod
    def _normaliser_geo(cls, valeur: str) -> str:
        return valeur.strip().upper()

    @field_validator("langue")
    @classmethod
    def _normaliser_langue(cls, valeur: str) -> str:
        return valeur.strip().lower()


# --------------------------------------------------------------------------- #
# Sortie — contrôle qualité et mots-clés
# --------------------------------------------------------------------------- #


class AlerteQualiteInput(BaseModel):
    """Anomalie détectée dans la fiche produit, signalée sans être corrigée."""

    type: str = Field(
        description=(
            "« contradiction » | « langue_inattendue » | "
            "« description_insuffisante » | « autre »"
        )
    )
    detail: str = Field(description="Description factuelle de l'anomalie constatée.")


class PropositionMotsCles(BaseModel):
    """Proposition de mots-clés produite par le LLM.

    Ne porte pas l'état du repli (`niveau_repli`, `fallback_applique`), qui
    relève de l'orchestration et non du modèle.
    """

    terme_pivot: str = Field(
        description="Terme de recherche générique, 1 à 4 mots, dans la langue du marché."
    )
    attribut_differenciant: str | None = Field(
        default=None,
        description="Attribut distinctif du produit, ex. « open ear ». None s'il n'y en a pas.",
    )
    termes_replis: list[str] = Field(
        default_factory=list,
        description="2 à 3 candidats de repli, du plus spécifique au plus générique.",
    )
    langue: str = Field(description="Code langue ISO-2 dans laquelle le terme est rédigé.")
    justification: str = Field(description="Raisonnement ayant conduit au terme pivot.")


class JeuMotsCles(BaseModel):
    """Jeu de mots-clés effectivement utilisé pour l'interrogation."""

    terme_pivot: str
    attribut_differenciant: str | None = None
    termes_replis: list[str] = Field(default_factory=list)
    langue: str
    justification: str
    niveau_repli: int = Field(
        default=0, description="0 = terme pivot initial, 1 = premier repli, etc."
    )
    fallback_applique: bool = False


# --------------------------------------------------------------------------- #
# Sortie — indicateurs
# --------------------------------------------------------------------------- #


class Saisonnalite(BaseModel):
    """Profil saisonnier moyen calculé sur l'horizon 5 ans."""

    indice_par_mois: dict[int, float] = Field(
        description="Indice moyen par mois calendaire, clés de 1 à 12."
    )
    mois_pic: int | None = None
    mois_creux: int | None = None
    amplitude: float | None = Field(
        default=None, description="(max - min) / moyenne des indices mensuels."
    )


class RequeteEmergente(BaseModel):
    """Requête associée en forte progression."""

    requete: str
    variation: str = Field(description="Valeur brute renvoyée par la source.")
    est_breakout: bool


class IndicateursTendance(BaseModel):
    """Indicateurs quantitatifs dérivés des séries brutes."""

    indice_moyen_12m: float | None = None
    profil_mensuel_12m: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Indice moyen par mois daté sur la fenêtre 12 mois, clés « AAAA-MM » "
            "triées chronologiquement. Les mois d'extrémité sont partiels."
        ),
    )
    momentum_90j: float | None = Field(
        default=None, description="Variation relative, ex. 0.23 = +23 %."
    )
    pente_annuelle_5ans: float | None = Field(
        default=None, description="Points d'indice par an."
    )
    volatilite: float | None = Field(
        default=None, description="Coefficient de variation sur la série 5 ans."
    )
    saisonnalite: Saisonnalite | None = None
    nb_breakout: int = 0
    concentration_geo: list[dict] = Field(
        default_factory=list, description="Top 5 zones : [{« zone »: str, « part »: float}]."
    )
    signal_effet_de_mode: bool = False
    profil_courbe: str = PROFIL_INDETERMINE


# --------------------------------------------------------------------------- #
# Sortie — collecte et résultat global
# --------------------------------------------------------------------------- #


class StatutCollecte(BaseModel):
    """Compte rendu d'un appel à la source de données."""

    horizon: str = Field(description="« 5y » | « 12m »")
    terme_interroge: str
    succes: bool
    message_erreur: str | None = None
    nb_points: int = 0
    nb_tentatives: int = 0


class ResultatTendances(BaseModel):
    """Objet de sortie complet de l'agent."""

    produit: FicheProduit
    marche: ParametresMarche
    alertes_qualite_input: list[AlerteQualiteInput] = Field(default_factory=list)
    mots_cles: JeuMotsCles
    indicateurs: IndicateursTendance | None = None
    requetes_emergentes: list[RequeteEmergente] = Field(default_factory=list)
    sujets_associes: list[str] = Field(default_factory=list)
    statuts_collecte: list[StatutCollecte] = Field(default_factory=list)
    donnees_disponibles: bool = False
    limites: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Enveloppes de sortie structurée pour les chaînes LCEL
# --------------------------------------------------------------------------- #


class RapportQualiteInput(BaseModel):
    """Enveloppe des alertes qualité — `with_structured_output` exige un objet."""

    alertes: list[AlerteQualiteInput] = Field(
        default_factory=list, description="Liste vide si aucune anomalie n'est détectée."
    )
