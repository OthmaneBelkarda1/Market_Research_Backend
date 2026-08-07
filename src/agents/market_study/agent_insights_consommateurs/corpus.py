"""Constitution du corpus unifié : unités courtes, documents web, filtrage,
dédoublonnage, échantillonnage stratifié et statistiques descriptives.

**Aucun appel LLM dans ce module.** Toutes les fonctions sont pures et
déterministes : à corpus d'entrée identique, sortie identique.
"""

from __future__ import annotations

import re
import unicodedata

from config import (
    AXE_INSIGHTS,
    LONGUEUR_MIN_TEXTE,
    MAX_CARACTERES_DOCUMENT,
    MAX_CARACTERES_UNITE,
    MAX_DOCUMENTS_WEB,
    MAX_UNITES_CORPUS,
    MIN_UNITES_ECRITURE,
    PART_MAX_PAR_SOURCE,
    PORTEE_GLOBALE,
    PORTEE_INCONNUE,
    PORTEE_REGIONALE,
    SEUIL_PERTINENCE_AMONT,
    SOURCE_PAR_TYPE_UNITE,
    UNITE_AVIS,
    UNITE_COMMENTAIRE,
    UNITE_POST,
    logger,
)
from schemas import (
    CorpusPrepare,
    DocumentWeb,
    EntreesChargees,
    StatsCorpus,
    UniteConsommateur,
)

_ESPACES = re.compile(r"\s+")
_CHIFFRES = re.compile(r"\d+")

_MOJIBAKE = re.compile(r"ΓÇ|â€|Ã[©¨ ´¢«»]|Â[«»°]|ï»¿")
"""Signatures de texte mal décodé en amont.

Constaté sur les sorties réelles : un texte UTF-8 relu en cp1252 puis ré-encodé
produit ces séquences (« ΓÇô » pour un tiret cadratin, « Ã© » pour « é »).
Elles doivent être signalées comme un défaut de données, jamais confondues avec
une écriture étrangère : « Γ » appartient à l'alphabet grec, mais sa présence ici
ne prouve aucune unité en grec.
"""

# Plages Unicode servant à constater une écriture non latine dans le corpus.
# Heuristique volontairement grossière : elle constate une écriture, pas une langue.
_ECRITURES: tuple[tuple[str, int, int], ...] = (
    ("arabe", 0x0600, 0x06FF),
    ("cyrillique", 0x0400, 0x04FF),
    ("grec", 0x0370, 0x03FF),
    ("hebreu", 0x0590, 0x05FF),
    ("cjk", 0x4E00, 0x9FFF),
)


def _normaliser_texte(texte: str) -> str:
    """Normalise un texte pour la comparaison de doublons.

    Args:
        texte: Texte brut.

    Returns:
        Le texte en minuscules, sans accents ni ponctuation d'espacement.
    """
    sans_accent = unicodedata.normalize("NFKD", texte)
    sans_accent = "".join(c for c in sans_accent if not unicodedata.combining(c))
    return _ESPACES.sub(" ", sans_accent.lower()).strip()


def _tronquer(texte: str, limite: int) -> tuple[str, bool]:
    """Tronque un texte à une limite de caractères.

    Args:
        texte: Texte à borner.
        limite: Nombre maximal de caractères conservés.

    Returns:
        Le couple `(texte_borne, a_ete_tronque)`.
    """
    propre = texte.strip()
    if len(propre) <= limite:
        return propre, False
    return propre[:limite].rstrip(), True


def _poids_votes_utiles(valeur: str | int | None) -> int:
    """Extrait un poids social entier d'un champ de votes utiles Amazon.

    Le collecteur transmet la mention brute d'Amazon (« 2 personne(s) ont trouvé
    cet avis utile »), pas un entier : le premier nombre trouvé est retenu.

    Args:
        valeur: Champ brut.

    Returns:
        Le nombre de votes, ou 0 si aucun nombre n'est lisible.
    """
    if isinstance(valeur, int):
        return max(0, valeur)
    if isinstance(valeur, str):
        trouve = _CHIFFRES.search(valeur)
        if trouve:
            return int(trouve.group())
    return 0


def _pertinence_acceptee(pertinence: float | None) -> bool:
    """Applique le seuil de pertinence amont.

    Une pertinence absente est acceptée : l'absence de score n'est pas une preuve
    de non-pertinence.

    Args:
        pertinence: Score amont ou `None`.

    Returns:
        Vrai si l'unité est conservée.
    """
    return pertinence is None or pertinence >= SEUIL_PERTINENCE_AMONT


