"""Point d'entrée en ligne de commande de l'agent de collecte Meta Ads.

Le résultat est écrit en JSON indenté dans un fichier — `output.json` par
défaut, écrasé à chaque exécution — et, sur demande (`--stdout`), sérialisé sur
la sortie standard pour être redirigé ou chaîné. Toute trace de progression part
sur `stderr`, afin que `stdout` reste parsable.

L'écriture du fichier est un confort de la CLI : `agent.rechercher_meta_ads`
retourne un objet en mémoire et n'écrit rien.

Si la région d'étude n'a pas pu être résolue en un pays, rien n'est collecté :
le JSON porte `region_couverte: false`, le motif est rappelé sur `stderr` et la
commande sort avec le code 3.

Exemples :
    python main.py \\
        --nom "JBL Endurance Peak 4 Open Ear" \\
        --description "Écouteurs à conduction ouverte pour le sport." \\
        --categorie "electronics" \\
        --geo MA \\
        --langue fr

    # Le pays accepte aussi du texte libre, et « ALL » pour tous les pays.
    python main.py --nom "..." --description "..." --geo "Casablanca" --langue fr

    # Surveillance directe de deux annonceurs, en plus du plan de recherches.
    python main.py --nom "..." --description "..." --geo FR --langue fr \\
        --annonceur https://www.facebook.com/nike \\
        --annonceur https://www.facebook.com/adidas
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent import rechercher_meta_ads
from config import MAX_ANNONCES_PAR_RECHERCHE, configurer_logging, obtenir_logger
from schemas import FicheProduit, ParametresMarche, ResultatRechercheMetaAds

_LOG = obtenir_logger(__name__)

_INDENTATION_JSON = 2

FICHIER_SORTIE_DEFAUT = "output.json"
"""Fichier écrit à chaque exécution, dans le répertoire courant."""

ENCODAGE_SORTIE = "utf-8"
"""Encodage imposé à l'écriture : sur Windows, le défaut est cp1252 et
corromprait les accents et les émojis des textes d'annonces collectés."""

CODE_SORTIE_REGION_NON_RESOLUE = 3
"""Code de sortie lorsque la région d'étude n'a pas pu être résolue en un pays.

Distinct de 0 (succès), de 1 (erreur d'exécution) et de 2 (erreur d'usage
argparse), pour qu'un orchestrateur amont puisse réagir sans analyser le JSON."""


def _analyser_arguments() -> argparse.Namespace:
    """Déclare et lit les arguments de la ligne de commande.

    Returns:
        Les arguments analysés.
    """
    analyseur = argparse.ArgumentParser(
        description=(
            "Collecte et qualifie un corpus d'annonces de la bibliothèque "
            "publicitaire de Meta (Facebook, Instagram) pour un produit "
            "e-commerce, sur le marché de la région d'étude."
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
        "--geo",
        required=True,
        help=(
            "Pays de diffusion étudié : code ISO-2 (« MA »), lieu en texte libre "
            "(« Maroc », « Casablanca »), ou « ALL » pour tous les pays. "
            "Sélectionne les annonces DIFFUSÉES dans ce pays, quel que soit le "
            "pays de l'annonceur."
        ),
    )
    analyseur.add_argument(
        "--langue", required=True, help="Code langue ISO-2 du marché, ex. fr."
    )
    analyseur.add_argument(
        "--annonceur",
        action="append",
        default=None,
        metavar="URL",
        help=(
            "URL d'une Page Facebook à surveiller directement, en plus du plan "
            "de recherches. Répétable. Un run par URL, sans filtre de pays ni de "
            "statut."
        ),
    )
    analyseur.add_argument(
        "--annonces",
        type=int,
        default=MAX_ANNONCES_PAR_RECHERCHE,
        help=(
            f"Plafond d'annonces par recherche (défaut : "
            f"{MAX_ANNONCES_PAR_RECHERCHE}). L'actor étant facturé À L'ANNONCE, "
            "c'est le principal levier de coût."
        ),
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


def _ecrire_resultat(resultat: ResultatRechercheMetaAds, chemin: str) -> None:
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
    pays = resultat.pays.code_pays if resultat.pays else "aucun pays"
    print(
        f"Résultat écrit dans {fichier.resolve()} "
        f"({len(resultat.annonces)} annonce(s) sur {pays}, "
        f"{resultat.stats.nb_annonceurs} annonceur(s), "
        f"{round(fichier.stat().st_size / 1024)} Ko).",
        file=sys.stderr,
    )


def main() -> None:
    """Exécute la collecte, écrit le fichier de sortie et, sur demande, stdout."""
    arguments = _analyser_arguments()
    configurer_logging(verbose=arguments.verbose)

    produit = FicheProduit(
        nom=arguments.nom,
        description=arguments.description,
        categorie=arguments.categorie,
    )
    # `geo` n'est pas forcé en majuscules : il accepte aussi un lieu en texte
    # libre, que `strategy.resoudre_pays` résout en code pays.
    marche = ParametresMarche(
        geo=arguments.geo.strip(),
        langue=arguments.langue.strip().lower(),
    )

    resultat = rechercher_meta_ads(
        produit,
        marche,
        urls_annonceurs=arguments.annonceur,
        max_annonces_par_recherche=max(1, arguments.annonces),
    )

    if arguments.sortie:
        _ecrire_resultat(resultat, arguments.sortie)
    if arguments.stdout:
        print(resultat.model_dump_json(indent=_INDENTATION_JSON))

    if not resultat.region_couverte:
        # Le motif exact est déjà dans le JSON ; il est répété sur stderr pour
        # que l'opérateur comprenne sans ouvrir le fichier.
        print(
            f"\nRégion « {marche.geo} » non résolue.\n"
            f"{resultat.statuts_collecte[0].message_erreur}\n"
            "Aucun run Apify n'a été lancé.",
            file=sys.stderr,
        )
        sys.exit(CODE_SORTIE_REGION_NON_RESOLUE)


if __name__ == "__main__":
    main()
