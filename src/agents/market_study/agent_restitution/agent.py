"""Orchestration de bout en bout de l'agent de restitution.

Séquence : chargement → préparation → rédaction → assemblage → post-validation
→ écriture des fichiers → assemblage du résultat.

Invariant central, et il diffère selon le gabarit.

**v1** : le rapport est toujours produit dès lors que l'analyse de synthèse est
exploitable. Une analyse détaillée manquante dégrade une section et y ajoute une
mention ; l'échec d'une chaîne de rédaction réduit la section à ses tableaux et y
ajoute un encart. Ce contrat est inchangé.

**v2** : une donnée absente dégrade — le sous-bloc affiche sa phrase standard —
mais un échec de RÉDACTION ne dégrade plus, il arrête. Le run 8609db9e a livré un
rapport dont les sept sous-blocs narratifs disaient « lecture narrative
indisponible », et l'étude a été marquée `completed` : un décideur a reçu un
document sans une phrase d'analyse, avec l'apparence d'une étude complète. Un
rapport v2 sans narratif n'est pas un rapport dégradé, c'est un échec — il sort
en `CODE_REDACTION_IMPOSSIBLE`, sans écrire aucun fichier, et l'orchestrateur
marque l'étude `partial` en conservant les JSON F3–F6 pour rejouer F7 seul.
"""

from __future__ import annotations

from pathlib import Path

from assemblage import assembler_rapport, assembler_resume
from assemblage_v2 import assembler_rapport_v2, assembler_resume_v2
from chargement import charger_entrees, inventorier_blocs
from config import (
    CONFIANCE_ELEVEE,
    CONFIANCE_FAIBLE,
    CONFIANCE_MOYENNE,
    BUDGET_MOTS,
    GABARIT_PAR_DEFAUT,
    GABARIT_RAPPORT,
    GABARIT_RAPPORT_V2,
    ECRAN_DECISION,
    GABARIT_V2,
    HYPOTHESES_SYSTEMATIQUES,
    MAX_MOTS_ACTION,
    MAX_MOTS_CELLULE_COURTE,
    MAX_MOTS_PUCE,
    CONTRAT_SOUS_BLOCS,
    LIMITES_SYSTEMATIQUES,
    SECTIONS_NARRATIVES,
    SECTION_PLC,
    SECTION_SYNTHESE,
    RedactionImpossible,
    logger,
    verifier_cle_api,
)
from preparation import ListeBlanche, horodatage, preparer
from preparation_v2 import enrichir
from redaction import rediger_section
from redaction_v2 import (
    compresser_cellules,
    ecarts_au_contrat,
    rangs_chiffrables,
    rediger_ecran,
    sous_blocs_a_rediger,
)
from schemas import (
    ConfianceGlobale,
    ControlesRestitution,
    EntreesChargees,
    FicheProduit,
    Injectables,
    ParametresMarche,
    ResultatRestitution,
    SortieEcran,
    SortieNarratif,
    StatutAnalyse,
)
from validation import (
    controler,
    enregistrer_contenu_code,
    nettoyer_narratifs,
    sections_a_regenerer,
)
from validation_v2 import (
    controler_v2,
    couper_au_budget,
    ecrans_a_regenerer,
    nettoyer_ecrans,
    retenir_compression,
)

CONSIGNE_REGENERATION: str = (
    "\n\nREPRISE — ta réponse précédente a été largement retirée à la relecture : "
    "elle contenait des chiffres absents des données fournies, ou un vocabulaire "
    "proscrit. Reformule en te limitant STRICTEMENT aux chiffres présents dans les "
    "données ci-dessous, recopiés à l'identique. En cas de doute sur un chiffre, "
    "n'en cite aucun : une phrase qualitative exacte vaut mieux qu'une phrase "
    "chiffrée fausse."
)

ORDRE_CONFIANCE: tuple[str, ...] = (CONFIANCE_FAIBLE, CONFIANCE_MOYENNE, CONFIANCE_ELEVEE)


