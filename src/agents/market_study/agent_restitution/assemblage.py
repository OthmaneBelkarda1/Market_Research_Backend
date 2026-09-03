"""Assemblage du rapport et du résumé exécutif — **aucun appel LLM ici**.

Le rendu est entièrement déterministe : titres, badges en blockquote, tableaux
injectés, narratifs insérés, encarts standard, et un commentaire HTML de
traçabilité en fin de chaque section. Ce commentaire est invisible au rendu
Markdown : il sert à l'audit, pas au lecteur.
"""

from __future__ import annotations

from config import (
    AVERTISSEMENT_METHODE,
    ENCART_NARRATIF_INDISPONIBLE,
    ENTREE_CONCURRENCE,
    ENTREE_INSIGHTS,
    ENTREE_PLC,
    ENTREE_RECOMMANDATIONS,
    GABARIT_RAPPORT,
    GLOSSAIRE,
    METHODOLOGIE,
    SECTION_ANNEXE,
    SECTION_CONCURRENCE,
    SECTION_CONSOMMATEURS,
    SECTION_DEMANDE,
    SECTION_ENTETE,
    SECTION_OPPORTUNITES_RISQUES,
    SECTION_PLC,
    SECTION_RECOMMANDATIONS,
    SECTION_SYNTHESE,
    SECTION_VERDICT,
    VERDICT_LISIBLE,
)
from schemas import Injectables, SectionProduite, SortieNarratif

TITRES_PRIORITES: dict[str, str] = {
    "P1": "Priorité 1 — à engager en premier",
    "P2": "Priorité 2 — à instruire ensuite",
    "P3": "Priorité 3 — à garder en réserve",
}


def compter_mots(texte: str) -> int:
    """Compte les mots d'un texte.

    Args:
        texte: Texte à mesurer.

    Returns:
        Le nombre de mots.
    """
    return len((texte or "").split())


def _narratif(narratifs: dict[str, SortieNarratif | None], section: str) -> str:
    """Rend le narratif d'une section, ou son encart de repli.

    Args:
        narratifs: Narratifs produits, par section.
        section: Identifiant de la section.

    Returns:
        Le texte Markdown du narratif.
    """
    sortie = narratifs.get(section)
    if sortie is None or not [p for p in sortie.paragraphes if p.strip()]:
        return ENCART_NARRATIF_INDISPONIBLE
    return "\n\n".join(p.strip() for p in sortie.paragraphes if p.strip())


def _bloc(*morceaux: str) -> str:
    """Assemble des morceaux non vides en un bloc Markdown.

    Args:
        *morceaux: Morceaux de texte, éventuellement vides.

    Returns:
        Le bloc, morceaux séparés d'une ligne blanche.
    """
    return "\n\n".join(m.strip() for m in morceaux if m and m.strip())


def _liste(elements: list[str]) -> str:
    """Rend une liste à puces Markdown.

    Args:
        elements: Éléments de la liste.

    Returns:
        La liste, ou une chaîne vide.
    """
    return "\n".join(f"- {e.strip()}" for e in elements if e and e.strip())


def _commentaire_sources(refs: list[str]) -> str:
    """Rend le commentaire HTML de traçabilité d'une section.

    Args:
        refs: Références des sources de la section.

    Returns:
        Le commentaire HTML, invisible au rendu.
    """
    return f"<!-- sources: {'; '.join(refs) if refs else 'aucune'} -->"


def _titre_verdict(injectables: Injectables) -> str:
    """Construit le titre de la section verdict, sans adoucissement.

    Args:
        injectables: Données injectables.

    Returns:
        Le titre, portant le mot du verdict tel quel.
    """
    return f"Verdict de potentiel : {injectables.verdict_lisible}"


def _titre_plc(injectables: Injectables) -> str:
    """Construit le titre de la section cycle de vie.

    Args:
        injectables: Données injectables.

    Returns:
        Le titre, portant la phase telle quelle si elle existe.
    """
    if injectables.phase_lisible:
        return f"Phase de cycle de vie du marché : {injectables.phase_lisible}"
    return "Phase de cycle de vie du marché"


