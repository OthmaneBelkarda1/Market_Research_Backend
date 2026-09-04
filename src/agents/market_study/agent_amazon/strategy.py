"""Où chercher, et quoi chercher : marketplace, plan de recherches, URLs.

Trois responsabilités, toutes en amont de la collecte :

1. `controler_fiche_produit` → `list[AlerteQualiteInput]` (informatif, ne bloque
   jamais le traitement) ;
2. `resoudre_marketplace` → `Marketplace | None`, la fonctionnalité régionale du
   module : une région d'étude devient le site Amazon **de son pays**, ou rien
   du tout si ce pays n'en a pas ;
3. `generer_plan_recherches` → `list[RecherchePlanifiee]`, chaque recherche
   portant déjà l'URL Amazon filtrée que l'actor crawlera.

Le modèle PROPOSE, le code DISPOSE. La marketplace sort d'une table
déterministe et exhaustive ; le LLM n'est consulté que pour traduire un lieu en
texte libre en code pays, et n'a aucune voix au chapitre sur le site retenu. De
même, chaque recherche proposée est contrôlée mécaniquement (tri dans la
nomenclature, bornes de prix cohérentes, note dans l'échelle, mots-clés non
réduits à la référence commerciale) avant d'être retenue.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote_plus, urlencode

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate

from config import (
    invoquer_avec_reprises,
    ANTHROPIC_API_KEY,
    CLES_TRI_AMAZON,
    MARKETPLACE_PAR_PAYS,
    MARKETPLACES,
    MARKETPLACES_SANS_DECIMALES,
    MAX_TOKENS_LLM,
    MODELE_CLAUDE,
    MOTIF_PAYS_SANS_MARKETPLACE,
    NB_RECHERCHES,
    NB_RECHERCHES_REPLI,
    PARAM_FACETTE,
    PARAM_RECHERCHE,
    PARAM_TRI,
    PREFIXE_FACETTE_PRIX,
    PREFIXE_WWW,
    TEMPERATURE_LLM,
    TRI_PERTINENCE,
    TRIS,
    obtenir_logger,
)
from schemas import (
    AlerteQualiteInput,
    FicheProduit,
    Marketplace,
    ParametresMarche,
    PlanRecherches,
    RapportQualiteInput,
    RechercheProposee,
    RecherchePlanifiee,
    RegionResolue,
)

_LOG = obtenir_logger(__name__)

_MOTIF_ESPACES = re.compile(r"\s+")
_MOTIF_CODE_ISO = re.compile(r"^[A-Za-z]{2}$")

NOTE_MIN_AMAZON: float = 1.0
NOTE_MAX_AMAZON: float = 5.0
"""Bornes de l'échelle de notation d'Amazon, utilisées pour valider `note_min`."""

UNITES_MINEURES_PAR_UNITE: int = 100
"""Facteur de conversion vers l'unité mineure d'une devise à décimales."""


# --------------------------------------------------------------------------- #
# Modèle
# --------------------------------------------------------------------------- #


def _modele() -> ChatAnthropic:
    """Instancie le modèle Claude utilisé par les chaînes de ce module.

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


def _sans_accents(texte: str) -> str:
    """Ramène un texte à une forme comparable, sans accents ni casse.

    Args:
        texte: Texte à normaliser.

    Returns:
        Le texte en minuscules, diacritiques retirés.
    """
    decompose = unicodedata.normalize("NFKD", texte.casefold())
    return "".join(c for c in decompose if not unicodedata.combining(c))


def _nettoyer_espaces(texte: str) -> str:
    """Réduit les espaces multiples d'un texte.

    Args:
        texte: Texte brut.

    Returns:
        Le texte aux espaces normalisés, sans espaces de bord.
    """
    return _MOTIF_ESPACES.sub(" ", texte).strip()


# --------------------------------------------------------------------------- #
# 1. Contrôle qualité de la fiche produit
# --------------------------------------------------------------------------- #