def _sections_a_rediger(entrees: EntreesChargees, injectables: Injectables) -> list[dict]:
    """Détermine les sections dont le narratif doit être rédigé.

    Args:
        entrees: Fichiers d'entrée validés.
        injectables: Données injectables.

    Returns:
        Les entrées de gabarit concernées.
    """
    a_rediger: list[dict] = []
    for gabarit in GABARIT_RAPPORT:
        if gabarit["id"] not in SECTIONS_NARRATIVES:
            continue
        if gabarit["id"] == SECTION_PLC and not injectables.phase_lisible:
            continue
        a_rediger.append(gabarit)
    _ = entrees
    return a_rediger


def _confiance_globale(entrees: EntreesChargees) -> ConfianceGlobale:
    """Hérite la confiance globale du minimum des confiances des entrées.

    Args:
        entrees: Fichiers d'entrée validés.

    Returns:
        Le niveau de confiance et sa justification.
    """
    niveaux: list[str] = []
    facteurs: list[str] = []
    for nom in entrees.blocs_disponibles:
        entree = getattr(entrees, nom, None)
        confiance = getattr(entree, "confiance_globale", None) if entree else None
        niveau = confiance.niveau if confiance else None
        if niveau in ORDRE_CONFIANCE:
            niveaux.append(niveau)
        facteurs.append(f"[{nom}] confiance amont {niveau or 'non déclarée'}")

    manquantes = [
        gabarit["id"]
        for gabarit in GABARIT_RAPPORT
        for requise in gabarit["entrees_requises"]
        if not entrees.presente(requise)
    ]
    niveau_retenu = (
        min(niveaux, key=ORDRE_CONFIANCE.index) if niveaux else CONFIANCE_FAIBLE
    )
    if manquantes:
        justification = (
            f"Étude partielle : {len(set(manquantes))} section(s) construite(s) sans "
            f"leur analyse détaillée. La confiance est en outre bornée par la plus "
            f"faible des confiances héritées."
        )
    else:
        justification = (
            "Toutes les analyses attendues sont disponibles. La confiance retenue est "
            "la plus faible des confiances héritées : un rapport n'est pas plus sûr "
            "que l'analyse la moins sûre qu'il restitue."
        )
    return ConfianceGlobale(
        niveau=niveau_retenu, justification=justification, facteurs=facteurs
    )


def _ecrire(chemin: str | None, contenu: str, libelle: str) -> str | None:
    """Écrit un document, ou n'écrit rien si aucun chemin n'est demandé.

    Args:
        chemin: Chemin du fichier ; chaîne vide ou `None` pour ne rien écrire.
        contenu: Markdown à écrire.
        libelle: Nom du document, pour la journalisation.

    Returns:
        Le chemin écrit, ou `None`.
    """
    if not chemin:
        return None
    Path(chemin).write_text(contenu, encoding="utf-8")
    ecrit = str(Path(chemin))
    logger.info("%s écrit dans %s", libelle, ecrit)
    return ecrit


def _limites(
    sources: list,
    degradees: list[str],
    absentes: list[str],
    nb_nombres_retires: int,
) -> list[str]:
    """Consolide les limites publiées avec le rapport.

    Args:
        sources: Comptes rendus de chargement.
        degradees: Sections construites depuis l'écho de synthèse.
        absentes: Sections remplacées par un encart standard.
        nb_nombres_retires: Nombres hors liste blanche retirés à la relecture.

    Returns:
        Les limites, dans l'ordre de publication.
    """
    limites = list(LIMITES_SYSTEMATIQUES)
    for compte_rendu in sources:
        limites.extend(
            f"[{compte_rendu.source}] {avertissement}"
            for avertissement in compte_rendu.avertissements
        )
    for section in degradees:
        limites.append(
            f"Section « {section} » construite depuis le rappel de l'analyse de "
            f"synthèse : son analyse détaillée n'a pas été fournie."
        )
    for section in absentes:
        limites.append(
            f"Section « {section} » sans contenu : l'analyse correspondante n'a pas "
            f"été fournie."
        )
    if nb_nombres_retires:
        limites.append(
            f"{nb_nombres_retires} nombre(s) proposés à la rédaction ne "
            f"correspondaient à aucune donnée des analyses fournies : les phrases "
            f"porteuses ont été retirées du rapport."
        )
    return limites