def _entete(injectables: Injectables) -> str:
    """Rend l'en-tête du rapport.

    Args:
        injectables: Données injectables.

    Returns:
        Le bloc d'en-tête Markdown.
    """
    meta = injectables.entete
    lignes = [
        f"# Étude de marché — {meta.get('produit', '')}",
        "",
        f"**Marché étudié** : {meta.get('marche', '')} "
        f"(langue d'étude : {meta.get('langue', '')})  ",
        f"**Catégorie** : {meta.get('categorie', '')}  ",
        f"**Date de l'étude** : {meta.get('date_run', '')}  ",
        f"**Portée de l'étude** : {meta.get('portee', '')}",
        "",
        f"> {meta.get('description', '')}",
        "",
        "**Avertissement de méthode**",
        "",
        _liste(list(AVERTISSEMENT_METHODE)),
    ]
    return "\n".join(lignes)


def _section_synthese(
    injectables: Injectables, narratifs: dict[str, SortieNarratif | None]
) -> str:
    """Rend la synthèse exécutive.

    Args:
        injectables: Données injectables.
        narratifs: Narratifs produits.

    Returns:
        Le corps de la section.
    """
    sortie = narratifs.get(SECTION_SYNTHESE)
    reserves = list(sortie.puces) if sortie else []
    return _bloc(
        injectables.badges.get(SECTION_SYNTHESE, ""),
        _narratif(narratifs, SECTION_SYNTHESE),
        "**Ce que l'étude établit**" if injectables.faits_cles else "",
        _liste(injectables.faits_cles),
        "**Ce qu'elle recommande en priorité**"
        if injectables.recommandations_majeures
        else "",
        _liste(injectables.recommandations_majeures),
        f"**Risque principal** — {injectables.risque_principal}"
        if injectables.risque_principal
        else "",
        "**Réserves majeures**" if reserves else "",
        _liste(reserves),
    )


def _section_verdict(
    injectables: Injectables, narratifs: dict[str, SortieNarratif | None]
) -> str:
    """Rend la section verdict.

    Args:
        injectables: Données injectables.
        narratifs: Narratifs produits.

    Returns:
        Le corps de la section.
    """
    if injectables.bascules:
        bascules = _bloc(
            "**Ce qui ferait changer ce verdict**",
            _liste([b.enonce for b in injectables.bascules]),
        )
    else:
        bascules = (
            "**Ce qui ferait changer ce verdict** — aucune amélioration d'un seul "
            "critère de la grille ne suffirait à changer le verdict."
        )
    return _bloc(
        injectables.badges.get(SECTION_VERDICT, ""),
        _narratif(narratifs, SECTION_VERDICT),
        injectables.tableau_grille,
        f"> **La règle appliquée.** {injectables.regle_litterale}",
        bascules,
        "**Ce qui manque pour trancher**" if injectables.donnees_a_completer else "",
        _liste(injectables.donnees_a_completer),
    )


def _section_plc(
    injectables: Injectables, narratifs: dict[str, SortieNarratif | None]
) -> str:
    """Rend la section cycle de vie, ou son encart de non-déclenchement.

    Args:
        injectables: Données injectables.
        narratifs: Narratifs produits.

    Returns:
        Le corps de la section.
    """
    if injectables.encart_plc:
        return injectables.encart_plc
    return _bloc(
        injectables.badges.get(SECTION_PLC, ""),
        _narratif(narratifs, SECTION_PLC),
        f"**Incertitude de la classification** : {injectables.incertitude_phase}.",
        injectables.tableau_signaux_plc,
        "**Ce que cette phase impose**" if injectables.recommandations_phase else "",
        injectables.recommandations_phase,
    )