_PROMPT_QUALITE = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Tu es analyste qualité de catalogue e-commerce. Tu examines une "
            "fiche produit et tu SIGNALES les anomalies SANS JAMAIS les corriger "
            "ni réécrire la fiche.\n\n"
            "Types d'anomalies à détecter :\n"
            "- « contradiction » : le titre et la description se contredisent "
            "sur une caractéristique technique.\n"
            "- « langue_inattendue » : la description n'est pas rédigée dans la "
            "langue du marché ciblé.\n"
            "- « description_insuffisante » : la description ne permet pas "
            "d'identifier la catégorie d'usage du produit, donc pas de formuler "
            "une recherche Amazon utile.\n"
            "- « autre » : toute autre incohérence factuelle notable.\n\n"
            "Règles :\n"
            "- N'invente aucune anomalie. Si la fiche est cohérente, renvoie une "
            "liste vide.\n"
            "- Une anomalie par entrée, avec un détail factuel citant les "
            "éléments en cause.\n"
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


def controler_fiche_produit(
    produit: FicheProduit, marche: ParametresMarche
) -> list[AlerteQualiteInput]:
    """Contrôle la cohérence de la fiche produit.

    Les alertes sont informatives : elles ne bloquent jamais le traitement.

    Args:
        produit: Fiche produit à contrôler.
        marche: Région d'étude, utilisée pour vérifier la langue de la
            description.

    Returns:
        Les anomalies détectées, liste vide si la fiche est cohérente ou si le
        contrôle lui-même a échoué.
    """
    chaine = _PROMPT_QUALITE | _modele().with_structured_output(RapportQualiteInput)
    entree = {
        "nom": produit.nom,
        "description": produit.description,
        "categorie": produit.categorie,
        "geo": marche.geo,
        "langue": marche.langue,
    }
    try:
        rapport: RapportQualiteInput = chaine.invoke(entree)
    except Exception as exception:  # noqa: BLE001 — le contrôle qualité ne bloque pas
        _LOG.warning("Contrôle qualité indisponible : %s", exception)
        return []

    _LOG.info("Contrôle qualité : %s alerte(s).", len(rapport.alertes))
    return rapport.alertes


# --------------------------------------------------------------------------- #
# 2. Résolution de la région en marketplace
# --------------------------------------------------------------------------- #

_PROMPT_REGION = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Tu identifies le PAYS auquel se rattache un lieu.\n\n"
            "On te donne un lieu en texte libre — pays, ville ou région, dans "
            "n'importe quelle langue ou orthographe (« Maroc », « Casablanca », "
            "« Bavière », « UAE », « الجزائر »). Tu renvoies son code ISO 3166-1 "
            "alpha-2 en majuscules.\n\n"
            "Pour une ville, le pays qui la contient. Pour une région large, le "
            "pays auquel elle est le plus identifiée. Tu ne choisis AUCUN site "
            "marchand et tu ne proposes AUCUN pays de substitution : si le lieu "
            "est ambigu ou introuvable, renvoie une chaîne vide.",
        ),
        ("human", "Lieu : {lieu}"),
    ]
)


def normaliser_domaine(texte: str | None) -> str:
    """Ramène ce qui a été saisi à un hôte de marketplace nu.

    Accepte « https://www.amazon.fr/ » comme « amazon.fr ».

    Args:
        texte: Domaine ou URL saisis, éventuellement nuls.

    Returns:
        L'hôte de marketplace, ou une chaîne vide si l'entrée est vide.
    """
    domaine = (texte or "").strip().casefold()
    for prefixe in ("https://", "http://", PREFIXE_WWW):
        domaine = domaine.removeprefix(prefixe)
    return domaine.split("/", 1)[0]


def _marketplace_du_pays(code_pays: str, saisi: str) -> tuple[Marketplace | None, str]:
    """Cherche le site Amazon du pays, et refuse tout repli s'il n'y en a pas.

    Args:
        code_pays: Code ISO-2 du pays étudié.
        saisi: Région telle qu'elle a été saisie, reprise dans l'explication.

    Returns:
        Un couple `(marketplace, explication)`. La marketplace est `None` quand
        le pays n'a pas de site Amazon propre — l'agent est alors inapplicable.
    """
    # « MA » saisi et résolu en MA n'a pas à s'afficher deux fois ; « Casablanca »
    # résolu en MA, si.
    origine = f"« {saisi} »" if saisi.strip().upper() == code_pays else f"« {saisi} » → {code_pays}"

    domaine = MARKETPLACE_PAR_PAYS.get(code_pays)
    if not domaine:
        motif = f"{origine} : aucun site Amazon dans ce pays. {MOTIF_PAYS_SANS_MARKETPLACE}"
        _LOG.error("Agent inapplicable — %s", motif)
        return None, motif

    explication = f"{origine} : collecte sur {domaine}, le site Amazon du pays."
    return (
        Marketplace(domaine=domaine, code_pays=code_pays, explication=explication),
        explication,
    )