CONSIGNE_BUDGET: str = (
    "\n\nREPRISE — l'écran précédent dépassait son budget de mots. Produis les "
    "mêmes idées, dans le même ordre, en supprimant tout ce qui n'aide pas à "
    "décider : adverbes, redites, formules de liaison. Ne supprime aucune puce, "
    "raccourcis-les."
)


def _comprimer_cellules_longues(
    injectables: Injectables, langue_analyse: str
) -> tuple[list[StatutAnalyse], int]:
    """Raccourcit par rédaction les cellules qui dépassent leur budget.

    C'est ce qui remplace la troncature à « … » du gabarit v1 : une cellule
    longue est réécrite plus court, jamais coupée au milieu de son argument. Une
    compression qui introduirait un chiffre absent de l'original est rejetée, et
    l'original INTÉGRAL prend alors le relais — long, mais exact.

    Les puces d'opportunités et de risques y passent depuis ce correctif. Leurs
    titres étaient coupés à douze mots en amont, ce qui les rendait
    inintelligibles sans que rien ne le signale ; ils arrivent maintenant entiers,
    et c'est ici qu'ils sont ramenés à leur budget quand ils le dépassent.

    Args:
        injectables: Données injectables, complétées sur place.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le couple `(statuts, nb_cellules_compressées)`.
    """
    statuts: list[StatutAnalyse] = []
    compressees = 0

    forces = [ligne["force_brute"] for ligne in injectables.concurrents_v2]
    faiblesses = [ligne["faiblesse_brute"] for ligne in injectables.concurrents_v2]
    cellules = forces + faiblesses
    if any(cellules):
        proposes, statut = compresser_cellules(
            cellules, MAX_MOTS_CELLULE_COURTE, langue_analyse
        )
        statuts.append(statut)
        retenus, acceptees = retenir_compression(
            cellules, proposes, MAX_MOTS_CELLULE_COURTE
        )
        compressees += acceptees
        milieu = len(forces)
        for rang, ligne in enumerate(injectables.concurrents_v2):
            ligne["force"] = retenus[rang]
            ligne["faiblesse"] = retenus[milieu + rang]

    puces_longues = [
        (source, rang, puce)
        for source, puces in (
            ("puces_opportunites", injectables.puces_opportunites),
            ("puces_risques", injectables.puces_risques),
        )
        for rang, puce in enumerate(puces)
        if len(puce.split()) > MAX_MOTS_PUCE
    ]
    if puces_longues:
        textes = [puce for _, _, puce in puces_longues]
        proposes, statut = compresser_cellules(textes, MAX_MOTS_PUCE, langue_analyse)
        statuts.append(statut)
        retenus, acceptees = retenir_compression(textes, proposes, MAX_MOTS_PUCE)
        compressees += acceptees
        for (source, rang, _), retenu in zip(puces_longues, retenus, strict=True):
            getattr(injectables, source)[rang] = retenu

    longues = [
        action["enonce_brut"]
        for action in injectables.actions_p1
        if not action.get("enonce")
    ]
    if longues:
        proposes, statut = compresser_cellules(longues, MAX_MOTS_ACTION, langue_analyse)
        statuts.append(statut)
        retenus, acceptees = retenir_compression(longues, proposes, MAX_MOTS_ACTION)
        compressees += acceptees
        rang = 0
        for action in injectables.actions_p1:
            if not action.get("enonce"):
                action["enonce"] = retenus[rang]
                rang += 1
    return statuts, compressees


