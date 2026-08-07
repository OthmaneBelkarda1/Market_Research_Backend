"""Contrôle qualité de la fiche produit et dérivation des requêtes marketplace.

Deux chaînes LCEL distinctes, toutes deux en sortie structurée :

1. `controler_fiche_produit` → `list[AlerteQualiteInput]` (informatif, ne bloque
   jamais le traitement) ;
2. `deriver_requetes` → `RequetesMarketplace` (2 à 4 requêtes catalogue dans la
   langue du marché).

La seconde ne bloque pas davantage : son échec fait basculer sur un repli
déterministe — le nom du produit comme requête unique — et la collecte
continue, limite consignée. Une collecte appauvrie reste exploitable ; une
collecte absente ne l'est pas.
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate

from config import (
    ANTHROPIC_API_KEY,
    MAX_TOKENS_LLM,
    MODELE_CLAUDE,
    NB_MAX_REQUETES,
    TEMPERATURE_LLM,
    obtenir_logger,
)
from schemas import (
    AlerteQualiteInput,
    FicheProduit,
    ParametresMarche,
    RapportQualiteInput,
    RequetesMarketplace,
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
            "- « contradiction » : le titre et la description se contredisent sur "
            "une caractéristique technique. Exemple : un titre annonçant « Open "
            "Ear » alors que la description mentionne « In-Ear » et des embouts "
            "d'oreille.\n"
            "- « langue_inattendue » : la description n'est pas rédigée dans la "
            "langue du marché ciblé.\n"
            "- « description_insuffisante » : la description ne permet pas "
            "d'identifier la catégorie d'usage du produit.\n"
            "- « autre » : toute autre incohérence factuelle notable.\n\n"
            "Règles :\n"
            "- N'invente aucune anomalie. Si la fiche est cohérente, renvoie une "
            "liste vide.\n"
            "- Une anomalie par entrée, avec un détail factuel citant les éléments "
            "en cause.\n"
            "- Rédige les détails en français.",
        ),
        (
            "human",
            "Marché ciblé : pays={geo}, langue={langue}, devise={devise}\n\n"
            "Titre : {nom}\n"
            "Catégorie : {categorie}\n"
            "Description : {description}",
        ),
    ]
)

_PROMPT_REQUETES = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Tu prépares l'interrogation du CATALOGUE d'une marketplace "
            "(AliExpress) pour une étude de prix. À partir d'une fiche produit, "
            "tu produis les requêtes de recherche à envoyer au moteur de "
            "recherche produits.\n\n"
            "RÈGLES IMPÉRATIVES :\n"
            "1. Ce sont des requêtes CATALOGUE, pas des questions de "
            "consommateur : nom générique du produit + attribut différenciant. "
            "Exemples de bonne facture : « ceinture lombaire double traction », "
            "« écouteurs conduction osseuse sport ». Aucune formulation "
            "conversationnelle (« est-ce que… », « avis sur… », « vaut le "
            "coup ») : ce moteur indexe des titres de fiches produit, pas des "
            "discussions.\n"
            "2. N'utilise JAMAIS le titre produit brut ni une référence "
            "commerciale complète : les titres de la marketplace sont des "
            "empilements de mots-clés, une référence exacte ne remonterait rien.\n"
            "3. RÈGLE LA PLUS IMPORTANTE — conserve l'ATTRIBUT DIFFÉRENCIANT du "
            "produit dans les requêtes lorsqu'il en existe un. Un terme trop "
            "générique remonte un catalogue entier et des prix sans rapport avec "
            "le produit étudié. Exemple : pour une ceinture à double traction, "
            "écrire « ceinture lombaire double traction » et NON « ceinture "
            "dos ».\n"
            f"4. Produis 2 à {NB_MAX_REQUETES} requêtes, TOUTES rédigées DANS LA "
            "LANGUE DU MARCHÉ (code {langue}), accents inclus et correctement "
            "orthographiés. C'est la langue dans laquelle les fiches sont "
            "traduites pour ce marché.\n"
            "5. Les requêtes doivent être COMPLÉMENTAIRES et non redondantes : "
            "fais varier le vocabulaire du besoin (usage médical, usage sportif, "
            "matériau, format) plutôt que de décliner la même formulation. Deux "
            "requêtes quasi identiques coûtent deux appels pour le même "
            "catalogue.\n"
            "6. Requêtes COURTES : 2 à 5 mots significatifs. Au-delà, le moteur "
            "de la marketplace ne trouve plus rien.\n"
            "7. Si le nom du produit contient une MARQUE ÉTABLIE, tu peux ajouter "
            "UNE SEULE requête portant sur cette marque, et le signaler dans "
            "`justification`. Aucune autre requête ne doit contenir de marque.\n"
            "8. `justification` explique en deux à trois phrases, en français, "
            "l'attribut différenciant retenu et l'angle de chaque requête.",
        ),
        (
            "human",
            "Marché ciblé : pays={geo}, langue={langue}, devise={devise}\n\n"
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


def _entree_chaine(produit: FicheProduit, marche: ParametresMarche) -> dict[str, str | None]:
    """Assemble les variables communes aux deux prompts.

    Args:
        produit: Fiche produit soumise.
        marche: Région d'étude.

    Returns:
        Le dictionnaire d'entrée des chaînes LCEL.
    """
    return {
        "nom": produit.nom,
        "description": produit.description,
        "categorie": produit.categorie,
        "geo": marche.geo,
        "langue": marche.langue,
        "devise": marche.devise,
    }


def controler_fiche_produit(
    produit: FicheProduit, marche: ParametresMarche
) -> list[AlerteQualiteInput]:
    """Contrôle la cohérence de la fiche produit.

    Les alertes sont informatives : elles ne bloquent jamais le traitement et ne
    modifient jamais la fiche.

    Args:
        produit: Fiche produit à contrôler.
        marche: Région d'étude, utilisée pour vérifier la langue de la
            description.

    Returns:
        Les anomalies détectées, liste vide si la fiche est cohérente ou si le
        contrôle lui-même a échoué.
    """
    chaine = _PROMPT_QUALITE | _modele().with_structured_output(RapportQualiteInput)
    try:
        rapport: RapportQualiteInput = chaine.invoke(_entree_chaine(produit, marche))
    except Exception as exception:  # noqa: BLE001 — le contrôle qualité ne bloque pas
        _LOG.warning("Contrôle qualité indisponible : %s", exception)
        return []

    _LOG.info("Contrôle qualité : %s alerte(s).", len(rapport.alertes))
    return rapport.alertes


def deriver_requetes(
    produit: FicheProduit, marche: ParametresMarche
) -> tuple[RequetesMarketplace, bool]:
    """Dérive les requêtes marketplace à soumettre à la recherche.

    En cas d'échec de la chaîne, un repli déterministe prend le relais : le nom
    du produit devient la requête unique. La collecte se poursuit, mais sa
    couverture est réduite — l'appelant doit consigner la limite correspondante.

    Args:
        produit: Fiche produit source.
        marche: Région d'étude, dont la langue de rédaction des requêtes.

    Returns:
        Un couple `(requetes, repli_utilise)`.
    """
    chaine = _PROMPT_REQUETES | _modele().with_structured_output(RequetesMarketplace)
    try:
        proposition: RequetesMarketplace = chaine.invoke(_entree_chaine(produit, marche))
    except Exception as exception:  # noqa: BLE001 — converti en repli déterministe
        _LOG.warning(
            "Dérivation des requêtes indisponible (%s) : repli sur le nom du produit.",
            exception,
        )
        return _repli(produit), True

    requetes = _nettoyer(proposition.requetes)[:NB_MAX_REQUETES]
    if not requetes:
        _LOG.warning("Aucune requête exploitable produite : repli sur le nom du produit.")
        return _repli(produit), True

    _LOG.info("Requêtes marketplace retenues : %s", requetes)
    return (
        RequetesMarketplace(requetes=requetes, justification=proposition.justification),
        False,
    )


def _repli(produit: FicheProduit) -> RequetesMarketplace:
    """Construit la requête de repli, sans appel LLM.

    Args:
        produit: Fiche produit source.

    Returns:
        Une stratégie à requête unique, fondée sur le nom du produit.
    """
    return RequetesMarketplace(
        requetes=[produit.nom.strip()],
        justification=(
            "Repli déterministe : la dérivation LLM des requêtes a échoué, le nom "
            "du produit est utilisé tel quel comme requête unique."
        ),
    )


def _nettoyer(valeurs: list[str]) -> list[str]:
    """Élague une liste de requêtes proposée par le modèle.

    Args:
        valeurs: Liste brute issue de la sortie structurée.

    Returns:
        Les valeurs non vides, dédoublonnées sans casse, dans l'ordre d'origine.
    """
    vues: set[str] = set()
    nettoyees: list[str] = []
    for valeur in valeurs:
        propre = valeur.strip()
        if not propre or propre.casefold() in vues:
            continue
        vues.add(propre.casefold())
        nettoyees.append(propre)
    return nettoyees