def _resoudre_par_llm(lieu: str) -> RegionResolue | None:
    """Demande au modèle à quel pays se rattache un lieu en texte libre.

    Args:
        lieu: Région d'étude en texte libre.

    Returns:
        Le pays identifié, ou `None` si l'appel a échoué.
    """
    chaine = _PROMPT_REGION | _modele().with_structured_output(RegionResolue)
    # Une région non résolue arrête la collecte avant qu'elle commence.
    return invoquer_avec_reprises(
        lambda: chaine.invoke({"lieu": lieu}), f"Résolution de la région « {lieu} »"
    )


def resoudre_marketplace(
    geo: str, domaine_force: str | None = None
) -> tuple[Marketplace | None, str]:
    """Détermine quel site Amazon interroger pour une région d'étude.

    **Règle centrale : un pays sans site Amazon propre rend l'agent
    inapplicable.** Aucun repli sur « la marketplace la plus proche » n'est fait
    — interroger `amazon.fr` pour le Maroc livrerait le marché français sous
    l'étiquette du marché marocain. La fonction renvoie alors `None`, et
    l'exécution s'arrête avant la moindre dépense.

    Ordre de décision :

    1. `domaine_force`, lorsqu'un opérateur a explicitement tranché ;
    2. la table `MARKETPLACE_PAR_PAYS`, si `geo` est un code ISO-2 ;
    3. le modèle pour toute autre saisie (ville, nom de pays, autre alphabet),
       qui identifie UNIQUEMENT le pays — la table décide ensuite.

    Ce choix ne porte que sur le site interrogé. Aucune adresse de livraison
    n'accompagne la collecte : voir `config.MOTIF_ABSENCE_LIVRAISON`.

    Args:
        geo: Code ISO-2 ou lieu en texte libre.
        domaine_force: Marketplace imposée, qui court-circuite la résolution.

    Returns:
        Un couple `(marketplace, explication)`. La marketplace est `None` quand
        l'agent ne s'applique pas ; l'explication est toujours renseignée.
    """
    saisi = (geo or "").strip()

    if domaine_force:
        domaine = normaliser_domaine(domaine_force)
        if domaine not in MARKETPLACES:
            _LOG.warning(
                "Marketplace « %s » hors nomenclature : elle est utilisée telle "
                "quelle, aucune vérification n'est possible.",
                domaine,
            )
        # La région d'étude reste celle qui a été demandée : imposer une
        # marketplace ne la déplace pas. Le code du pays hôte de la marketplace
        # ne sert de repli que lorsque la région n'est pas un code ISO-2.
        code = (
            saisi.upper()
            if _MOTIF_CODE_ISO.match(saisi)
            else next(
                (pays for pays, hote in MARKETPLACE_PAR_PAYS.items() if hote == domaine),
                "",
            )
        )
        explication = (
            f"Marketplace imposée par l'appelant : {domaine}. Le contrôle de "
            "couverture du pays est court-circuité — c'est une décision "
            "d'opérateur, pas une déduction du module."
        )
        _LOG.warning("Marketplace imposée : %s (région saisie : « %s »).", domaine, saisi)
        return (
            Marketplace(domaine=domaine, code_pays=code, explication=explication),
            explication,
        )

    if not saisi:
        motif = "Région d'étude non renseignée : aucune marketplace ne peut être choisie."
        _LOG.error(motif)
        return None, motif

    if "amazon." in saisi.casefold():
        return resoudre_marketplace(saisi, domaine_force=saisi)

    if _MOTIF_CODE_ISO.match(saisi):
        return _marketplace_du_pays(saisi.upper(), saisi)

    resolue = _resoudre_par_llm(saisi)
    code = (resolue.code_pays or "").strip().upper() if resolue else ""
    if not _MOTIF_CODE_ISO.match(code):
        motif = (
            f"Région « {saisi} » non résolue en un pays : impossible de vérifier "
            "qu'Amazon y exploite un site. Aucune marketplace n'est interrogée "
            "par défaut."
        )
        _LOG.error(motif)
        return None, motif

    return _marketplace_du_pays(code, saisi)