def _comprimer_puces_hors_budget(
    ecran: str,
    sortie: SortieEcran | None,
    injectables: Injectables,
    langue_analyse: str,
) -> tuple[SortieEcran | None, StatutAnalyse | None]:
    """Réécrit plus court les puces qui dépassent le budget de leur sous-bloc.

    La règle du v2 est qu'aucune réduction ne coupe : elle passe par la chaîne de
    compression, qui reformule. Une compression rejetée par la liste blanche rend
    la puce d'origine — trop longue, mais exacte — et le contrôle de conformité
    la refusera ensuite. C'est l'ordre voulu : mieux vaut un écran refusé qu'un
    écran publié avec une phrase amputée de son argument.

    Args:
        ecran: Identifiant de l'écran.
        sortie: Puces produites, ou `None`.
        injectables: Données injectables.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le couple `(sortie_réécrite, statut_ou_None)`. Le statut est `None`
        lorsqu'aucune puce ne dépassait.
    """
    if sortie is None:
        return sortie, None
    trop_longues = [
        (sous_bloc, rang, puce, CONTRAT_SOUS_BLOCS[sous_bloc].max_mots_puce)
        for sous_bloc, puces in sortie.sous_blocs.items()
        if sous_bloc in CONTRAT_SOUS_BLOCS
        for rang, puce in enumerate(puces)
        if len(puce.split()) > CONTRAT_SOUS_BLOCS[sous_bloc].max_mots_puce
    ]
    if not trop_longues:
        return sortie, None

    # Un seul appel, au budget le plus serré des puces concernées : la chaîne de
    # compression prend une liste et un plafond unique.
    budget = min(maximum for _, _, _, maximum in trop_longues)
    textes = [puce for _, _, puce, _ in trop_longues]
    logger.info(
        "écran %s : %d puce(s) au-dessus de leur budget, compression à %d mots",
        ecran, len(textes), budget,
    )
    proposes, statut = compresser_cellules(textes, budget, langue_analyse)
    retenus, _ = retenir_compression(textes, proposes, budget)
    for (sous_bloc, rang, _, _), retenu in zip(trop_longues, retenus, strict=True):
        sortie.sous_blocs[sous_bloc][rang] = retenu
    statut.phase = f"compression_puces_{ecran}"
    return sortie, statut


