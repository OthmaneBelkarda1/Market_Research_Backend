"""Point d'entrée en ligne de commande de l'agent de recherche web régionalisée.

Le résultat est écrit en JSON indenté dans un fichier — `output.json` par
défaut, écrasé à chaque exécution — et, sur demande (`--stdout`), sérialisé sur
la sortie standard pour être redirigé ou chaîné. Toute trace de progression part
sur `stderr`, afin que `stdout` reste parsable.

L'écriture du fichier est un confort de la CLI : `agent.rechercher_web` retourne
un objet en mémoire et n'écrit rien.

Exemple :
    python main.py \\
        --nom "JBL Endurance Peak 4 Open Ear" \\
        --description "Écouteurs à conduction ouverte pour le sport." \\
        --categorie "electronics" \\
        --geo MA \\
        --langue fr
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from langchain_core.callbacks import get_usage_metadata_callback

from agent import rechercher_web
from config import configurer_logging, obtenir_logger, resumer_consommation
from schemas import FicheProduit, ParametresMarche, ResultatRechercheWeb

_LOG = obtenir_logger(__name__)

_INDENTATION_JSON = 2

FICHIER_SORTIE_DEFAUT = "output.json"
"""Fichier écrit à chaque exécution, dans le répertoire courant."""

ENCODAGE_SORTIE = "utf-8"
"""Encodage imposé à l'écriture : sur Windows, le défaut est cp1252 et
corromprait tous les accents du corpus collecté."""


def _analyser_arguments() -> argparse.Namespace:
    """Déclare et lit les arguments de la ligne de commande.

    Returns:
        Les arguments analysés.
    """
    analyseur = argparse.ArgumentParser(
        description=(
            "Collecte et qualifie un corpus de pages web pour un produit "
            "e-commerce, sur une région d'étude donnée, selon deux axes : "
            "consommateurs et concurrence."
        )
    )
    analyseur.add_argument("--nom", required=True, help="Titre commercial du produit.")
    analyseur.add_argument(
        "--description", required=True, help="Description libre du produit."
    )
    analyseur.add_argument(
        "--categorie", default=None, help="Catégorie e-commerce (optionnel)."
    )
    analyseur.add_argument(
        "--geo", required=True, help="Code pays ISO-2 de la région d'étude, ex. FR."
    )
    analyseur.add_argument(
        "--langue", required=True, help="Code langue ISO-2 du marché, ex. fr."
    )
    analyseur.add_argument(
        "--sortie",
        default=FICHIER_SORTIE_DEFAUT,
        help=(
            f"Fichier JSON de sortie (défaut : {FICHIER_SORTIE_DEFAUT}, écrasé à "
            "chaque exécution). Passer une chaîne vide pour n'écrire aucun fichier."
        ),
    )
    analyseur.add_argument(
        "--stdout",
        action="store_true",
        help="Affiche aussi le JSON sur la sortie standard, pour le rediriger.",
    )
    analyseur.add_argument(
        "--verbose",
        action="store_true",
        help="Affiche la progression de la collecte sur stderr.",
    )
    return analyseur.parse_args()


def _ecrire_resultat(resultat: ResultatRechercheWeb, chemin: str) -> None:
    """Écrit le résultat en JSON indenté dans un fichier.

    L'échec d'écriture n'interrompt pas la CLI : la collecte a déjà eu lieu et
    son coût est engagé. L'erreur est signalée sur `stderr`, et `--stdout` reste
    le moyen de récupérer le corpus.

    Args:
        resultat: Résultat de la collecte.
        chemin: Chemin du fichier à écrire, relatif au répertoire courant.
    """
    fichier = Path(chemin)
    try:
        fichier.write_text(
            resultat.model_dump_json(indent=_INDENTATION_JSON), encoding=ENCODAGE_SORTIE
        )
    except OSError as exception:
        _LOG.error("Écriture de « %s » impossible : %s", fichier, exception)
        return

    # Confirmation sur stderr, et non via le logger : elle doit rester visible
    # sans `--verbose`, sans pour autant être émise au niveau WARNING.
    print(
        f"Résultat écrit dans {fichier.resolve()} "
        f"({len(resultat.pages)} page(s), {round(fichier.stat().st_size / 1024)} Ko).",
        file=sys.stderr,
    )


def main() -> None:
    """Exécute la recherche, écrit le fichier de sortie et, sur demande, stdout."""
    arguments = _analyser_arguments()
    configurer_logging(verbose=arguments.verbose)

    produit = FicheProduit(
        nom=arguments.nom,
        description=arguments.description,
        categorie=arguments.categorie,
    )
    marche = ParametresMarche(
        geo=arguments.geo.strip().upper(),
        langue=arguments.langue.strip().lower(),
    )

    with get_usage_metadata_callback() as consommation:
        resultat = rechercher_web(produit, marche)
    recapitulatif = resumer_consommation(consommation.usage_metadata)
    if recapitulatif:
        print(f"Consommation LLM — {recapitulatif}", file=sys.stderr)

    if arguments.sortie:
        _ecrire_resultat(resultat, arguments.sortie)
    if arguments.stdout:
        print(resultat.model_dump_json(indent=_INDENTATION_JSON))


if __name__ == "__main__":
    main()