# --------------------------------------------------------------------------- #
# 3. Construction des URLs de recherche Amazon
# --------------------------------------------------------------------------- #


def _facteur_unites_mineures(domaine: str) -> int:
    """Donne le facteur de conversion vers l'unité mineure d'une marketplace.

    Args:
        domaine: Hôte de la marketplace.

    Returns:
        1 sur les devises sans sous-unité, 100 partout ailleurs.
    """
    return 1 if domaine in MARKETPLACES_SANS_DECIMALES else UNITES_MINEURES_PAR_UNITE


def _facette_prix(
    prix_min: float | None, prix_max: float | None, domaine: str
) -> str | None:
    """Construit la facette de prix native d'Amazon.

    Args:
        prix_min: Prix plancher, ou `None`.
        prix_max: Prix plafond, ou `None`.
        domaine: Marketplace visée, qui détermine l'unité mineure.

    Returns:
        La valeur du paramètre `rh`, ou `None` si aucune borne n'est posée.
    """
    if prix_min is None and prix_max is None:
        return None
    facteur = _facteur_unites_mineures(domaine)
    borne_basse = int(prix_min * facteur) if prix_min is not None else ""
    borne_haute = int(prix_max * facteur) if prix_max is not None else ""
    return f"{PREFIXE_FACETTE_PRIX}:{borne_basse}-{borne_haute}"


def construire_url(
    mots_cles: str,
    tri: str,
    prix_min: float | None,
    prix_max: float | None,
    domaine: str,
    *,
    avec_filtres: bool = True,
) -> str:
    """Construit l'URL de recherche Amazon que l'actor crawlera.

    Args:
        mots_cles: Mots-clés de la recherche.
        tri: Tri du module, converti en paramètre `s=` d'Amazon.
        prix_min: Prix plancher, ou `None`.
        prix_max: Prix plafond, ou `None`.
        domaine: Marketplace visée.
        avec_filtres: Faux pour retirer la facette de prix, lors d'une relance
            après une recherche restée vide.

    Returns:
        L'URL absolue de la page de résultats.
    """
    parametres: dict[str, str] = {PARAM_RECHERCHE: mots_cles}

    cle_tri = CLES_TRI_AMAZON.get(tri)
    if cle_tri:
        parametres[PARAM_TRI] = cle_tri

    facette = _facette_prix(prix_min, prix_max, domaine) if avec_filtres else None
    if facette:
        parametres[PARAM_FACETTE] = facette

    return f"https://www.{domaine}/s?" + urlencode(parametres, quote_via=quote_plus)


def relancer_sans_filtres(recherche: RecherchePlanifiee) -> RecherchePlanifiee:
    """Rejoue une recherche restée vide, sans la facette de prix d'Amazon.

    Une recherche vide vient le plus souvent d'une page bloquée ou d'une facette
    que la marketplace n'a pas acceptée. Retirer la facette laisse les critères
    de prix être appliqués côté Python, sur un corpus effectivement collecté.

    Args:
        recherche: Recherche à relancer, telle qu'elle a été exécutée.

    Returns:
        Une copie de la recherche dont l'URL ne porte plus de facette.
    """
    domaine = recherche.url.split("//", 1)[-1].split("/", 1)[0].removeprefix(PREFIXE_WWW)
    return recherche.model_copy(
        update={
            "url": construire_url(
                recherche.mots_cles,
                recherche.tri,
                recherche.prix_min,
                recherche.prix_max,
                domaine,
                avec_filtres=False,
            ),
            "filtres_url": False,
            "est_repli": True,
        }
    )


# --------------------------------------------------------------------------- #
# 4. Plan de recherches
# --------------------------------------------------------------------------- #

