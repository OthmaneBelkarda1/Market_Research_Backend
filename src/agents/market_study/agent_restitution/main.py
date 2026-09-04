"""Point d'entrée en ligne de commande de l'agent de restitution.

`stdout` reste du **JSON pur** : ce sont les métadonnées et les contrôles. Les
documents, eux, ne sortent que dans leurs fichiers Markdown dédiés.

Codes de sortie : 0 succès, 1 erreur imprévue, 2 entrée inexploitable ou
incohérence produit, 4 rédaction impossible.

Le 4 est le seul qui laisse le disque intact : aucun `.md` n'est écrit, et le
`ResultatRestitution` part sur stderr avec ses `statuts_analyse` complets — c'est
là que se lit la cause. Il vaut pour le gabarit v2 seul ; le v1 dégrade et sort
en 0, son contrat est inchangé. Voir le README pour la table complète et pour la
raison du 4 plutôt que du 2.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from langchain_core.callbacks import get_usage_metadata_callback

from config import (
    GABARITS_DISPONIBLES,
    GABARIT_PAR_DEFAUT,
    CHEMIN_RAPPORT_DEFAUT,
    CHEMIN_RESUME_DEFAUT,
    CODE_ENTREE_INEXPLOITABLE,
    CODE_ERREUR_IMPREVUE,
    CODE_REDACTION_IMPOSSIBLE,
    CODE_SUCCES,
    ConfigurationRedactionInvalide,
    RedactionImpossible,
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
        "--gabarit",
        choices=GABARITS_DISPONIBLES,
        default=GABARIT_PAR_DEFAUT,
        help=(
            "Gabarit du rapport : « v2 » (défaut), rapport décisionnel en cinq "
            "écrans ; « v1 », ancien rendu en neuf sections, conservé le temps de "
            "la transition."
        ),
    )
    analyseur.add_argument(
        "--sources-etat",
        default=None,
        help=(
            "JSON de l'état des collecteurs, produit par l'orchestrateur : "
            "{source: {donnees_disponibles, nb_items, raison}}. Il porte la RAISON "
            "d'une collecte vide, que les sorties d'analyse ne conservent pas — "
            "sans lui, la ligne « Sources analysées » dit qu'une source n'a rien "
            "rendu, mais pas pourquoi."
        ),
    )
    analyseur.add_argument(
        "--stdout", action="store_true", help="Émet aussi le JSON sur stdout."
    )
    analyseur.add_argument(
        "--verbose", action="store_true", help="Journalisation détaillée."
    )
    return analyseur


def _lire_etat_sources(chemin: str | None) -> dict[str, dict[str, Any]] | None:
    """Lit le fichier d'état des collecteurs, s'il est fourni.

    Args:
        chemin: Chemin du JSON, ou `None`.

    Returns:
        L'état par source, ou `None` si l'argument est absent.

    Raises:
        OSError: Si le fichier est illisible.
        json.JSONDecodeError: Si son contenu n'est pas du JSON.
    """
    if not chemin:
        return None
    contenu = json.loads(Path(chemin).read_text(encoding="utf-8"))
    return contenu if isinstance(contenu, dict) else None


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
        # Import tardif, et volontairement : `redaction_v2` vérifie ses gabarits de
        # prompt au chargement, et cette vérification peut échouer. Importée en tête
        # de module, elle sortirait en trace nue avant même que le CLI existe ; ici,
        # elle est rapportée comme ce qu'elle est — une rédaction impossible.
        from agent import restituer
    except ConfigurationRedactionInvalide as erreur:
        logger.error("configuration de rédaction invalide : %s", erreur)
        print(
            f"Configuration de rédaction invalide : {erreur}\n"
            "Aucun appel au modèle n'a été fait et aucun fichier n'a été écrit : "
            "c'est un défaut de gabarit, à corriger dans le code.",
            file=sys.stderr,
        )
        return CODE_REDACTION_IMPOSSIBLE

    try:
        etat_sources = _lire_etat_sources(arguments.sources_etat)
    except (OSError, json.JSONDecodeError) as erreur:
        print(f"--sources-etat illisible : {erreur}", file=sys.stderr)
        return CODE_ENTREE_INEXPLOITABLE

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
                gabarit=arguments.gabarit,
                etat_sources=etat_sources,
            )
        recapitulatif = resumer_consommation(consommation.usage_metadata)
        if recapitulatif:
            print(f"Consommation LLM — {recapitulatif}", file=sys.stderr)
    except RedactionImpossible as erreur:
        # Aucun `.md` n'a été écrit : `restituer` lève avant l'écriture. Le
        # diagnostic part sur stderr — c'est tout ce que l'exploitant aura,
        # puisqu'il n'y a pas de fichier de sortie à relire. `statuts_analyse`
        # y va en entier : c'est là que se lit QUELLE chaîne a échoué, après
        # combien de tentatives et sur quel motif.
        logger.error("rédaction impossible : %s", erreur)
        print(f"Rédaction impossible : {erreur}", file=sys.stderr)
        diagnostic = {
            "code": "REDACTION_IMPOSSIBLE",
            "message": str(erreur),
            "statuts_analyse": [
                statut.model_dump(by_alias=True)
                for statut in getattr(erreur, "statuts", [])
            ],
        }
        print(json.dumps(diagnostic, ensure_ascii=False, indent=2), file=sys.stderr)
        return CODE_REDACTION_IMPOSSIBLE
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