def _rediger_ecrans(
    injectables: Injectables,
    liste: ListeBlanche,
    produit: str,
    libelle_marche: str,
    langue_analyse: str,
) -> tuple[dict[str, SortieEcran | None], list[StatutAnalyse], dict]:
    """Rédige les écrans narratifs, les nettoie, les régénère et les borne.

    Trois passes au plus par écran : la rédaction, une régénération si le
    nettoyage a trop retiré, une réduction si le budget de mots est dépassé. La
    coupe finale retire des puces entières par la fin — jamais le contenu d'une
    puce, qui produirait une phrase fausse.

    Args:
        injectables: Données injectables.
        liste: Liste blanche numérique.
        produit: Nom du produit étudié.
        libelle_marche: Libellé du marché.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le triplet `(narratifs, statuts, compteurs)`.
    """
    statuts: list[StatutAnalyse] = []
    narratifs: dict[str, SortieEcran | None] = {}
    ecrans = {e["id"]: e for e in GABARIT_RAPPORT_V2 if e["narratif"]}

    for identifiant, ecran in ecrans.items():
        sortie, statut = rediger_ecran(
            identifiant,
            ecran["titre"],
            ecran["budget_mots"],
            injectables,
            produit,
            libelle_marche,
            langue_analyse,
        )
        statuts.append(statut)
        if not statut.succes:
            # UNE régénération, et elle dit au modèle ce qui manquait — « 2 puces
            # au lieu de 3 », « puce 1 sans chiffre ». Le v1 renvoyait la même
            # entrée à l'identique : sur le run 8609db9e, les huit invocations
            # ont produit huit fois la même erreur au mot près.
            logger.info(
                "écran %s hors gabarit, régénération : %s",
                identifiant,
                statut.message_erreur,
            )
            sortie, reprise = rediger_ecran(
                identifiant,
                ecran["titre"],
                ecran["budget_mots"],
                injectables,
                produit,
                libelle_marche,
                langue_analyse,
                erreur_precedente=statut.message_erreur or "",
            )
            reprise.phase = f"conformite_{identifiant}"
            statuts.append(reprise)
            if not reprise.succes:
                # Dernier recours avant l'échec, et il ne coupe rien : une puce
                # qui dépasse de trois mots est RÉÉCRITE plus court. Un écart de
                # structure — deux puces au lieu de trois, un libellé manquant —
                # n'a pas de recours et arrête ici ; un écart de longueur en a un,
                # et arrêter le module pour trois mots serait disproportionné.
                sortie, statut_compression = _comprimer_puces_hors_budget(
                    identifiant, sortie, injectables, langue_analyse
                )
                if statut_compression is not None:
                    statuts.append(statut_compression)
                restants = ecarts_au_contrat(
                    sortie,
                    sous_blocs_a_rediger(identifiant, injectables),
                    rangs_chiffrables(injectables),
                    structurels_seuls=True,
                )
                if restants:
                    raise RedactionImpossible(
                        f"écran « {identifiant} » : la sortie reste hors gabarit "
                        f"après régénération et compression — {' ; '.join(restants)}",
                        statuts,
                    )
        narratifs[identifiant] = sortie

    narratifs, compteurs = nettoyer_ecrans(narratifs, liste)

    for identifiant in ecrans_a_regenerer(compteurs):
        logger.info("régénération de l'écran %s", identifiant)
        sortie, statut = rediger_ecran(
            identifiant,
            ecrans[identifiant]["titre"],
            ecrans[identifiant]["budget_mots"],
            injectables,
            produit,
            libelle_marche,
            langue_analyse,
            consigne_supplementaire=CONSIGNE_REGENERATION,
        )
        statut.phase = f"regeneration_{identifiant}"
        statuts.append(statut)
        if sortie is not None:
            reprises, compte = nettoyer_ecrans({identifiant: sortie}, liste)
            narratifs[identifiant] = reprises[identifiant]
            for cle, valeur in compte[identifiant].items():
                compteurs[identifiant][cle] += valeur

    for identifiant, sortie in list(narratifs.items()):
        if sortie is None:
            continue
        budget = BUDGET_MOTS.get(identifiant, 0)
        mots = sum(len(p.split()) for puces in sortie.sous_blocs.values() for p in puces)
        if not budget or mots <= budget:
            continue
        logger.info("écran %s hors budget (%d mots > %d)", identifiant, mots, budget)
        reprise, statut = rediger_ecran(
            identifiant,
            ecrans[identifiant]["titre"],
            budget,
            injectables,
            produit,
            libelle_marche,
            langue_analyse,
            consigne_supplementaire=CONSIGNE_BUDGET,
        )
        statut.phase = f"reduction_{identifiant}"
        statuts.append(statut)
        if reprise is not None:
            nettoyee, _ = nettoyer_ecrans({identifiant: reprise}, liste)
            sortie = nettoyee[identifiant] or sortie
        sortie, retirees = couper_au_budget(sortie, budget)
        narratifs[identifiant] = sortie
        if retirees:
            statuts.append(
                StatutAnalyse(
                    phase=f"reduction_{identifiant}",
                    succes=True,
                    message_erreur=(
                        f"{retirees} puce(s) retirée(s) par la fin pour tenir le "
                        f"budget de {budget} mots ; aucune puce n'a été amputée."
                    ),
                    nb_elements=retirees,
                )
            )
    return narratifs, statuts, compteurs


CONTROLES_BLOQUANTS_V2: tuple[tuple[str, str], ...] = (
    ("gabarit_conforme", "des sous-blocs s'écartent du gabarit"),
    ("aucun_repli_interdit", "un texte de repli interdit subsiste"),
    ("aucune_troncature", "un texte porte la signature d'une coupe machine"),
    ("ligne_sources_complete", "la ligne « Sources analysées » est incomplète"),
)
"""Contrôles qui ARRÊTENT le module au lieu de dégrader le rapport.

Les autres contrôles de `controler_v2` restent informatifs : ils décrivent un
rapport qui part quand même. Ces quatre-là décrivent un rapport qu'il vaut mieux
ne pas envoyer — leur point commun est qu'un lecteur ne peut pas voir le défaut
et le corrigera donc jamais de lui-même. Un budget dépassé se voit ; un
« Pourquoi » à deux puces au lieu de trois, non."""