_REGLES_COMMUNES = (
    "RÈGLES DE RÉDACTION — impératives :\n"
    "1. Les mots-clés sont rédigés DANS LA LANGUE PRINCIPALE DE LA MARKETPLACE "
    "(amazon.fr → français, amazon.de → allemand, amazon.com → anglais), et non "
    "dans la langue de la fiche produit.\n"
    "2. N'utilise JAMAIS le titre commercial brut : personne ne tape une "
    "référence complète dans la barre de recherche Amazon, et une telle "
    "recherche ne remonte rien. Emploie des formulations COURTES ET "
    "CATÉGORIELLES, deux à cinq mots.\n"
    "3. RÈGLE LA PLUS IMPORTANTE — conserve l'ATTRIBUT DIFFÉRENCIANT du produit "
    "quand il en existe un. Un terme trop générique remonte du bruit. Exemple : "
    "pour des écouteurs « open ear », écrire « écouteurs open ear » et NON "
    "« écouteurs sport ».\n"
    "4. Les bornes de prix s'entendent DANS LA DEVISE DE LA MARKETPLACE "
    "(amazon.fr → euros, amazon.com → dollars, amazon.co.jp → yens). Ne "
    "convertis rien : donne des ordres de grandeur plausibles pour ce marché, "
    "ou laisse à null.\n"
    "5. Chaque filtre que tu poses RETIRE des produits du corpus. Ne pose "
    "`note_min` que si la fiche appelle explicitement une exigence de qualité, "
    "et `nb_avis_min` que pour exiger une preuve de demande (≈100 pour "
    "« best-seller », ≈500 pour « incontournable »). Sinon : null et 0.\n\n"
    "TRIS DISPONIBLES : « pertinence » (défaut), « meilleures_ventes » "
    "(popularité), « prix_croissant » (entrée de gamme), « prix_decroissant » "
    "(premium), « note » (mieux notés), « nouveautes » (sorties récentes)."
)

_PROMPT_PLAN = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Tu prépares une étude de marché sur Amazon. À partir d'une fiche "
            "produit et d'une marketplace, tu produis un plan de recherches : "
            "chaque recherche sera tapée telle quelle dans la barre de recherche "
            "d'Amazon, puis scrapée.\n\n"
            + _REGLES_COMMUNES
            + "\n\n"
            "PLAN ATTENDU : exactement {nb_recherches} recherches, chacune sous "
            "un ANGLE DIFFÉRENT — le classement d'Amazon dépend fortement de la "
            "formulation, deux recherches proches ramèneraient les mêmes fiches "
            "et coûteraient deux fois le même résultat. Fais varier le niveau de "
            "généralité (catégorie large vs attribut précis), le tri, et les "
            "bornes de prix pour couvrir l'entrée de gamme comme le haut de "
            "gamme.\n\n"
            "Chaque `justification` tient en une phrase et décrit l'angle visé.",
        ),
        (
            "human",
            "Marketplace : www.{domaine}\n"
            "Région d'étude : pays={geo}, langue du marché={langue}\n\n"
            "Titre : {nom}\n"
            "Catégorie : {categorie}\n"
            "Description : {description}",
        ),
    ]
)

_PROMPT_REPLI = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Tu complètes un plan de recherches Amazon déjà exécuté : le corpus "
            "obtenu est TROP COURT. Tu produis de nouvelles recherches pour le "
            "compléter.\n\n"
            + _REGLES_COMMUNES
            + "\n\n"
            "CONSIGNE PROPRE AU REPLI :\n"
            "- Produis exactement {nb_recherches} recherche(s).\n"
            "- ÉLARGIS : les recherches déjà exécutées sont reproduites "
            "ci-dessous et se sont révélées trop étroites. Monte d'un cran en "
            "généralité, abandonne les bornes de prix les plus serrées, et "
            "change de vocabulaire — reformuler la même intention ramènerait les "
            "mêmes fiches.\n"
            "- Conserve malgré tout l'attribut différenciant du produit : "
            "élargir ne veut pas dire changer de catégorie.",
        ),
        (
            "human",
            "Marketplace : www.{domaine}\n"
            "Région d'étude : pays={geo}, langue du marché={langue}\n\n"
            "Titre : {nom}\n"
            "Catégorie : {categorie}\n"
            "Description : {description}\n\n"
            "Recherches déjà exécutées :\n{recherches_utilisees}",
        ),
    ]
)


