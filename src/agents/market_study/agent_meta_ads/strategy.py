"""Où chercher, et quoi chercher : pays, plan de recherches, URLs.

Trois responsabilités, toutes en amont de la collecte :

1. `controler_fiche_produit` → `list[AlerteQualiteInput]` (informatif, ne bloque
   jamais le traitement) ;
2. `resoudre_pays` → `PaysCible | None` : une région d'étude devient le pays de
   DIFFUSION interrogé dans la bibliothèque publicitaire, ou rien du tout si
   elle n'a pas pu être résolue ;
3. `generer_plan_recherches` → `list[RecherchePlanifiee]`, chaque recherche
   portant déjà l'URL de la bibliothèque que l'actor ouvrira.

Le modèle PROPOSE, le code DISPOSE. Le LLM n'est consulté que pour traduire un
lieu en texte libre en code pays, et n'a aucune voix au chapitre sur l'URL
construite. De même, chaque recherche proposée est contrôlée mécaniquement
(mode d'appariement et statut dans la nomenclature, mots-clés non réduits à la
référence commerciale) avant d'être retenue.
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
    FILTRER_PAR_LANGUE_CONTENU,
    MAX_TOKENS_LLM,
    MODELE_CLAUDE,
    MOTIF_PAYS_NON_RESOLU,
    MOTS_MONDE,
    NB_RECHERCHES,
    NB_RECHERCHES_REPLI,
    PARAM_CIBLAGE,
    PARAM_LANGUE_CONTENU,
    PARAM_MEDIA,
    PARAM_PAYS,
    PARAM_REQUETE,
    PARAM_STATUT,
    PARAM_TYPE_ANNONCE,
    PARAM_TYPE_RECHERCHE,
    PAYS_TOUS,
    RECHERCHE_MOTS_CLES,
    STATUT_ACTIVES,
    STATUT_TOUTES,
    STATUTS,
    STATUTS_META,
    TEMPERATURE_LLM,
    TYPES_RECHERCHE,
    TYPES_RECHERCHE_META,
    URL_BIBLIOTHEQUE,
    VALEUR_CIBLAGE,
    VALEUR_MEDIA,
    VALEUR_TYPE_ANNONCE,
    obtenir_logger,
)
from schemas import (
    AlerteQualiteInput,
    FicheProduit,
    ParametresMarche,
    PaysCible,
    PlanRecherches,
    RapportQualiteInput,
    RecherchePlanifiee,
    RechercheProposee,
    RegionResolue,
)

_LOG = obtenir_logger(__name__)

_MOTIF_ESPACES = re.compile(r"\s+")
_MOTIF_CODE_ISO = re.compile(r"^[A-Za-z]{2}$")

LIBELLE_ANNONCEUR: str = "annonceur imposé"
"""Libellé de `mots_cles` pour une collecte lancée depuis une URL de Page."""

JUSTIFICATION_ANNONCEUR: str = (
    "Surveillance directe d'un annonceur désigné par l'appelant : toutes ses "
    "annonces, sans passer par le moteur de recherche de la bibliothèque."
)


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
            "une recherche publicitaire utile.\n"
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
# 2. Résolution de la région en pays de diffusion
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
            "pays auquel elle est le plus identifiée. Tu ne proposes AUCUN pays "
            "de substitution : si le lieu est ambigu ou introuvable, renvoie une "
            "chaîne vide.",
        ),
        ("human", "Lieu : {lieu}"),
    ]
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


def resoudre_pays(geo: str) -> tuple[PaysCible | None, str]:
    """Détermine le pays de diffusion à interroger pour une région d'étude.

    À la différence d'une place de marché, la bibliothèque publicitaire couvre
    tous les pays : la fonction ne refuse jamais une région parce qu'elle serait
    hors périmètre, mais **uniquement** parce qu'elle n'a pas su la résoudre.
    Aucun repli sur un pays par défaut ni sur « tous les pays » n'est fait : ce
    serait livrer un corpus qui ne décrit pas la région demandée.

    Ordre de décision :

    1. « ALL », « monde », « worldwide »… → tous les pays, explicitement ;
    2. un code ISO-2, pris tel quel ;
    3. le modèle pour toute autre saisie (ville, nom de pays, autre alphabet).

    Args:
        geo: Code ISO-2, lieu en texte libre, ou mot désignant le monde entier.

    Returns:
        Un couple `(pays, explication)`. Le pays est `None` quand la région n'a
        pas pu être résolue ; l'explication est toujours renseignée.
    """
    saisi = (geo or "").strip()

    if not saisi:
        motif = f"Région d'étude non renseignée. {MOTIF_PAYS_NON_RESOLU}"
        _LOG.error(motif)
        return None, motif

    if _sans_accents(saisi) in MOTS_MONDE:
        explication = (
            f"« {saisi} » : collecte sur tous les pays à la fois "
            f"(`country={PAYS_TOUS}`). Le corpus mêle des marchés très "
            "différents et ne décrit aucun d'eux en particulier."
        )
        _LOG.warning("Pays retenu : tous (%s).", PAYS_TOUS)
        return PaysCible(code_pays=PAYS_TOUS, explication=explication), explication

    if _MOTIF_CODE_ISO.match(saisi):
        code = saisi.upper()
        explication = (
            f"« {saisi} » : collecte des annonces diffusées en {code}, "
            "quel que soit le pays de l'annonceur."
        )
        return PaysCible(code_pays=code, explication=explication), explication

    resolue = _resoudre_par_llm(saisi)
    code = (resolue.code_pays or "").strip().upper() if resolue else ""
    if not _MOTIF_CODE_ISO.match(code):
        motif = f"Région « {saisi} » non résolue en un pays. {MOTIF_PAYS_NON_RESOLU}"
        _LOG.error(motif)
        return None, motif

    explication = (
        f"« {saisi} » → {code} : collecte des annonces diffusées en {code}, "
        "quel que soit le pays de l'annonceur."
    )
    return PaysCible(code_pays=code, explication=explication), explication


# --------------------------------------------------------------------------- #
# 3. Construction des URLs de la bibliothèque publicitaire
# --------------------------------------------------------------------------- #


def construire_url(
    mots_cles: str,
    type_recherche: str,
    statut: str,
    code_pays: str,
    langue: str | None = None,
) -> str:
    """Construit l'URL de recherche que l'actor ouvrira.

    Args:
        mots_cles: Mots-clés de la recherche.
        type_recherche: Mode d'appariement du module, converti en `search_type`.
        statut: Statut de diffusion du module, converti en `active_status`.
        code_pays: Code ISO-2 du pays de diffusion, ou « ALL ».
        langue: Langue de créatif imposée, ou `None` pour n'en imposer aucune.
            Ignorée si `FILTRER_PAR_LANGUE_CONTENU` est faux.

    Returns:
        L'URL absolue de la page de résultats.
    """
    parametres: dict[str, str] = {
        PARAM_REQUETE: mots_cles,
        PARAM_PAYS: code_pays,
        PARAM_STATUT: STATUTS_META.get(statut, STATUTS_META[STATUT_ACTIVES]),
        PARAM_TYPE_ANNONCE: VALEUR_TYPE_ANNONCE,
        PARAM_TYPE_RECHERCHE: TYPES_RECHERCHE_META.get(
            type_recherche, TYPES_RECHERCHE_META[RECHERCHE_MOTS_CLES]
        ),
        PARAM_MEDIA: VALEUR_MEDIA,
        PARAM_CIBLAGE: VALEUR_CIBLAGE,
    }
    if FILTRER_PAR_LANGUE_CONTENU and langue:
        parametres[PARAM_LANGUE_CONTENU] = langue

    return f"{URL_BIBLIOTHEQUE}?" + urlencode(parametres, quote_via=quote_plus)


def peut_etre_elargie(recherche: RecherchePlanifiee) -> bool:
    """Indique s'il reste quelque chose à relâcher sur une recherche vide.

    Une recherche déjà tous statuts confondus et sans filtre de langue ne
    gagnerait rien à être rejouée : ce serait un run facturé pour le même
    résultat.

    Args:
        recherche: Recherche restée sans annonce.

    Returns:
        Vrai s'il existe un filtre d'URL à retirer.
    """
    return not recherche.est_annonceur and recherche.filtres_url


def elargir(recherche: RecherchePlanifiee, code_pays: str) -> RecherchePlanifiee:
    """Rejoue une recherche restée vide, sans ses filtres d'URL.

    Une recherche vide vient le plus souvent d'un filtre trop serré : le statut
    « actives » exclut tout ce qui vient de s'arrêter, et le filtre de langue de
    créatif ampute les marchés multilingues. L'élargissement les retire tous les
    deux ; les mots-clés, eux, ne sont pas touchés.

    Args:
        recherche: Recherche à relancer, telle qu'elle a été exécutée.
        code_pays: Pays de diffusion interrogé.

    Returns:
        Une copie de la recherche dont l'URL ne porte plus de filtre.
    """
    return recherche.model_copy(
        update={
            "statut_diffusion": STATUT_TOUTES,
            "url": construire_url(
                recherche.mots_cles, recherche.type_recherche, STATUT_TOUTES, code_pays
            ),
            "filtres_url": False,
            "est_repli": True,
        }
    )


def recherche_annonceur(url: str) -> RecherchePlanifiee:
    """Transforme une URL de Page Facebook en recherche exécutable.

    L'URL est transmise telle quelle à l'actor, qui remonte les annonces de
    cette Page. Aucun filtre n'y est appliqué : ni statut, ni pays — c'est
    l'annonceur qui est surveillé, pas un marché.

    Args:
        url: URL de la Page ou de sa bibliothèque publicitaire.

    Returns:
        La recherche correspondante.
    """
    return RecherchePlanifiee(
        mots_cles=f"{LIBELLE_ANNONCEUR} : {url}",
        type_recherche=RECHERCHE_MOTS_CLES,
        statut_diffusion=STATUT_TOUTES,
        justification=JUSTIFICATION_ANNONCEUR,
        url=url.strip(),
        filtres_url=False,
        est_annonceur=True,
        est_repli=False,
    )


# --------------------------------------------------------------------------- #
# 4. Plan de recherches
# --------------------------------------------------------------------------- #

_REGLES_COMMUNES = (
    "CE QU'EST CETTE RECHERCHE — à ne pas confondre avec une recherche "
    "e-commerce : le moteur de la bibliothèque publicitaire de Meta apparie tes "
    "mots sur le TEXTE DES ANNONCES elles-mêmes, pas sur un catalogue produit. "
    "Tu cherches donc les formulations qu'un ANNONCEUR emploie pour vendre, et "
    "non celles qu'un acheteur taperait sur une place de marché.\n\n"
    "RÈGLES DE RÉDACTION — impératives :\n"
    "1. Les mots-clés sont rédigés DANS LA LANGUE DES ANNONCES DIFFUSÉES SUR LE "
    "MARCHÉ visé, et non dans celle de la fiche produit. Sur un marché "
    "multilingue, privilégie la langue commerciale dominante.\n"
    "2. N'utilise JAMAIS le titre commercial brut : une référence complète "
    "n'apparaît dans aucun texte d'annonce et ne remonte rien. Emploie des "
    "formulations COURTES, deux à quatre mots.\n"
    "3. RÈGLE LA PLUS IMPORTANTE — conserve l'ATTRIBUT DIFFÉRENCIANT du produit "
    "quand il en existe un. Un terme trop générique remonte du bruit. Exemple : "
    "pour des écouteurs « open ear », écrire « écouteurs open ear » et NON "
    "« écouteurs ».\n"
    "4. `type_recherche` : « expression_exacte » pour une marque, un nom de "
    "produit ou une accroche que tu veux retrouver mot pour mot ; « mots_cles » "
    "pour une formulation catégorielle — c'est le mode par défaut, et le plus "
    "productif.\n"
    "5. `statut_diffusion` : « actives » dans la quasi-totalité des cas, car "
    "c'est la pression publicitaire d'aujourd'hui qui renseigne, et parce que "
    "hors Union européenne les annonces commerciales arrêtées ne sont "
    "généralement plus consultables. « toutes » seulement pour un angle "
    "historique assumé."
)

_PROMPT_PLAN = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Tu prépares une étude de la publicité concurrente sur les "
            "plateformes de Meta (Facebook, Instagram). À partir d'une fiche "
            "produit et d'un pays, tu produis un plan de recherches : chacune "
            "sera tapée telle quelle dans la bibliothèque publicitaire de Meta, "
            "puis scrapée.\n\n"
            + _REGLES_COMMUNES
            + "\n\n"
            "PLAN ATTENDU : exactement {nb_recherches} recherches, chacune sous "
            "un ANGLE DIFFÉRENT. Fais varier le niveau de généralité (catégorie "
            "large, attribut précis, promesse marketing, problème résolu) et le "
            "mode d'appariement. Deux recherches proches ramèneraient les mêmes "
            "créatifs et coûteraient deux fois le même résultat.\n\n"
            "Chaque `justification` tient en une phrase et décrit l'angle visé.",
        ),
        (
            "human",
            "Pays de diffusion : {geo}\n"
            "Langue du marché : {langue}\n\n"
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
            "Tu complètes un plan de recherches déjà exécuté sur la bibliothèque "
            "publicitaire de Meta : le corpus obtenu est TROP COURT. Tu produis "
            "de nouvelles recherches pour le compléter.\n\n"
            + _REGLES_COMMUNES
            + "\n\n"
            "CONSIGNE PROPRE AU REPLI :\n"
            "- Produis exactement {nb_recherches} recherche(s).\n"
            "- ÉLARGIS : les recherches déjà exécutées sont reproduites "
            "ci-dessous et se sont révélées trop étroites. Monte d'un cran en "
            "généralité, préfère « mots_cles » à « expression_exacte », et change "
            "de vocabulaire — reformuler la même intention ramènerait les mêmes "
            "créatifs.\n"
            "- Pense au vocabulaire du BÉNÉFICE plutôt qu'à celui du produit : "
            "les annonces parlent souvent du problème résolu, pas de la "
            "catégorie technique.\n"
            "- Conserve malgré tout l'attribut différenciant du produit : "
            "élargir ne veut pas dire changer de catégorie.",
        ),
        (
            "human",
            "Pays de diffusion : {geo}\n"
            "Langue du marché : {langue}\n\n"
            "Titre : {nom}\n"
            "Catégorie : {categorie}\n"
            "Description : {description}\n\n"
            "Recherches déjà exécutées :\n{recherches_utilisees}",
        ),
    ]
)


def _entree_commune(
    produit: FicheProduit, marche: ParametresMarche, pays: PaysCible
) -> dict[str, object]:
    """Assemble les variables communes aux prompts de plan et de repli.

    Args:
        produit: Fiche produit soumise.
        marche: Région d'étude.
        pays: Pays de diffusion retenu.

    Returns:
        Le dictionnaire d'entrée des chaînes LCEL.
    """
    return {
        "nom": produit.nom,
        "description": produit.description,
        "categorie": produit.categorie,
        "geo": pays.code_pays,
        "langue": marche.langue,
    }


def _conformer(
    proposee: RechercheProposee,
    pays: PaysCible,
    langue: str,
    nom_produit: str,
    est_repli: bool,
) -> RecherchePlanifiee | None:
    """Contrôle et corrige mécaniquement une recherche proposée par le modèle.

    Args:
        proposee: Recherche telle que le modèle l'a produite.
        pays: Pays de diffusion, reporté dans l'URL.
        langue: Langue du marché, pour le filtre de langue de créatif.
        nom_produit: Titre commercial du produit, interdit comme mots-clés.
        est_repli: Vrai si la recherche appartient au cycle de repli.

    Returns:
        La recherche conforme, ou `None` si elle est inexploitable — mots-clés
        vides ou réduits à la référence commerciale.
    """
    mots_cles = _nettoyer_espaces(proposee.mots_cles)
    if not mots_cles:
        return None

    # Le titre commercial brut n'apparaît dans aucun texte d'annonce : une telle
    # recherche revient à payer un run pour un résultat vide. La règle est portée
    # par le prompt, mais un LLM n'est pas contraint. Reformuler mécaniquement est
    # impossible : la recherche est écartée.
    if nom_produit and _sans_accents(nom_produit) in _sans_accents(mots_cles):
        _LOG.warning(
            "Recherche reprenant le titre produit brut écartée : « %s ».", mots_cles
        )
        return None

    type_recherche = proposee.type_recherche.strip().casefold()
    if type_recherche not in TYPES_RECHERCHE:
        _LOG.warning(
            "Mode d'appariement hors nomenclature « %s » — ramené à « %s ».",
            proposee.type_recherche,
            RECHERCHE_MOTS_CLES,
        )
        type_recherche = RECHERCHE_MOTS_CLES

    statut = proposee.statut_diffusion.strip().casefold()
    if statut not in STATUTS:
        _LOG.warning(
            "Statut de diffusion hors nomenclature « %s » — ramené à « %s ».",
            proposee.statut_diffusion,
            STATUT_ACTIVES,
        )
        statut = STATUT_ACTIVES

    return RecherchePlanifiee(
        mots_cles=mots_cles,
        type_recherche=type_recherche,
        statut_diffusion=statut,
        justification=proposee.justification.strip(),
        url=construire_url(mots_cles, type_recherche, statut, pays.code_pays, langue),
        filtres_url=statut != STATUT_TOUTES or FILTRER_PAR_LANGUE_CONTENU,
        est_annonceur=False,
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
        _LOG.info(
            "  [%s/%s] %s → %s",
            prefixe,
            recherche.statut_diffusion,
            recherche.mots_cles,
            recherche.url,
        )


def generer_plan_recherches(
    produit: FicheProduit, marche: ParametresMarche, pays: PaysCible
) -> tuple[list[RecherchePlanifiee], bool]:
    """Construit le plan de recherches à exécuter sur la bibliothèque.

    Le plan vise `NB_RECHERCHES` recherches sous des angles distincts. Les
    propositions du modèle sont contrôlées et corrigées mécaniquement ; aucune
    re-sollicitation n'est faite si le compte n'y est pas.

    Args:
        produit: Fiche produit à étudier.
        marche: Région d'étude.
        pays: Pays de diffusion retenu.

    Returns:
        Un couple `(plan, compte_atteint)`. Le plan est vide si la génération a
        échoué — aucune collecte par mots-clés n'est alors possible.
    """
    entree = _entree_commune(produit, marche, pays)
    entree["nb_recherches"] = NB_RECHERCHES

    plan = _invoquer_plan(_PROMPT_PLAN, entree, "plan initial")
    if plan is None:
        return [], False

    conformes = [
        recherche
        for recherche in (
            _conformer(proposee, pays, marche.langue, produit.nom, est_repli=False)
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
    pays: PaysCible,
    recherches_utilisees: list[str],
) -> list[RecherchePlanifiee]:
    """Génère les recherches de repli lorsque le corpus reste trop court.

    Appelée au plus une fois par exécution : jamais en boucle.

    Args:
        produit: Fiche produit à étudier.
        marche: Région d'étude.
        pays: Pays de diffusion retenu.
        recherches_utilisees: Mots-clés déjà exécutés, pour imposer un angle
            plus large.

    Returns:
        Les recherches de repli conformes, liste vide si la génération a échoué.
    """
    entree = _entree_commune(produit, marche, pays)
    entree["nb_recherches"] = NB_RECHERCHES_REPLI
    entree["recherches_utilisees"] = "\n".join(f"- {mots}" for mots in recherches_utilisees)

    plan = _invoquer_plan(_PROMPT_REPLI, entree, "repli")
    if plan is None:
        return []

    deja_utilisees = {_sans_accents(mots) for mots in recherches_utilisees}
    conformes = [
        recherche
        for recherche in (
            _conformer(proposee, pays, marche.langue, produit.nom, est_repli=True)
            for proposee in plan.recherches
        )
        if recherche is not None
        and _sans_accents(recherche.mots_cles) not in deja_utilisees
    ]
    retenues = _dedoublonner(conformes)[:NB_RECHERCHES_REPLI]

    _LOG.info("Repli : %s recherche(s) retenue(s).", len(retenues))
    _tracer(retenues, "repli")
    return retenues