def _section_demande(
    injectables: Injectables, narratifs: dict[str, SortieNarratif | None]
) -> str:
    """Rend la section demande.

    Args:
        injectables: Données injectables.
        narratifs: Narratifs produits.

    Returns:
        Le corps de la section.
    """
    return _bloc(
        injectables.badges.get(SECTION_DEMANDE, ""),
        _narratif(narratifs, SECTION_DEMANDE),
        injectables.tableau_demande,
    )


def _section_consommateurs(
    injectables: Injectables, narratifs: dict[str, SortieNarratif | None]
) -> str:
    """Rend la section consommateurs.

    Args:
        injectables: Données injectables.
        narratifs: Narratifs produits.

    Returns:
        Le corps de la section.
    """
    irritants: list[str] = []
    for point in injectables.pain_points:
        entete = f"**{point['libelle']}**"
        details = " · ".join(
            partie
            for partie in (
                f"{point['frequence']} des contributions" if point.get("frequence") else "",
                f"intensité moyenne {point['intensite']}" if point.get("intensite") else "",
            )
            if partie
        )
        morceaux = [f"{entete}{f' — {details}' if details else ''}"]
        if point.get("description"):
            morceaux.append(point["description"])
        extrait = injectables.verbatims.get(point["cle"])
        if extrait is not None:
            morceaux.append(
                f"> « {extrait.texte} »\n"
                f"<!-- extrait: {extrait.id_unite} ({extrait.source}) -->"
            )
        irritants.append("\n\n".join(morceaux))

    return _bloc(
        injectables.badges.get(SECTION_CONSOMMATEURS, ""),
        injectables.mentions_partielles.get(SECTION_CONSOMMATEURS, ""),
        _narratif(narratifs, SECTION_CONSOMMATEURS),
        "**Besoins exprimés**" if injectables.tableau_besoins else "",
        injectables.tableau_besoins,
        "**Attentes exprimées**" if injectables.tableau_attentes else "",
        injectables.tableau_attentes,
        "**Principaux points de friction**" if irritants else "",
        "\n\n".join(irritants),
        "**Répartition du sentiment par source**"
        if injectables.tableau_sentiment
        else "",
        injectables.tableau_sentiment,
        "**Écarts entre sources et lecture proposée**"
        if injectables.divergences
        else "",
        _liste(injectables.divergences),
    )


def _section_concurrence(
    injectables: Injectables, narratifs: dict[str, SortieNarratif | None]
) -> str:
    """Rend la section concurrence.

    Args:
        injectables: Données injectables.
        narratifs: Narratifs produits.

    Returns:
        Le corps de la section.
    """
    return _bloc(
        injectables.badges.get(SECTION_CONCURRENCE, ""),
        injectables.mentions_partielles.get(SECTION_CONCURRENCE, ""),
        _narratif(narratifs, SECTION_CONCURRENCE),
        "**Intensité concurrentielle observée**"
        if injectables.tableau_intensite
        else "",
        injectables.tableau_intensite,
        "**Principaux concurrents observés**"
        if injectables.tableau_concurrents
        else "",
        injectables.tableau_concurrents,
        "**Benchmark de prix, par source et par devise**"
        if injectables.tableau_benchmark
        else "",
        injectables.tableau_benchmark,
        "> **Aucune conversion de devise n'a été effectuée.** Deux prix libellés "
        "dans deux devises décrivent deux marchés, pas le même montant."
        if injectables.tableau_benchmark
        else "",
        "**Portée régionale de chaque source**"
        if injectables.portee_regionale
        else "",
        _liste(injectables.portee_regionale),
        "**Standards observés sur le marché**" if injectables.normes_marche else "",
        _liste(injectables.normes_marche),
        "**Angles peu exploités dans le corpus collecté**"
        if injectables.angles_peu_exploites
        else "",
        _liste(injectables.angles_peu_exploites),
    )


