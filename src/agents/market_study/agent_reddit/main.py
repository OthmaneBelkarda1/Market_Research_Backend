"""Point d'entrée en ligne de commande de l'agent de collecte Reddit.

Le résultat est sérialisé en JSON indenté sur `stdout` ; toute trace de
progression part sur `stderr`, afin que la sortie standard reste parsable.

Exemple :
    python main.py \\
        --nom "JBL Endurance Peak 4 Open Ear" \\
        --description "Écouteurs à conduction ouverte pour le sport." \\
        --categorie "electronics" \\
        --geo FR \\
        --langue fr
"""

from __future__ import annotations

import argparse

from agent import collecter_reddit
from config import configurer_logging
from schemas import FicheProduit, ParametresMarche

_INDENTATION_JSON = 2


def _analyser_arguments() -> argparse.Namespace:
    """Déclare et lit les arguments de la ligne de commande.

    Returns:
        Les arguments analysés.
    """
    analyseur = argparse.ArgumentParser(
        description=(
            "Collecte et qualifie un corpus de discussions Reddit pour un produit "
            "e-commerce, sur un marché donné."
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
        "--verbose",
        action="store_true",
        help="Affiche la progression de la collecte sur stderr.",
    )
    return analyseur.parse_args()


def main() -> None:
    """Exécute la collecte et écrit le résultat JSON sur la sortie standard."""
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

    resultat = collecter_reddit(produit, marche)
    print(resultat.model_dump_json(indent=_INDENTATION_JSON))


if __name__ == "__main__":
    main()
