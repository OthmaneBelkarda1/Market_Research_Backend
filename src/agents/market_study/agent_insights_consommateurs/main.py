"""Point d'entrée en ligne de commande de l'agent Insights Consommateurs.

`stdout` reste du JSON pur : toute progression et toute erreur partent sur
`stderr`. Codes de sortie : 0 succès, 1 erreur imprévue, 2 entrée inexploitable
ou incohérence de produit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from langchain_core.callbacks import get_usage_metadata_callback

from agent import analyser_insights
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
        prog="agent_insights_consommateurs",
        description=(
            "Analyse de l'axe 1 — Insights Consommateurs. Consomme les sorties "
            "JSON des collecteurs Reddit, Amazon et Recherche web ; ne collecte "
            "rien lui-même."
        ),
        epilog=(
            "Note : le collecteur Reddit émet sur stdout — le fichier passé à "
            "--reddit est une redirection (python main.py ... > sortie.json). "
            "Amazon et Recherche web écrivent nativement output.json."
        ),
    )
    analyseur.add_argument("--reddit", default=None, help="Sortie JSON de agent_reddit.")
    analyseur.add_argument("--amazon", default=None, help="Sortie JSON de agent_amazon.")
    analyseur.add_argument(
        "--recherche-web", default=None, help="Sortie JSON de agent_recherche_web."
    )
    analyseur.add_argument(
        "--langue-analyse",
        default="fr",
        help="Code langue de rédaction de l'analyse (défaut : fr).",
    )
    analyseur.add_argument(
        "--sortie",
        default="output.json",
        help="Fichier de sortie ; chaîne vide pour n'écrire aucun fichier.",
    )
    analyseur.add_argument(
        "--stdout",
        action="store_true",
        help="Émet aussi le JSON sur la sortie standard.",
    )
    analyseur.add_argument(
        "--verbose", action="store_true", help="Journalisation détaillée sur stderr."
    )
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

    if not any((arguments.reddit, arguments.amazon, arguments.recherche_web)):
        print(
            "Aucun fichier d'entrée fourni. Renseigne au moins l'une des options "
            "--reddit, --amazon ou --recherche-web.",
            file=sys.stderr,
        )
        return CODE_ENTREE_INEXPLOITABLE

    try:
        with get_usage_metadata_callback() as consommation:
            resultat = analyser_insights(
                chemin_reddit=arguments.reddit,
                chemin_amazon=arguments.amazon,
                chemin_web=arguments.recherche_web,
                langue_analyse=arguments.langue_analyse,
            )
        recapitulatif = resumer_consommation(consommation.usage_metadata)
        if recapitulatif:
            print(f"Consommation LLM — {recapitulatif}", file=sys.stderr)
    except ErreurCoherenceProduit as erreur:
        print(f"Incohérence bloquante : {erreur}", file=sys.stderr)
        return CODE_ENTREE_INEXPLOITABLE
    except Exception as erreur:  # noqa: BLE001 — le CLI ne doit jamais afficher de trace nue
        logger.exception("erreur imprévue")
        print(f"Erreur imprévue : {type(erreur).__name__} : {erreur}", file=sys.stderr)
        return CODE_ERREUR_IMPREVUE

    if not resultat.sources_utilisees or all(
        not source.donnees_disponibles for source in resultat.sources_utilisees
    ):
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

    return CODE_SUCCES


if __name__ == "__main__":
    raise SystemExit(main())
