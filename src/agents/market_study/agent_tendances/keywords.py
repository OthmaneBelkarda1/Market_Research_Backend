"""Contrôle qualité de la fiche produit et dérivation du mot-clé pivot.

Deux chaînes LCEL distinctes, toutes deux en sortie structurée :

1. `controler_fiche_produit` → `list[AlerteQualiteInput]` (informatif, ne bloque jamais) ;
2. `deriver_mots_cles` → `JeuMotsCles` (terme pivot + repliements ordonnés).
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate

from config import (
    ANTHROPIC_API_KEY,
    MAX_TOKENS_LLM,
    MODELE_CLAUDE,
    TEMPERATURE_LLM,
    obtenir_logger,
)
from schemas import (
    AlerteQualiteInput,
    FicheProduit,
    JeuMotsCles,
    ParametresMarche,
    PropositionMotsCles,
    RapportQualiteInput,
)

_LOG = obtenir_logger(__name__)

_PROMPT_QUALITE = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Tu es analyste qualité de catalogue e-commerce. Tu examines une fiche "
            "produit et tu SIGNALES les anomalies SANS JAMAIS les corriger ni "
            "réécrire la fiche.\n\n"
            "Types d'anomalies à détecter :\n"
            "- « contradiction » : le titre et la description se contredisent sur une "
            "caractéristique technique. Exemple : un titre annonçant « Open Ear » "
            "alors que la description mentionne « In-Ear » et des embouts d'oreille.\n"
            "- « langue_inattendue » : la description n'est pas rédigée dans la langue "
            "du marché ciblé.\n"
            "- « description_insuffisante » : la description ne permet pas d'identifier "
            "la catégorie d'usage du produit.\n"
            "- « autre » : toute autre incohérence factuelle notable.\n\n"
            "Règles :\n"
            "- N'invente aucune anomalie. Si la fiche est cohérente, renvoie une liste vide.\n"
            "- Une anomalie par entrée, avec un détail factuel citant les éléments en cause.\n"
            "- Rédige les détails en français.",
        ),
        (
            "human",
            "Marché ciblé : pays={geo}, langue={langue}\n\n"
            "Titre : {nom}\n"
            "Catégorie : {categorie}\n"
            "Description : {description}",
        ),
    ]
)

_PROMPT_MOTS_CLES = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Tu es spécialiste de la recherche de mots-clés pour Google Trends. À "
            "partir d'une fiche produit, tu produis le terme de recherche qui sera "
            "réellement interrogé.\n\n"
            "RÈGLES IMPÉRATIVES :\n"
            "1. Le titre produit brut est inexploitable tel quel. Le terme pivot doit "
            "être COURT (1 à 4 mots), GÉNÉRIQUE et CATÉGORIEL, tel qu'un consommateur "
            "le taperait réellement dans un moteur de recherche.\n"
            "2. RÈGLE LA PLUS IMPORTANTE — le terme pivot doit IMPÉRATIVEMENT "
            "conserver l'attribut différenciant du produit lorsqu'il en existe un. Un "
            "terme trop générique désigne une catégorie mature dont la courbe est "
            "plate : son pouvoir discriminant est nul. Exemple : pour des écouteurs "
            "« open ear », retenir « écouteurs open ear » et NON « écouteurs sport ».\n"
            "3. Renseigne `attribut_differenciant` avec l'attribut identifié, ou null "
            "s'il n'en existe aucun.\n"
            "4. Le terme doit être rédigé dans la langue du marché (code {langue}), "
            "accents inclus et correctement orthographiés.\n"
            "5. Le terme pivot ne doit JAMAIS être une marque, un modèle ni une "
            "référence produit.\n"
            "6. `termes_replis` contient 2 à 3 candidats ordonnés du PLUS SPÉCIFIQUE au "
            "PLUS GÉNÉRIQUE, distincts du terme pivot. Ils ne serviront qu'en cas "
            "d'absence de données sur le terme pivot.\n"
            "7. `justification` explique en une à deux phrases, en français, le choix "
            "du terme pivot et de l'attribut retenu.\n"
            "8. En cas de contradiction interne dans la fiche, retiens l'attribut porté "
            "par le titre commercial et signale-le dans la justification.",
        ),
        (
            "human",
            "Marché ciblé : pays={geo}, langue={langue}\n\n"
            "Titre : {nom}\n"
            "Catégorie : {categorie}\n"
            "Description : {description}",
        ),
    ]
)


def _modele() -> ChatAnthropic:
    """Instancie le modèle Claude utilisé par les deux chaînes.

    Returns:
        Le client `ChatAnthropic` configuré.

    Raises:
        RuntimeError: Si `ANTHROPIC_API_KEY` est absente de l'environnement.
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY absente de l'environnement.")
    return ChatAnthropic(
        model=MODELE_CLAUDE,
        temperature=TEMPERATURE_LLM,
        max_tokens=MAX_TOKENS_LLM,
        api_key=ANTHROPIC_API_KEY,
    )


