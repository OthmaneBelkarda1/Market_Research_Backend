"""Point d'entrée en ligne de commande de l'agent de restitution.

`stdout` reste du **JSON pur** : ce sont les métadonnées et les contrôles. Les
documents, eux, ne sortent que dans leurs fichiers Markdown dédiés.

Codes de sortie : 0 succès, 1 erreur imprévue, 2 entrée inexploitable ou
incohérence produit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from langchain_core.callbacks import get_usage_metadata_callback

from agent import restituer
from config import (
    CHEMIN_RAPPORT_DEFAUT,
    CHEMIN_RESUME_DEFAUT,
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
        prog="agent_restitution",
        description=(
            "Dernier maillon du pipeline : transforme les sorties d'analyse en un "
            "rapport d'étude de marché Markdown et son résumé exécutif. Ne collecte "
            "rien, n'analyse rien — il met en forme des analyses existantes."
        ),
        epilog=(
            "La sortie d'analyse de synthèse est requise ; les trois autres sont "
            "optionnelles. Une analyse manquante produit une section dégradée AVEC "
            "mention explicite, jamais une section silencieusement vide."
        ),
    )
    analyseur.add_argument(
        "--recommandations",
        required=True,
        help="Sortie de agent_recommandations_strategiques (F5) — requise.",
    )
    analyseur.add_argument(
        "--insights", default=None, help="Sortie de agent_insights_consommateurs (F3)."
    )
    analyseur.add_argument(
        "--concurrence",
        default=None,
        help="Sortie de agent_analyse_concurrentielle (F4).",
    )
    analyseur.add_argument("--plc", default=None, help="Sortie de agent_plc (F6).")
    analyseur.add_argument(
        "--rapport",
        default=CHEMIN_RAPPORT_DEFAUT,
        help="Fichier du rapport complet ; chaîne vide pour ne pas l'écrire.",
    )
    analyseur.add_argument(
        "--resume",
        default=CHEMIN_RESUME_DEFAUT,
        help="Fichier du résumé exécutif ; chaîne vide pour ne pas l'écrire.",
    )
    analyseur.add_argument(
        "--langue-analyse", default="fr", help="Langue de rédaction (défaut : fr)."
    )
    analyseur.add_argument(
        "--sortie",
        default="output.json",
        help="Fichier de métadonnées ; chaîne vide pour n'écrire aucun fichier.",
    )
    analyseur.add_argument(
        "--stdout", action="store_true", help="Émet aussi le JSON sur stdout."
    )
    analyseur.add_argument(
        "--verbose", action="store_true", help="Journalisation détaillée."
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

    try:
        with get_usage_metadata_callback() as consommation:
            resultat = restituer(
                chemin_recommandations=arguments.recommandations,
                chemin_insights=arguments.insights,
                chemin_concurrence=arguments.concurrence,
                chemin_plc=arguments.plc,
                chemin_rapport=arguments.rapport,
                chemin_resume=arguments.resume,
                langue_analyse=arguments.langue_analyse,
            )
        recapitulatif = resumer_consommation(consommation.usage_metadata)
        if recapitulatif:
            print(f"Consommation LLM — {recapitulatif}", file=sys.stderr)
    except ErreurCoherenceProduit as erreur:
        print(f"Incohérence bloquante : {erreur}", file=sys.stderr)
        return CODE_ENTREE_INEXPLOITABLE
    except ValueError as erreur:
        print(f"Entrée inexploitable : {erreur}", file=sys.stderr)
        return CODE_ENTREE_INEXPLOITABLE
    except Exception as erreur:  # noqa: BLE001 — le CLI n'affiche jamais de trace nue
        logger.exception("erreur imprévue")
        print(f"Erreur imprévue : {type(erreur).__name__} : {erreur}", file=sys.stderr)
        return CODE_ERREUR_IMPREVUE

    document = resultat.model_dump_json(indent=2, by_alias=True)

    if arguments.sortie:
        chemin = Path(arguments.sortie)
        chemin.write_text(document, encoding="utf-8")
        logger.info("métadonnées écrites dans %s", chemin)

    if arguments.stdout or not arguments.sortie:
        print(document)

    controles = resultat.controles
    print(
        f"Rapport : {resultat.chemin_rapport or 'non écrit'} | "
        f"résumé : {resultat.chemin_resume or 'non écrit'} | "
        f"nombres vérifiés : {controles.nb_nombres_verifies} | "
        f"retirés : {controles.nb_nombres_retires} | "
        f"verdict conforme : {controles.verdict_conforme} | "
        f"sections partielles : "
        f"{', '.join(controles.mentions_etude_partielle) or 'aucune'}",
        file=sys.stderr,
    )
    return CODE_SUCCES


if __name__ == "__main__":
    raise SystemExit(main())