def _refuser_si_non_conforme(
    controles: ControlesRestitution,
    statuts: list[StatutAnalyse] | None = None,
) -> None:
    """Interrompt la restitution si un contrôle bloquant est en échec.

    Appelée APRÈS l'assemblage et AVANT toute écriture : le rapport existe en
    mémoire, il est complet, et c'est précisément parce qu'il a l'air complet
    qu'il ne doit pas atteindre le disque.

    Args:
        controles: Résultat de la post-validation v2.
        statuts: Statuts accumulés, joints à l'exception pour le diagnostic.

    Raises:
        RedactionImpossible: Si au moins un contrôle bloquant est faux.
    """
    motifs = [
        motif for champ, motif in CONTROLES_BLOQUANTS_V2 if not getattr(controles, champ)
    ]
    if not motifs:
        return
    detail = (
        f" ({'; '.join(controles.sous_blocs_non_conformes[:5])})"
        if controles.sous_blocs_non_conformes
        else ""
    )
    raise RedactionImpossible(
        "rapport refusé par la post-validation : " + ", ".join(motifs) + detail,
        statuts,
    )


def restituer(
    chemin_recommandations: str,
    chemin_insights: str | None,
    chemin_concurrence: str | None,
    chemin_plc: str | None,
    chemin_rapport: str | None,
    chemin_resume: str | None,
    langue_analyse: str,
    gabarit: str = GABARIT_PAR_DEFAUT,
    etat_sources: dict[str, dict] | None = None,
) -> ResultatRestitution:
    """Produit le rapport d'étude de marché et son résumé exécutif.

    Args:
        chemin_recommandations: Sortie de l'analyse de synthèse — requise.
        chemin_insights: Sortie de l'analyse des avis, ou `None`.
        chemin_concurrence: Sortie de l'analyse concurrentielle, ou `None`.
        chemin_plc: Sortie de l'analyse de cycle de vie, ou `None`.
        chemin_rapport: Fichier du rapport ; chaîne vide pour ne pas l'écrire.
        chemin_resume: Fichier du résumé ; chaîne vide pour ne pas l'écrire.
        langue_analyse: Code langue de rédaction.
        gabarit: `v2` pour le rapport décisionnel, `v1` pour l'ancien rendu.
        etat_sources: État des collecteurs transmis par l'orchestrateur
            (`--sources-etat`) : `donnees_disponibles`, `nb_items`, `raison` par
            source. Il porte la RAISON d'une collecte vide, que les JSON
            d'analyse ne conservent pas.

    Returns:
        Les métadonnées et contrôles de la restitution.

    Raises:
        ErreurCoherenceProduit: Si les fichiers portent des produits différents.
        ValueError: Si l'analyse de synthèse est absente ou inexploitable.
        RuntimeError: Si la clé API est absente.
        RedactionImpossible: Si un écran narratif n'a pas pu être rédigé
            conformément au gabarit v2. Aucun fichier n'est alors écrit.
    """
    entrees, sources, alertes, bruts = charger_entrees(
        chemin_recommandations, chemin_insights, chemin_concurrence, chemin_plc
    )
    if entrees.recommandations is None:
        raise ValueError(
            "la sortie de l'analyse de synthèse (--recommandations) est absente ou "
            "inexploitable : sans verdict ni dossier, il n'y a pas de rapport à "
            "écrire."
        )

    produit = entrees.produit or FicheProduit(nom="inconnu", description="")
    marche = entrees.marche or ParametresMarche(geo="??", langue=langue_analyse)
    libelle_marche = f"{marche.geo} ({marche.langue})"

    degradees, absentes = inventorier_blocs(entrees)
    injectables, liste, statuts, hypotheses_preparation = preparer(
        entrees, bruts, degradees, absentes
    )
    if gabarit == GABARIT_V2:
        statuts_v2, hypotheses_v2 = enrichir(
            injectables, entrees, degradees, absentes, etat_sources
        )
        statuts.extend(statuts_v2)
        hypotheses_preparation.extend(hypotheses_v2)
        injectables.hypotheses = list(injectables.hypotheses) + hypotheses_v2
    enregistrer_contenu_code(liste, injectables)

    verifier_cle_api()

    if gabarit == GABARIT_V2:
        statuts_compression, nb_compressees = _comprimer_cellules_longues(
            injectables, langue_analyse
        )
        statuts.extend(statuts_compression)
        ecrans, statuts_ecrans, compteurs = _rediger_ecrans(
            injectables, liste, produit.nom, libelle_marche, langue_analyse
        )
        statuts.extend(statuts_ecrans)
        rapport, sections_produites = assembler_rapport_v2(injectables, ecrans)
        resume = assembler_resume_v2(injectables, ecrans)
        controles, statuts_validation = controler_v2(
            rapport, resume, injectables, liste, compteurs, sections_produites, ecrans
        )
        controles.nb_cellules_compressees = nb_compressees
        statuts.extend(statuts_validation)
        _refuser_si_non_conforme(controles, statuts)
        decision = ecrans.get(ECRAN_DECISION)
        narratif_synthese_texte = "\n".join(
            puce
            for puces in (decision.sous_blocs.values() if decision else [])
            for puce in puces
        )
        return ResultatRestitution(
            produit=produit,
            marche=marche,
            horodatage_utc=horodatage(),
            sources_utilisees=sources,
            alertes_coherence=alertes,
            sections_produites=sections_produites,
            controles=controles,
            chemin_rapport=_ecrire(chemin_rapport, rapport, "rapport"),
            chemin_resume=_ecrire(chemin_resume, resume, "résumé"),
            synthese_executive=narratif_synthese_texte,
            statuts_analyse=statuts,
            donnees_suffisantes=True,
            confiance_globale=_confiance_globale(entrees),
            limites=_limites(
                sources, degradees, absentes, controles.nb_nombres_retires
            ),
            hypotheses=list(HYPOTHESES_SYSTEMATIQUES) + hypotheses_preparation,
        )

    # --- Rédaction ---------------------------------------------------------- #
    narratifs: dict[str, SortieNarratif | None] = {}
    for gabarit in _sections_a_rediger(entrees, injectables):
        sortie, statut = rediger_section(
            gabarit["id"],
            gabarit["titre"],
            gabarit["longueur_narrative_max_mots"],
            injectables,
            produit.nom,
            libelle_marche,
            langue_analyse,
        )
        narratifs[gabarit["id"]] = sortie
        statuts.append(statut)

    # --- Nettoyage et régénération unique ----------------------------------- #
    narratifs, compteurs = nettoyer_narratifs(narratifs, liste)
    for section in sections_a_regenerer(compteurs):
        gabarit = next(g for g in GABARIT_RAPPORT if g["id"] == section)
        logger.info("régénération du narratif de la section %s", section)
        sortie, statut = rediger_section(
            section,
            gabarit["titre"],
            gabarit["longueur_narrative_max_mots"],
            injectables,
            produit.nom,
            libelle_marche,
            langue_analyse,
            consigne_supplementaire=CONSIGNE_REGENERATION,
        )
        statut.phase = f"regeneration_{section}"
        statuts.append(statut)
        if sortie is not None:
            reprises, compte_reprise = nettoyer_narratifs({section: sortie}, liste)
            narratifs[section] = reprises[section]
            for cle, valeur in compte_reprise[section].items():
                compteurs[section][cle] += valeur

    # --- Assemblage et post-validation -------------------------------------- #
    rapport, sections_produites = assembler_rapport(injectables, narratifs)
    resume = assembler_resume(injectables, narratifs)
    controles, statuts_validation = controler(
        rapport, resume, injectables, liste, compteurs
    )
    statuts.extend(statuts_validation)

    # --- Écriture et résultat ----------------------------------------------- #
    chemin_rapport_ecrit = _ecrire(chemin_rapport, rapport, "rapport")
    chemin_resume_ecrit = _ecrire(chemin_resume, resume, "résumé")
    limites = _limites(sources, degradees, absentes, controles.nb_nombres_retires)

    narratif_synthese = narratifs.get(SECTION_SYNTHESE)
    resultat = ResultatRestitution(
        produit=produit,
        marche=marche,
        horodatage_utc=horodatage(),
        sources_utilisees=sources,
        alertes_coherence=alertes,
        sections_produites=sections_produites,
        controles=controles,
        chemin_rapport=chemin_rapport_ecrit,
        chemin_resume=chemin_resume_ecrit,
        synthese_executive=(
            "\n\n".join(narratif_synthese.paragraphes) if narratif_synthese else ""
        ),
        statuts_analyse=statuts,
        donnees_suffisantes=True,
        confiance_globale=_confiance_globale(entrees),
        limites=limites,
        hypotheses=list(HYPOTHESES_SYSTEMATIQUES) + hypotheses_preparation,
    )
    return resultat
