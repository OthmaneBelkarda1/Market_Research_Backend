"""Orchestration de bout en bout de l'agent de restitution.

Séquence : chargement → préparation → rédaction → assemblage → post-validation
→ écriture des fichiers → assemblage du résultat.

Invariant central : **le rapport est toujours produit** dès lors que l'analyse
de synthèse est exploitable. Une analyse détaillée manquante dégrade une section
et y ajoute une mention explicite ; l'échec d'une chaîne de rédaction réduit une
section à ses tableaux et y ajoute un encart. Aucun de ces cas n'empêche
l'écriture du rapport ni la conformité de la sortie.
"""

from __future__ import annotations

from pathlib import Path

from assemblage import assembler_rapport, assembler_resume
from chargement import charger_entrees, inventorier_blocs
from config import (
    CONFIANCE_ELEVEE,
    CONFIANCE_FAIBLE,
    CONFIANCE_MOYENNE,
    GABARIT_RAPPORT,
    HYPOTHESES_SYSTEMATIQUES,
    LIMITES_SYSTEMATIQUES,
    SECTIONS_NARRATIVES,
    SECTION_PLC,
    SECTION_SYNTHESE,
    logger,
    verifier_cle_api,
)
from preparation import horodatage, preparer
from redaction import rediger_section
from schemas import (
    ConfianceGlobale,
    EntreesChargees,
    FicheProduit,
    Injectables,
    ParametresMarche,
    ResultatRestitution,
    SortieNarratif,
    StatutAnalyse,
)
from validation import (
    controler,
    enregistrer_contenu_code,
    nettoyer_narratifs,
    sections_a_regenerer,
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


def restituer(
    chemin_recommandations: str,
    chemin_insights: str | None,
    chemin_concurrence: str | None,
    chemin_plc: str | None,
    chemin_rapport: str | None,
    chemin_resume: str | None,
    langue_analyse: str,
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

    Returns:
        Les métadonnées et contrôles de la restitution.

    Raises:
        ErreurCoherenceProduit: Si les fichiers portent des produits différents.
        ValueError: Si l'analyse de synthèse est absente ou inexploitable.
        RuntimeError: Si la clé API est absente.
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
    enregistrer_contenu_code(liste, injectables)

    verifier_cle_api()

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

    # --- Écriture ----------------------------------------------------------- #
    chemin_rapport_ecrit: str | None = None
    chemin_resume_ecrit: str | None = None
    if chemin_rapport:
        Path(chemin_rapport).write_text(rapport, encoding="utf-8")
        chemin_rapport_ecrit = str(Path(chemin_rapport))
        logger.info("rapport écrit dans %s", chemin_rapport_ecrit)
    if chemin_resume:
        Path(chemin_resume).write_text(resume, encoding="utf-8")
        chemin_resume_ecrit = str(Path(chemin_resume))
        logger.info("résumé écrit dans %s", chemin_resume_ecrit)

    # --- Résultat ----------------------------------------------------------- #
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
    if controles.nb_nombres_retires:
        limites.append(
            f"{controles.nb_nombres_retires} nombre(s) proposés à la rédaction ne "
            f"correspondaient à aucune donnée des analyses fournies : les phrases "
            f"porteuses ont été retirées du rapport."
        )

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