def _entree_commune(
    produit: FicheProduit, marche: ParametresMarche, marketplace: Marketplace
) -> dict[str, object]:
    """Assemble les variables communes aux prompts de plan et de repli.

    Args:
        produit: Fiche produit soumise.
        marche: Région d'étude.
        marketplace: Marketplace retenue.

    Returns:
        Le dictionnaire d'entrée des chaînes LCEL.
    """
    return {
        "nom": produit.nom,
        "description": produit.description,
        "categorie": produit.categorie,
        "geo": marketplace.code_pays or marche.geo,
        "langue": marche.langue,
        "domaine": marketplace.domaine,
    }


def _conformer(
    proposee: RechercheProposee,
    marketplace: Marketplace,
    nom_produit: str,
    est_repli: bool,
) -> RecherchePlanifiee | None:
    """Contrôle et corrige mécaniquement une recherche proposée par le modèle.

    Args:
        proposee: Recherche telle que le modèle l'a produite.
        marketplace: Marketplace visée, qui détermine l'URL et l'unité de prix.
        nom_produit: Titre commercial du produit, interdit comme mots-clés.
        est_repli: Vrai si la recherche appartient au cycle de repli.

    Returns:
        La recherche conforme, ou `None` si elle est inexploitable — mots-clés
        vides ou réduits à la référence commerciale.
    """
    mots_cles = _nettoyer_espaces(proposee.mots_cles)
    if not mots_cles:
        return None

    # Le titre commercial brut n'est pas une formulation de recherche : sur
    # Amazon, il ne remonte au mieux qu'une seule fiche. La règle est portée par
    # le prompt, mais un LLM n'est pas contraint. Reformuler mécaniquement est
    # impossible : la recherche est écartée.
    if nom_produit and _sans_accents(nom_produit) in _sans_accents(mots_cles):
        _LOG.warning(
            "Recherche reprenant le titre produit brut écartée : « %s ».", mots_cles
        )
        return None

    tri = proposee.tri.strip().casefold()
    if tri not in TRIS:
        _LOG.warning("Tri hors nomenclature « %s » — ramené à « %s ».", proposee.tri, TRI_PERTINENCE)
        tri = TRI_PERTINENCE

    prix_min, prix_max = proposee.prix_min, proposee.prix_max
    if prix_min is not None and prix_min <= 0:
        prix_min = None
    if prix_max is not None and prix_max <= 0:
        prix_max = None
    if prix_min is not None and prix_max is not None and prix_min > prix_max:
        _LOG.warning("Bornes de prix inversées (%s > %s) — permutées.", prix_min, prix_max)
        prix_min, prix_max = prix_max, prix_min

    note_min = proposee.note_min
    if note_min is not None and not NOTE_MIN_AMAZON <= note_min <= NOTE_MAX_AMAZON:
        _LOG.warning("Note minimale hors échelle (%s) — ignorée.", note_min)
        note_min = None

    return RecherchePlanifiee(
        mots_cles=mots_cles,
        tri=tri,
        prix_min=prix_min,
        prix_max=prix_max,
        note_min=note_min,
        nb_avis_min=max(0, proposee.nb_avis_min),
        justification=proposee.justification.strip(),
        url=construire_url(mots_cles, tri, prix_min, prix_max, marketplace.domaine),
        filtres_url=prix_min is not None or prix_max is not None,
        est_repli=est_repli,
    )


def _dedoublonner(recherches: list[RecherchePlanifiee]) -> list[RecherchePlanifiee]:
    """Écarte les recherches dont les mots-clés sont déjà présents.

    Args:
        recherches: Recherches conformes, dans l'ordre de proposition.

    Returns:
        Les recherches uniques, dans l'ordre d'origine.
    """
    vues: set[str] = set()
    uniques: list[RecherchePlanifiee] = []
    for recherche in recherches:
        cle = _sans_accents(recherche.mots_cles)
        if cle in vues:
            _LOG.info("Recherche en doublon écartée : « %s ».", recherche.mots_cles)
            continue
        vues.add(cle)
        uniques.append(recherche)
    return uniques