def _section_recommandations(injectables: Injectables) -> str:
    """Rend la section recommandations — énoncés recopiés, sans narratif.

    Args:
        injectables: Données injectables.

    Returns:
        Le corps de la section.
    """
    morceaux: list[str] = []
    for priorite in sorted(injectables.tableaux_recommandations):
        tableau_priorite = injectables.tableaux_recommandations[priorite]
        if not tableau_priorite:
            continue
        morceaux.append(f"**{TITRES_PRIORITES.get(priorite, priorite)}**")
        morceaux.append(tableau_priorite)
    if injectables.recommandation_prix:
        morceaux.append("**Positionnement prix proposé**")
        morceaux.append(injectables.recommandation_prix)
    return _bloc(*morceaux)


def _section_opportunites_risques(injectables: Injectables) -> str:
    """Rend la section opportunités et risques.

    Args:
        injectables: Données injectables.

    Returns:
        Le corps de la section.
    """
    return _bloc(
        "**Opportunités identifiées**" if injectables.tableau_opportunites else "",
        injectables.tableau_opportunites,
        "**Risques identifiés**" if injectables.tableau_risques else "",
        injectables.tableau_risques,
    )


def _section_annexe(injectables: Injectables) -> str:
    """Rend l'annexe : sources, méthode, limites et glossaire.

    Args:
        injectables: Données injectables.

    Returns:
        Le corps de la section.
    """
    limites = []
    for famille, elements in injectables.limites_par_famille:
        limites.append(f"**{famille}**")
        limites.append(_liste(elements))
    glossaire = _liste([f"**{terme}** — {definition}" for terme, definition in GLOSSAIRE])
    return _bloc(
        "**Sources et volumes exploités**" if injectables.annexe_sources else "",
        injectables.annexe_sources,
        "**Période couverte**",
        injectables.annexe_periode,
        "**Méthode**",
        # Les points de méthode portent déjà leur numéro : les préfixer d'une
        # puce produirait « - 1. … ».
        "\n".join(METHODOLOGIE),
        "**Limites de l'étude**",
        "Les limites ci-dessous sont reprises telles quelles des analyses amont : "
        "elles ne sont ni réécrites, ni atténuées.",
        _bloc(*limites),
        "**Hypothèses assumées**" if injectables.hypotheses else "",
        _liste(injectables.hypotheses),
        "**Glossaire des indicateurs**",
        glossaire,
    )


