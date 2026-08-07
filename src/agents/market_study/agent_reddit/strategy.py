"""Contrôle qualité de la fiche produit et dérivation de la stratégie de recherche.

Deux chaînes LCEL distinctes, toutes deux en sortie structurée :

1. `controler_fiche_produit` → `list[AlerteQualiteInput]` (informatif, ne bloque
   jamais le traitement) ;
2. `deriver_strategie` → `StrategieRecherche` (requêtes consommateur et
   subreddits cibles).
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate

from config import (
    ANTHROPIC_API_KEY,
    MAX_TOKENS_LLM,
    MODELE_CLAUDE,
    NB_MAX_SUBREDDITS_CIBLES,
    NB_SUBREDDITS_REGIONAUX,
    TEMPERATURE_LLM,
    obtenir_logger,
)
from schemas import (
    AlerteQualiteInput,
    FicheProduit,
    ParametresMarche,
    RapportQualiteInput,
    StrategieRecherche,
    SubredditRegional,
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
            "Marché ciblé : pays={geo}, langue={langue}\n\n"
            "Titre : {nom}\n"
            "Catégorie : {categorie}\n"
            "Description : {description}",
        ),
    ]
)

_PROMPT_STRATEGIE = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Tu prépares la collecte de discussions consommateurs sur Reddit pour "
            "une étude de marché. À partir d'une fiche produit, tu produis les "
            "requêtes de recherche et les subreddits à interroger.\n\n"
            "RÈGLES IMPÉRATIVES SUR LES REQUÊTES :\n"
            "1. Les requêtes sont des FORMULATIONS DE CONSOMMATEUR, telles qu'on "
            "les tape dans la recherche Reddit : un problème vécu, une demande "
            "d'avis, une comparaison. Exemples de bonne facture : « bone "
            "conduction headphones worth it », « écouteurs open ear avis ».\n"
            "2. N'utilise JAMAIS le titre produit brut : personne ne tape une "
            "référence commerciale complète dans Reddit.\n"
            "3. RÈGLE LA PLUS IMPORTANTE — conserve l'ATTRIBUT DIFFÉRENCIANT du "
            "produit dans les requêtes lorsqu'il en existe un. Un terme trop "
            "générique n'a aucun pouvoir discriminant et remonte du bruit. "
            "Exemple : pour des écouteurs « open ear », écrire « écouteurs open "
            "ear » et NON « écouteurs sport ».\n"
            "4. Produis 3 à 4 `requetes_marche` DANS LA LANGUE DU MARCHÉ (code "
            "{langue}), accents inclus et correctement orthographiés.\n"
            "5. Produis 2 à 3 `requetes_globales` EN ANGLAIS. Reddit étant "
            "majoritairement anglophone, ce corpus global est un complément "
            "assumé du corpus régional.\n"
            "5 bis. Les `requetes_globales` doivent être DISTINCTES des "
            "`requetes_marche`, y compris lorsque la langue du marché EST "
            "l'anglais. Dans ce cas, ne répète JAMAIS les mêmes formulations : "
            "aborde des ANGLES COMPLÉMENTAIRES — par exemple les requêtes marché "
            "sur l'intention d'achat et la comparaison, les requêtes globales sur "
            "le problème vécu, l'efficacité réelle ou le retour d'expérience à "
            "long terme. Deux requêtes identiques coûtent deux fois le même "
            "résultat.\n"
            "6. Si le nom du produit contient une MARQUE ÉTABLIE, tu peux ajouter "
            "UNE SEULE requête portant sur cette marque — utile pour récupérer "
            "des avis existants. Signale-le alors dans `justification`. Aucune "
            "autre requête ne doit contenir de marque ni de référence produit.\n\n"
            "RÈGLES IMPÉRATIVES SUR LES SUBREDDITS :\n"
            f"7. `subreddits_regionaux` est OBLIGATOIRE et doit contenir "
            f"EXACTEMENT {NB_SUBREDDITS_REGIONAUX} subreddit(s). Ne la laisse "
            "JAMAIS vide, quel que soit le produit. C'est le seul point d'ancrage "
            "géographique du corpus : Reddit n'offre aucun filtre de pays.\n"
            "8. Ce subreddit régional est la communauté GÉNÉRALISTE LA PLUS ACTIVE "
            "du pays {geo} — pas un subreddit lié au produit. Choisis-le sur le "
            "seul critère du volume de discussion. Exemples : FR → « r/france » ; "
            "US → « r/AskAnAmerican » ; GB → « r/unitedkingdom » ; DE → « r/de » ; "
            "CA → « r/canada » ; MA → « r/Morocco ». Si le pays n'a pas de "
            "communauté généraliste notable, retiens la plus grande communauté "
            "rattachée à ce pays que tu connaisses — mais ne renvoie pas de liste "
            "vide.\n"
            "9. `subreddits_thematiques` : subreddits liés à la catégorie du "
            "produit, indépendamment du pays. C'est là que se trouvera l'essentiel "
            "du signal produit.\n"
            "9 bis. RÈGLE DÉTERMINANTE POUR LE RENDEMENT — ces subreddits doivent "
            "être les plus SPÉCIFIQUES possible : vise la communauté dédiée au "
            "PROBLÈME, à la PATHOLOGIE, à la PRATIQUE ou à l'OBJET précis que le "
            "produit adresse. Une communauté généraliste noie le sujet dans du "
            "hors-sujet et gaspille la collecte : mesuré sur une genouillère "
            "orthopédique, cibler « r/Health » et « r/fitness » a produit 53 % de "
            "bruit (guides de squats, tennis et longévité, mobilité des poignets), "
            "là où « r/KneeInjuries », « r/Osteoarthritis » ou « r/ACL » "
            "auraient donné du signal dense.\n"
            "9 ter. Sont donc INTERDITS comme subreddits thématiques, sauf s'il "
            "n'existe réellement aucune communauté plus précise : r/Health, "
            "r/fitness, r/Fitness, r/AskReddit, r/LifeProTips, r/BuyItForLife, "
            "r/technology, r/gadgets et tout autre agrégateur généraliste. "
            "Descends d'un cran : à la place de « r/fitness » pour un accessoire "
            "de course, retiens « r/running » ou « r/trailrunning » ; à la place "
            "de « r/Health » pour un dispositif médical, retiens la communauté de "
            "la pathologie visée.\n"
            f"10. Le total des deux listes ne doit pas dépasser "
            f"{NB_MAX_SUBREDDITS_CIBLES} entrées : le subreddit régional d'abord, "
            "puis les thématiques du plus actif au moins actif.\n"
            "11. Note les subreddits sous la forme « r/nom ».\n"
            "12. AVERTISSEMENT : tu peux proposer des subreddits inexistants ou "
            "inactifs, et tu n'as AUCUN moyen de le vérifier. Ne prétends jamais "
            "avoir vérifié leur existence : elle ne sera constatée qu'à "
            "l'exécution. Il est ATTENDU que le subreddit régional remonte peu ou "
            "pas de discussions sur le produit — c'est une information en soi, pas "
            "un échec.\n\n"
            "13. `justification` explique en deux à trois phrases, en français, le "
            "choix des requêtes, l'attribut différenciant retenu et le choix des "
            "subreddits.",
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


_PROMPT_SUBREDDIT_REGIONAL = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Tu connais la cartographie des communautés Reddit par pays. On te "
            "donne un code pays ISO-2 et tu renvoies UN seul subreddit : la "
            "communauté GÉNÉRALISTE LA PLUS ACTIVE de ce pays.\n\n"
            "RÈGLES :\n"
            "1. Critère unique : le volume de discussion. Ignore tout thème "
            "produit.\n"
            "2. Renvoie la forme « r/nom », exactement telle qu'elle s'écrit sur "
            "Reddit, casse comprise.\n"
            "3. Exemples : FR → « r/france » ; US → « r/AskAnAmerican » ; "
            "GB → « r/unitedkingdom » ; DE → « r/de » ; BR → « r/brasil » ; "
            "IT → « r/italy » ; ES → « r/es » ; MA → « r/Morocco » ; "
            "JP → « r/japan » ; IN → « r/india ».\n"
            "4. Tu DOIS répondre. Si le pays n'a pas de communauté généraliste "
            "notable, renvoie la plus grande communauté rattachée à ce pays que "
            "tu connaisses. Ne renvoie jamais de chaîne vide.",
        ),
        ("human", "Code pays : {geo}"),
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
        marche: Marché ciblé.

    Returns:
        Le dictionnaire d'entrée des chaînes LCEL.
    """
    return {
        "nom": produit.nom,
        "description": produit.description,
        "categorie": produit.categorie,
        "geo": marche.geo,
        "langue": marche.langue,
    }


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