def _construire_unites_reddit(entrees: EntreesChargees) -> list[UniteConsommateur]:
    """Construit les unités issues de Reddit (posts puis commentaires).

    Args:
        entrees: Fichiers d'entrée chargés.

    Returns:
        Les unités Reddit, non filtrées sur la longueur ni dédoublonnées.
    """
    if entrees.reddit is None:
        return []

    unites: list[UniteConsommateur] = []
    contexte_parent: dict[str, tuple[str, float | None]] = {}

    for post in entrees.reddit.posts:
        contexte_parent[post.id] = (post.portee or PORTEE_INCONNUE, post.pertinence)
        corps = "\n\n".join(part for part in (post.titre, post.texte or "") if part).strip()
        texte, tronque = _tronquer(corps, MAX_CARACTERES_UNITE)
        unites.append(
            UniteConsommateur(
                id_unite=f"rd-p-{post.id}",
                source=UNITE_POST,
                texte=texte,
                titre=post.titre or None,
                date=post.date_creation,
                portee=post.portee or PORTEE_INCONNUE,
                poids_social=max(0, post.score or 0),
                pertinence_amont=post.pertinence,
                tronque=tronque,
            )
        )

    for commentaire in entrees.reddit.commentaires:
        portee, pertinence = contexte_parent.get(
            commentaire.id_post or "", (PORTEE_INCONNUE, None)
        )
        texte, tronque = _tronquer(commentaire.texte, MAX_CARACTERES_UNITE)
        unites.append(
            UniteConsommateur(
                id_unite=f"rd-c-{commentaire.id}",
                source=UNITE_COMMENTAIRE,
                texte=texte,
                date=commentaire.date_creation,
                portee=portee,
                poids_social=max(0, commentaire.score or 0),
                pertinence_amont=pertinence,
                tronque=tronque,
            )
        )
    return unites


def _construire_unites_amazon(entrees: EntreesChargees) -> list[UniteConsommateur]:
    """Construit les unités issues des avis Amazon.

    La portée d'un avis est régionale si le pays de la marketplace interrogée est
    celui du marché d'étude, globale sinon : un avis rédigé sur amazon.fr décrit
    le marché français, pas nécessairement celui de l'étude.

    Args:
        entrees: Fichiers d'entrée chargés.

    Returns:
        Les unités Amazon.
    """
    if entrees.amazon is None:
        return []

    pays_marketplace = (
        (entrees.amazon.marketplace.code_pays or "").strip().upper()
        if entrees.amazon.marketplace
        else ""
    )
    pays_etude = (entrees.marche.geo if entrees.marche else "").strip().upper()
    portee = (
        PORTEE_REGIONALE
        if pays_marketplace and pays_etude and pays_marketplace == pays_etude
        else PORTEE_GLOBALE
    )

    unites: list[UniteConsommateur] = []
    for produit in entrees.amazon.produits:
        for index, avis in enumerate(produit.avis):
            corps = "\n\n".join(
                part for part in (avis.titre or "", avis.texte or "") if part
            ).strip()
            texte, tronque = _tronquer(corps, MAX_CARACTERES_UNITE)
            unites.append(
                UniteConsommateur(
                    id_unite=f"amz-{produit.asin}-{index}",
                    source=UNITE_AVIS,
                    texte=texte,
                    titre=avis.titre,
                    note_sur_5=avis.note,
                    date=avis.date,
                    portee=portee,
                    poids_social=_poids_votes_utiles(avis.votes_utiles),
                    pertinence_amont=None,
                    tronque=tronque,
                )
            )
    return unites


def _construire_documents(entrees: EntreesChargees) -> list[DocumentWeb]:
    """Construit les documents web servant l'axe consommateurs.

    Args:
        entrees: Fichiers d'entrée chargés.

    Returns:
        Les documents retenus, bornés à `MAX_DOCUMENTS_WEB`.
    """
    if entrees.web is None:
        return []

    documents: list[DocumentWeb] = []
    for index, page in enumerate(entrees.web.pages):
        if AXE_INSIGHTS not in (page.axes_servis or []):
            continue
        if not _pertinence_acceptee(page.pertinence):
            continue
        contenu = page.contenu_markdown or ""
        extrait, tronque = _tronquer(contenu, MAX_CARACTERES_DOCUMENT)
        if len(extrait) < LONGUEUR_MIN_TEXTE:
            continue
        documents.append(
            DocumentWeb(
                id_unite=f"web-{index}",
                url=page.url,
                domaine=page.domaine,
                titre=page.titre,
                extrait=extrait,
                type_source=page.type_source,
                portee=(
                    PORTEE_REGIONALE
                    if page.portee_regionale is True
                    else PORTEE_GLOBALE
                    if page.portee_regionale is False
                    else PORTEE_INCONNUE
                ),
                tronque=tronque,
            )
        )
    return documents[:MAX_DOCUMENTS_WEB]


