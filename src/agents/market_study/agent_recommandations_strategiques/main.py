"""Point d'entrée en ligne de commande de l'agent Recommandations Stratégiques.

`stdout` reste du JSON pur. Codes de sortie : 0 succès — **y compris pour un
verdict négatif ou indéterminé, qui sont des résultats d'analyse et non des
erreurs** —, 1 erreur imprévue, 2 entrée inexploitable ou incohérence produit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from langchain_core.callbacks import get_usage_metadata_callback

from agent import recommander
from config import (
    CODE_ENTREE_INEXPLOITABLE,
    CODE_ERREUR_IMPREVUE,
    CODE_SUCCES,
    configurer_logs,
    resumer_consommation,
)
from schemas import ErreurCoherenceProduit


def construire_analyseur() -> argparse.ArgumentParser:
    """Construit l'analyseur d'arguments de la commande.

    Returns:
        L'analyseur configuré.
    """
    analyseur = argparse.ArgumentParser(
        prog="agent_recommandations_strategiques",
        description=(
            "Axe 3 — Recommandations stratégiques et verdict de potentiel. Consomme "
            "les sorties d'analyse F3 et F4 et celle du collecteur Tendances ; ne "
            "collecte rien et ne relit aucune donnée brute de collecteur."
        ),
        epilog=(
            "Note : le collecteur Tendances émet sur stdout — le fichier passé à "
            "--tendances est une redirection. F3 et F4 écrivent nativement "
            "output.json. Un verdict défavorable sort en code 0."
        ),
    )
    analyseur.add_argument(
        "--insights", default=None, help="Sortie de agent_insights_consommateurs (F3)."
    )
    analyseur.add_argument(
        "--concurrence", default=None, help="Sortie de agent_analyse_concurrentielle (F4)."
    )
    analyseur.add_argument(
        "--tendances", default=None, help="Sortie du collecteur Tendances."
    )
    analyseur.add_argument(
        "--langue-analyse", default="fr", help="Langue de rédaction (défaut : fr)."
    )
    analyseur.add_argument(
        "--sortie",
        default="output.json",
        help="Fichier de sortie ; chaîne vide pour n'écrire aucun fichier.",
    )
    analyseur.add_argument("--stdout", action="store_true", help="Émet aussi le JSON sur stdout.")
    analyseur.add_argument("--verbose", action="store_true", help="Journalisation détaillée.")
    return analyseur


def main(argv: list[str] | None = None) -> int:
    """Exécute l'agent depuis la ligne de commande.

    Args:
        argv: Arguments, ou `None` pour utiliser `sys.argv`.

    Returns:
        Le code de sortie du processus.
    """
    arguments = construire_analyseur().parse_args(argv)
    logger = configurer_logs(arguments.verbose)

    if not any((arguments.insights, arguments.concurrence, arguments.tendances)):
        print(
            "Aucun fichier d'entrée fourni. Renseigne au moins l'une des options "
            "--insights, --concurrence ou --tendances.",
            file=sys.stderr,
        )
        return CODE_ENTREE_INEXPLOITABLE

    try:
        with get_usage_metadata_callback() as consommation:
            resultat = recommander(
                chemin_insights=arguments.insights,
                chemin_concurrence=arguments.concurrence,
                chemin_tendances=arguments.tendances,
                langue_analyse=arguments.langue_analyse,
            )
        recapitulatif = resumer_consommation(consommation.usage_metadata)
        if recapitulatif:
            print(f"Consommation LLM — {recapitulatif}", file=sys.stderr)
    except ErreurCoherenceProduit as erreur:
        print(f"Incohérence bloquante : {erreur}", file=sys.stderr)
        return CODE_ENTREE_INEXPLOITABLE
    except Exception as erreur:  # noqa: BLE001 — le CLI n'affiche jamais de trace nue
        logger.exception("erreur imprévue")
        print(f"Erreur imprévue : {type(erreur).__name__} : {erreur}", file=sys.stderr)
        return CODE_ERREUR_IMPREVUE

    if all(not source.donnees_disponibles for source in resultat.sources_utilisees):
        print(
            "Aucun fichier d'entrée exploitable : "
            + " ; ".join(
                f"[{s.source}] {', '.join(s.avertissements) or 'non fourni'}"
                for s in resultat.sources_utilisees
            ),
            file=sys.stderr,
        )
        return CODE_ENTREE_INEXPLOITABLE

    document = resultat.model_dump_json(indent=2, by_alias=True)

    if arguments.sortie:
        chemin = Path(arguments.sortie)
        chemin.write_text(document, encoding="utf-8")
        logger.info("résultat écrit dans %s", chemin)

    if arguments.stdout or not arguments.sortie:
        print(document)

    # Un verdict défavorable est un résultat d'analyse, jamais une erreur : le
    # code de sortie reste 0 pour que l'appelant lise le JSON, pas le code.
    print(
        f"Verdict : {resultat.verdict_potentiel.verdict} "
        f"(score {resultat.verdict_potentiel.score_total}, "
        f"declenche_plc={resultat.verdict_potentiel.declenche_plc}, "
        f"confiance {resultat.verdict_potentiel.confiance})",
        file=sys.stderr,
    )
    return CODE_SUCCES


if __name__ == "__main__":
    raise SystemExit(main())