def deriver_strategie(
    produit: FicheProduit, marche: ParametresMarche
) -> StrategieRecherche:
    """Dérive les requêtes de recherche et les subreddits cibles.

    La chaîne propose ; l'agent tronque ensuite selon les plafonds de coût. Les
    subreddits proposés ne sont pas vérifiés : leur existence n'est constatée
    qu'à l'exécution.

    Args:
        produit: Fiche produit source.
        marche: Marché ciblé, dont la langue de rédaction des requêtes.

    Returns:
        La stratégie de recherche.

    Raises:
        RuntimeError: Si la dérivation échoue ou ne produit aucune requête —
            sans requête, aucune collecte n'est possible.
    """
    chaine = _PROMPT_STRATEGIE | _modele().with_structured_output(StrategieRecherche)
    try:
        strategie: StrategieRecherche = chaine.invoke(_entree_chaine(produit, marche))
    except Exception as exception:  # noqa: BLE001 — converti en erreur explicite
        raise RuntimeError(
            f"Dérivation de la stratégie de recherche impossible : {exception}"
        ) from exception

    strategie.requetes_marche = _nettoyer(strategie.requetes_marche)
    strategie.requetes_globales = _nettoyer(strategie.requetes_globales)
    strategie.subreddits_regionaux = _nettoyer(strategie.subreddits_regionaux)
    strategie.subreddits_thematiques = _nettoyer(strategie.subreddits_thematiques)

    if not strategie.requetes_marche and not strategie.requetes_globales:
        raise RuntimeError(
            "Dérivation de la stratégie de recherche impossible : aucune requête produite."
        )

    # Le subreddit régional est imposé par le prompt, mais un LLM n'est pas
    # contraint : l'omission a été constatée en pratique. Un rattrapage ciblé,
    # à l'entrée différente, est tenté — une simple relance de la même chaîne
    # rendrait la même réponse à température nulle.
    if not strategie.subreddits_regionaux:
        _LOG.warning(
            "Aucun subreddit régional proposé pour geo=%s — rattrapage ciblé.",
            marche.geo,
        )
        rattrapage = _completer_subreddit_regional(marche.geo)
        if rattrapage:
            strategie.subreddits_regionaux = [rattrapage]
        else:
            _LOG.warning(
                "Rattrapage infructueux : le corpus sera sans ancrage géographique."
            )

    _LOG.info(
        "Stratégie : %s requête(s) marché, %s requête(s) globale(s), "
        "subreddits régionaux %s, subreddits thématiques %s.",
        len(strategie.requetes_marche),
        len(strategie.requetes_globales),
        strategie.subreddits_regionaux,
        strategie.subreddits_thematiques,
    )
    return strategie


def _completer_subreddit_regional(geo: str) -> str | None:
    """Demande le subreddit généraliste d'un pays, en rattrapage d'une omission.

    Appel court et dédié, déclenché uniquement lorsque la chaîne de stratégie a
    renvoyé une liste régionale vide. Ne bloque jamais : un échec laisse le
    corpus sans ancrage géographique, ce que l'agent consigne en limite.

    Args:
        geo: Code pays ISO-2 en majuscules.

    Returns:
        Le subreddit sous la forme « r/nom », ou `None` si le rattrapage échoue.
    """
    chaine = _PROMPT_SUBREDDIT_REGIONAL | _modele().with_structured_output(
        SubredditRegional
    )
    try:
        reponse: SubredditRegional = chaine.invoke({"geo": geo})
    except Exception as exception:  # noqa: BLE001 — le rattrapage ne bloque pas
        _LOG.warning("Rattrapage du subreddit régional indisponible : %s", exception)
        return None

    nom = reponse.nom.strip()
    if not nom:
        return None
    _LOG.info("Subreddit régional obtenu par rattrapage pour %s : %s", geo, nom)
    return nom


def _nettoyer(valeurs: list[str]) -> list[str]:
    """Élague une liste de chaînes proposée par le modèle.

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