def _filtrer(unites: list[UniteConsommateur]) -> tuple[list[UniteConsommateur], dict[str, int]]:
    """Applique le seuil de pertinence, le plancher de longueur et le dédoublonnage.

    Args:
        unites: Unités brutes.

    Returns:
        Le couple `(unites_retenues, compteurs_exclusion)`.
    """
    compteurs = {"pertinence": 0, "trop_court": 0, "doublon": 0}
    vues: set[str] = set()
    retenues: list[UniteConsommateur] = []

    for unite in unites:
        if not _pertinence_acceptee(unite.pertinence_amont):
            compteurs["pertinence"] += 1
            continue
        if len(unite.texte) < LONGUEUR_MIN_TEXTE:
            compteurs["trop_court"] += 1
            continue
        empreinte = _normaliser_texte(unite.texte)
        if empreinte in vues:
            compteurs["doublon"] += 1
            continue
        vues.add(empreinte)
        retenues.append(unite)

    return retenues, compteurs


def _cle_priorite(unite: UniteConsommateur) -> tuple[float, int, str]:
    """Clé de tri décroissant : pertinence, puis poids social, puis récence.

    Args:
        unite: Unité à classer.

    Returns:
        Le tuple de tri.
    """
    return (
        unite.pertinence_amont if unite.pertinence_amont is not None else 0.0,
        unite.poids_social,
        unite.date or "",
    )


def _echantillonner(
    unites: list[UniteConsommateur],
) -> tuple[list[UniteConsommateur], bool]:
    """Échantillonne le corpus par quotas de source si le plafond est dépassé.

    Aucune source ne peut dépasser `PART_MAX_PAR_SOURCE` du corpus tant qu'une
    autre source dispose encore d'unités ; la capacité non consommée est
    redistribuée aux sources restantes.

    Args:
        unites: Unités éligibles.

    Returns:
        Le couple `(unites_retenues, echantillonnage_applique)`.
    """
    if len(unites) <= MAX_UNITES_CORPUS:
        return unites, False

    groupes: dict[str, list[UniteConsommateur]] = {}
    for unite in unites:
        groupes.setdefault(SOURCE_PAR_TYPE_UNITE[unite.source], []).append(unite)
    for lot in groupes.values():
        lot.sort(key=_cle_priorite, reverse=True)

    if len(groupes) <= 1:
        unique = next(iter(groupes.values()))
        return unique[:MAX_UNITES_CORPUS], True

    plafond = max(1, int(MAX_UNITES_CORPUS * PART_MAX_PAR_SOURCE))
    retenues: dict[str, list[UniteConsommateur]] = {}
    for source, lot in groupes.items():
        retenues[source] = lot[:plafond]

    # Redistribution de la capacité résiduelle aux sources encore fournies.
    capacite = MAX_UNITES_CORPUS - sum(len(lot) for lot in retenues.values())
    while capacite > 0:
        progresse = False
        for source, lot in groupes.items():
            if capacite <= 0:
                break
            deja = len(retenues[source])
            if deja < len(lot):
                retenues[source].append(lot[deja])
                capacite -= 1
                progresse = True
        if not progresse:
            break

    plates = [u for lot in retenues.values() for u in lot]
    plates.sort(key=_cle_priorite, reverse=True)
    return plates[:MAX_UNITES_CORPUS], True