def controler_fiche_produit(
    produit: FicheProduit, marche: ParametresMarche
) -> list[AlerteQualiteInput]:
    """Contrôle la cohérence de la fiche produit.

    Les alertes sont informatives : elles ne bloquent jamais le traitement.

    Args:
        produit: Fiche produit à contrôler.
        marche: Marché ciblé, utilisé pour vérifier la langue de la description.

    Returns:
        Les anomalies détectées, liste vide si la fiche est cohérente ou si le
        contrôle a échoué.
    """
    chaine = _PROMPT_QUALITE | _modele().with_structured_output(RapportQualiteInput)
    try:
        rapport: RapportQualiteInput = chaine.invoke(
            {
                "nom": produit.nom,
                "description": produit.description,
                "categorie": produit.categorie,
                "geo": marche.geo,
                "langue": marche.langue,
            }
        )
    except Exception as exception:  # noqa: BLE001 — le contrôle qualité ne bloque pas
        _LOG.warning("Contrôle qualité indisponible : %s", exception)
        return []

    _LOG.info("Contrôle qualité : %s alerte(s)", len(rapport.alertes))
    return rapport.alertes


def deriver_mots_cles(produit: FicheProduit, marche: ParametresMarche) -> JeuMotsCles:
    """Dérive le terme pivot et les termes de repli à partir de la fiche produit.

    Args:
        produit: Fiche produit source.
        marche: Marché ciblé, dont la langue de rédaction du terme.

    Returns:
        Le jeu de mots-clés initial, `niveau_repli` à 0.

    Raises:
        RuntimeError: Si la dérivation échoue — sans terme pivot, aucune
            collecte n'est possible.
    """
    chaine = _PROMPT_MOTS_CLES | _modele().with_structured_output(PropositionMotsCles)
    try:
        proposition: PropositionMotsCles = chaine.invoke(
            {
                "nom": produit.nom,
                "description": produit.description,
                "categorie": produit.categorie,
                "geo": marche.geo,
                "langue": marche.langue,
            }
        )
    except Exception as exception:  # noqa: BLE001 — converti en erreur explicite
        raise RuntimeError(f"Dérivation du mot-clé impossible : {exception}") from exception

    terme_pivot = proposition.terme_pivot.strip()
    if not terme_pivot:
        raise RuntimeError("Dérivation du mot-clé impossible : terme pivot vide.")

    replis = [
        repli.strip()
        for repli in proposition.termes_replis
        if repli.strip() and repli.strip().casefold() != terme_pivot.casefold()
    ]

    _LOG.info(
        "Terme pivot retenu : « %s » (attribut différenciant : %s) — replis : %s",
        terme_pivot,
        proposition.attribut_differenciant,
        replis,
    )
    return JeuMotsCles(
        terme_pivot=terme_pivot,
        attribut_differenciant=proposition.attribut_differenciant,
        termes_replis=replis,
        langue=proposition.langue or marche.langue,
        justification=proposition.justification,
        niveau_repli=0,
        fallback_applique=False,
    )