def _invoquer_plan(
    prompt: ChatPromptTemplate, entree: dict[str, object], contexte: str
) -> PlanRecherches | None:
    """Exécute une chaîne de génération de recherches.

    Args:
        prompt: Gabarit de prompt à utiliser.
        entree: Variables du gabarit.
        contexte: Libellé de l'appel, pour les traces.

    Returns:
        Le plan produit, ou `None` si l'appel a échoué.
    """
    chaine = prompt | _modele().with_structured_output(PlanRecherches)
    # Sans plan, il n'y a pas de collecte du tout : c'est l'appel le plus coûteux
    # à perdre de tout l'agent, et le seul essai unique qu'il avait est ce qui a
    # vidé trois collecteurs de l'étude 7a93b99d.
    return invoquer_avec_reprises(
        lambda: chaine.invoke(entree), f"Génération des recherches ({contexte})"
    )


def _tracer(recherches: list[RecherchePlanifiee], prefixe: str) -> None:
    """Journalise le plan retenu, URL comprise.

    Args:
        recherches: Recherches retenues.
        prefixe: Libellé du cycle, pour distinguer plan initial et repli.
    """
    for recherche in recherches:
        _LOG.info("  [%s/%s] %s → %s", prefixe, recherche.tri, recherche.mots_cles, recherche.url)


def generer_plan_recherches(
    produit: FicheProduit, marche: ParametresMarche, marketplace: Marketplace
) -> tuple[list[RecherchePlanifiee], bool]:
    """Construit le plan de recherches à exécuter sur la marketplace.

    Le plan vise `NB_RECHERCHES` recherches sous des angles distincts. Les
    propositions du modèle sont contrôlées et corrigées mécaniquement ; aucune
    re-sollicitation n'est faite si le compte n'y est pas.

    Args:
        produit: Fiche produit à étudier.
        marche: Région d'étude.
        marketplace: Marketplace retenue.

    Returns:
        Un couple `(plan, compte_atteint)`. Le plan est vide si la génération a
        échoué — aucune collecte n'est alors possible.
    """
    entree = _entree_commune(produit, marche, marketplace)
    entree["nb_recherches"] = NB_RECHERCHES

    plan = _invoquer_plan(_PROMPT_PLAN, entree, "plan initial")
    if plan is None:
        return [], False

    conformes = [
        recherche
        for recherche in (
            _conformer(proposee, marketplace, produit.nom, est_repli=False)
            for proposee in plan.recherches
        )
        if recherche is not None
    ]
    retenues = _dedoublonner(conformes)[:NB_RECHERCHES]

    _LOG.info(
        "Plan de recherches : %s recherche(s) retenue(s) sur %s proposée(s).",
        len(retenues),
        len(plan.recherches),
    )
    _tracer(retenues, "plan")
    return retenues, len(retenues) == NB_RECHERCHES


def generer_recherches_repli(
    produit: FicheProduit,
    marche: ParametresMarche,
    marketplace: Marketplace,
    recherches_utilisees: list[str],
) -> list[RecherchePlanifiee]:
    """Génère les recherches de repli lorsque le corpus reste trop court.

    Appelée au plus une fois par exécution : jamais en boucle.

    Args:
        produit: Fiche produit à étudier.
        marche: Région d'étude.
        marketplace: Marketplace retenue.
        recherches_utilisees: Mots-clés déjà exécutés, pour imposer un angle
            plus large.

    Returns:
        Les recherches de repli conformes, liste vide si la génération a échoué.
    """
    entree = _entree_commune(produit, marche, marketplace)
    entree["nb_recherches"] = NB_RECHERCHES_REPLI
    entree["recherches_utilisees"] = "\n".join(f"- {mots}" for mots in recherches_utilisees)

    plan = _invoquer_plan(_PROMPT_REPLI, entree, "repli")
    if plan is None:
        return []

    deja_utilisees = {_sans_accents(mots) for mots in recherches_utilisees}
    conformes = [
        recherche
        for recherche in (
            _conformer(proposee, marketplace, produit.nom, est_repli=True)
            for proposee in plan.recherches
        )
        if recherche is not None
        and _sans_accents(recherche.mots_cles) not in deja_utilisees
    ]
    retenues = _dedoublonner(conformes)[:NB_RECHERCHES_REPLI]

    _LOG.info("Repli : %s recherche(s) retenue(s).", len(retenues))
    _tracer(retenues, "repli")
    return retenues