def _langues_constatees(
    entrees: EntreesChargees, unites: list[UniteConsommateur]
) -> list[str]:
    """Constate les langues et écritures présentes dans le corpus.

    Heuristique assumée : la langue du marché et celles déclarées par les pages
    web sont reprises telles quelles ; une écriture non latine détectée dans les
    textes est signalée comme constat d'écriture, jamais comme langue certaine.

    Args:
        entrees: Fichiers d'entrée chargés.
        unites: Unités analysées.

    Returns:
        La liste triée des constats.
    """
    constats: set[str] = set()
    if entrees.marche:
        constats.add(entrees.marche.langue)
    if entrees.reddit is not None:
        constats.add("en (corpus Reddit majoritairement anglophone)")

    compte_ecritures: dict[str, int] = {}
    for unite in unites:
        if _MOJIBAKE.search(unite.texte):
            # Texte corrompu en amont : toute « écriture » qu'on y lirait serait
            # un artefact d'encodage. Signalé ailleurs, comme défaut de données.
            continue
        vues_unite: set[str] = set()
        for caractere in unite.texte[:400]:
            point = ord(caractere)
            for nom, debut, fin in _ECRITURES:
                if debut <= point <= fin:
                    vues_unite.add(nom)
                    break
        for nom in vues_unite:
            compte_ecritures[nom] = compte_ecritures.get(nom, 0) + 1
    for nom, occurrences in compte_ecritures.items():
        if occurrences >= MIN_UNITES_ECRITURE:
            constats.add(f"écriture {nom} constatée dans {occurrences} unité(s)")
    return sorted(constats)


def construire_corpus(entrees: EntreesChargees) -> CorpusPrepare:
    """Construit le corpus analysable à partir des fichiers chargés.

    Args:
        entrees: Fichiers d'entrée validés.

    Returns:
        Le corpus préparé, ses statistiques et ses limites méthodologiques.
    """
    brutes = _construire_unites_reddit(entrees) + _construire_unites_amazon(entrees)
    eligibles, exclusions = _filtrer(brutes)
    analysees, echantillonne = _echantillonner(eligibles)
    documents = _construire_documents(entrees)

    par_source: dict[str, int] = {}
    for unite in eligibles:
        source = SOURCE_PAR_TYPE_UNITE[unite.source]
        par_source[source] = par_source.get(source, 0) + 1

    repartition_portee: dict[str, int] = {}
    for unite in analysees:
        repartition_portee[unite.portee] = repartition_portee.get(unite.portee, 0) + 1

    dates = sorted(u.date for u in analysees if u.date)
    taux = round(len(analysees) / len(eligibles), 4) if eligibles else 1.0

    stats = StatsCorpus(
        nb_unites_par_source=par_source,
        nb_unites_analysees=len(analysees),
        nb_documents_analyses=len(documents),
        taux_echantillonnage=taux,
        periode_couverte={
            "min": dates[0] if dates else None,
            "max": dates[-1] if dates else None,
        },
        repartition_portee=repartition_portee,
        langues_constatees=_langues_constatees(entrees, analysees),
    )

    limites: list[str] = []
    nb_mojibake = sum(1 for u in analysees if _MOJIBAKE.search(u.texte))
    if nb_mojibake:
        limites.append(
            f"{nb_mojibake} unité(s) sur {len(analysees)} portent des caractères mal "
            f"décodés en amont (séquences de type « ΓÇô », « Ã© ») : le collecteur "
            f"a produit du texte doublement encodé. Les verbatims cités peuvent "
            f"contenir ces artefacts, et la classification de ces unités est "
            f"dégradée d'autant."
        )
    if echantillonne:
        limites.append(
            f"Le corpus éligible ({len(eligibles)} unités) dépassait le plafond "
            f"d'analyse ({MAX_UNITES_CORPUS}) : un échantillonnage stratifié par "
            f"source a été appliqué (taux {taux:.0%}, priorité à la pertinence "
            f"amont puis au poids social puis à la récence). Les fréquences "
            f"portent sur l'échantillon, pas sur le corpus collecté."
        )
    if exclusions["doublon"]:
        limites.append(
            f"{exclusions['doublon']} unité(s) écartée(s) comme doublons de texte : "
            f"les republications et citations gonflaient artificiellement les fréquences."
        )
    if len(par_source) == 1:
        source_unique = next(iter(par_source))
        limites.append(
            f"Corpus limité à une seule source ({source_unique}) : la lecture "
            f"reflète le public de cette source et non celui du marché."
        )
    if not documents and entrees.web is not None:
        limites.append(
            "Aucune page web ne servait l'axe consommateurs (axes_servis ne "
            "contient pas « axe1 ») : la source web n'apporte rien à cette analyse."
        )

    logger.debug(
        "corpus : %d unités brutes → %d éligibles → %d analysées ; %d documents ; "
        "exclusions %s",
        len(brutes),
        len(eligibles),
        len(analysees),
        len(documents),
        exclusions,
    )
    return CorpusPrepare(
        unites=analysees, documents=documents, stats=stats, limites=limites
    )