def assembler_rapport(
    injectables: Injectables,
    narratifs: dict[str, SortieNarratif | None],
) -> tuple[str, list[SectionProduite]]:
    """Assemble le rapport Markdown complet.

    Args:
        injectables: Données injectables construites par le code.
        narratifs: Narratifs produits par les chaînes de rédaction.

    Returns:
        Le couple `(markdown, sections_produites)`.
    """
    corps: dict[str, str] = {
        SECTION_SYNTHESE: _section_synthese(injectables, narratifs),
        SECTION_VERDICT: _section_verdict(injectables, narratifs),
        SECTION_PLC: _section_plc(injectables, narratifs),
        SECTION_DEMANDE: _section_demande(injectables, narratifs),
        SECTION_CONSOMMATEURS: _section_consommateurs(injectables, narratifs),
        SECTION_CONCURRENCE: _section_concurrence(injectables, narratifs),
        SECTION_RECOMMANDATIONS: _section_recommandations(injectables),
        SECTION_OPPORTUNITES_RISQUES: _section_opportunites_risques(injectables),
        SECTION_ANNEXE: _section_annexe(injectables),
    }
    titres: dict[str, str] = {
        SECTION_VERDICT: _titre_verdict(injectables),
        SECTION_PLC: _titre_plc(injectables),
    }
    entrees_par_section: dict[str, list[str]] = {
        SECTION_SYNTHESE: [ENTREE_RECOMMANDATIONS],
        SECTION_VERDICT: [ENTREE_RECOMMANDATIONS],
        SECTION_PLC: [ENTREE_PLC] if injectables.phase_lisible else [],
        SECTION_DEMANDE: [ENTREE_RECOMMANDATIONS],
        SECTION_CONSOMMATEURS: [ENTREE_INSIGHTS]
        if SECTION_CONSOMMATEURS not in injectables.sections_degradees
        else [ENTREE_RECOMMANDATIONS],
        SECTION_CONCURRENCE: [ENTREE_CONCURRENCE]
        if SECTION_CONCURRENCE not in injectables.sections_degradees
        else [ENTREE_RECOMMANDATIONS],
        SECTION_RECOMMANDATIONS: [ENTREE_RECOMMANDATIONS],
        SECTION_OPPORTUNITES_RISQUES: [ENTREE_RECOMMANDATIONS],
        SECTION_ANNEXE: [ENTREE_RECOMMANDATIONS],
    }

    document: list[str] = [_entete(injectables)]
    sections: list[SectionProduite] = [
        SectionProduite(
            id_section=SECTION_ENTETE,
            titre=injectables.entete.get("produit", ""),
            entrees_utilisees=[ENTREE_RECOMMANDATIONS],
            refs_sources=injectables.refs_par_section.get(SECTION_ENTETE, []),
        )
    ]

    numero = 0
    for gabarit in GABARIT_RAPPORT:
        identifiant = gabarit["id"]
        if identifiant == SECTION_ENTETE:
            continue
        numero += 1
        titre = titres.get(identifiant, gabarit["titre"])
        refs = injectables.refs_par_section.get(identifiant, [])
        document.append(
            f"\n---\n\n## {numero}. {titre}\n\n"
            f"{corps[identifiant]}\n\n{_commentaire_sources(refs)}"
        )
        narratif = narratifs.get(identifiant)
        sections.append(
            SectionProduite(
                id_section=identifiant,
                titre=titre,
                entrees_utilisees=entrees_par_section.get(identifiant, []),
                badge_confiance=injectables.badges.get(identifiant),
                nb_mots_narratif=compter_mots(
                    " ".join(narratif.paragraphes) if narratif else ""
                ),
                degradee=identifiant in injectables.sections_degradees,
                refs_sources=refs,
            )
        )
    return "\n".join(document) + "\n", sections


def assembler_resume(
    injectables: Injectables, narratifs: dict[str, SortieNarratif | None]
) -> str:
    """Assemble le résumé exécutif — une page, autoportante.

    Args:
        injectables: Données injectables.
        narratifs: Narratifs produits.

    Returns:
        Le résumé Markdown.
    """
    sortie = narratifs.get(SECTION_SYNTHESE)
    reserves = list(sortie.puces) if sortie else []
    phase = (
        f"**Phase de cycle de vie du marché** : {injectables.phase_lisible} "
        f"(incertitude {injectables.incertitude_phase}).  "
        if injectables.phase_lisible
        else "**Phase de cycle de vie du marché** : non déterminée.  "
    )
    return (
        _bloc(
            f"# Résumé exécutif — {injectables.entete.get('produit', '')}",
            f"**Marché** : {injectables.entete.get('marche', '')}  \n"
            f"**Date de l'étude** : {injectables.entete.get('date_run', '')}  \n"
            f"**Verdict de potentiel** : {injectables.verdict_lisible} "
            f"(fiabilité du verdict : {injectables.confiance_verdict}).  \n"
            + phase,
            _narratif(narratifs, SECTION_SYNTHESE),
            "**Ce que l'étude établit**" if injectables.faits_cles else "",
            _liste(injectables.faits_cles[:3]),
            "**Ce qu'elle recommande en priorité**"
            if injectables.recommandations_majeures
            else "",
            _liste(injectables.recommandations_majeures[:3]),
            f"**Risque principal** — {injectables.risque_principal}"
            if injectables.risque_principal
            else "",
            "**Réserves majeures**" if reserves else "",
            _liste(reserves),
            "Le rapport complet détaille la grille de verdict, les conditions qui "
            "le feraient changer, le paysage concurrentiel, les besoins exprimés "
            "et les limites de l'étude.",
        )
        + "\n"
    )


VERDICTS_LISIBLES: dict[str, str] = dict(VERDICT_LISIBLE)
"""Réexport pour la post-validation, qui contrôle le titre de la section verdict."""
