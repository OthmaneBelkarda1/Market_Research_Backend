"""Constantes, seuils et plomberie LLM de l'agent Analyse Concurrentielle.

Aucune valeur magique ne doit exister ailleurs que dans ce module. Les seuils
sont des **heuristiques non validées empiriquement**, commentées une à une.

Ce module ne dépend d'aucun autre module interne.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from dotenv import load_dotenv

# --------------------------------------------------------------------------- #
# Encodage — correctif obligatoire
# --------------------------------------------------------------------------- #
for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

load_dotenv()

FORMAT_LOG: str = "%(asctime)s [%(levelname)s] %(name)s : %(message)s"
NOM_LOGGER: str = "agent_analyse_concurrentielle"


def configurer_logs(verbeux: bool) -> logging.Logger:
    """Configure la journalisation vers `stderr`.

    Args:
        verbeux: Si vrai, niveau DEBUG ; sinon WARNING.

    Returns:
        Le logger du module.
    """
    gestionnaire = logging.StreamHandler(stream=sys.stderr)
    gestionnaire.setFormatter(logging.Formatter(FORMAT_LOG))
    logger_local = logging.getLogger(NOM_LOGGER)
    logger_local.handlers.clear()
    logger_local.addHandler(gestionnaire)
    logger_local.setLevel(logging.DEBUG if verbeux else logging.WARNING)
    logger_local.propagate = False
    return logger_local


logger = logging.getLogger(NOM_LOGGER)

# --------------------------------------------------------------------------- #
# Modèles LLM — deux niveaux, température nulle
# --------------------------------------------------------------------------- #
# Identifiants vérifiés le 05/08/2026 : tous deux disponibles à l'API Anthropic.
# `claude-sonnet-5` remplace `claude-sonnet-4-5-20250929` (02/09/2026) : même
# famille, génération postérieure, et un tarif inférieur d'un tiers dans les deux
# sens (2/10 $ contre 3/15 $ par million de jetons). Il refuse `temperature` ;
# `construire_modele` ne la transmet donc qu'aux modèles qui l'acceptent.

MODELE_EXTRACTION: str = "claude-haiku-4-5-20251001"
"""Extraction d'attributs de titres et de claims d'annonces, par lots."""

MODELE_SYNTHESE: str = "claude-sonnet-5"
"""Consolidation des concurrents, analyse qualitative, différenciation."""

TEMPERATURE: float = 0.0

MODELES_SANS_ECHANTILLONNAGE: frozenset[str] = frozenset(
    {"claude-sonnet-5", "claude-opus-5"}
)
"""Modèles de génération courante : ils refusent tout paramètre d'échantillonnage.

`temperature`, `top_p` et `top_k` y renvoient une erreur 400. Le déterminisme que
`TEMPERATURE = 0` visait n'était de toute façon pas atteint : deux exécutions du
même code sur les mêmes entrées produisent déjà des sorties différentes
(cf. `docs/baseline_jetons.md` §7). `TEMPERATURE` reste transmise aux modèles qui
l'acceptent encore, Haiku 4.5 en particulier.
"""

RAISONNEMENT_SYNTHESE: dict[str, str] = {"type": "disabled"}
"""Raisonnement adaptatif de la génération courante, désactivé par défaut.

Désactivé, le modèle se comporte comme Sonnet 4.5 — aucun jeton de raisonnement
facturé — donc la baisse de tarif 3/15 → 2/10 $ par million de jetons est acquise
sans contrepartie sur la sortie. Passer à `{"type": "adaptive"}`, éventuellement
avec `output_config={"effort": "low"}`, est le seul arbitrage qualité/coût de ce
module : il demande une campagne de mesure avant d'être adopté.
"""

MAX_TOKENS_EXTRACTION: int = 8000
MAX_TOKENS_SYNTHESE: int = 16000

NOM_VARIABLE_CLE_API: str = "ANTHROPIC_API_KEY"

TARIFS_USD_PAR_MTOK: dict[str, tuple[float, float]] = {
    MODELE_EXTRACTION: (1.00, 5.00),
    MODELE_SYNTHESE: (2.00, 10.00),
}
"""Tarifs publics (entrée, sortie) par million de jetons, pour estimation seule.

Valeurs saisies à la main, non interrogées en ligne : à vérifier avant tout
usage budgétaire.
"""

# --------------------------------------------------------------------------- #
# Seuils de construction du référentiel
# --------------------------------------------------------------------------- #

SEUIL_PERTINENCE_AMONT: float = 0.5
"""Offres, annonces et pages sous ce score amont sont écartées. `None` accepté."""

# Valeurs de `correspondance` **constatées sur les sorties réelles** des
# collecteurs. Elles diffèrent des libellés courts employés dans la note de
# cadrage (« equivalent », « concurrent », « categorie ») : ce sont les
# constantes réellement émises par `agent_amazon` et `agent_meta_ads`.
CORRESPONDANCE_AMAZON_EQUIVALENT: str = "produit_equivalent"
CORRESPONDANCE_AMAZON_VARIANTE: str = "variante"
CORRESPONDANCE_META_CONCURRENT: str = "concurrent_direct"
CORRESPONDANCE_META_CATEGORIE: str = "categorie_proche"
CORRESPONDANCE_ACCESSOIRE: str = "accessoire"
CORRESPONDANCE_HORS_SUJET: str = "hors_sujet"

CORRESPONDANCES_COEUR_AMAZON: frozenset[str] = frozenset(
    {CORRESPONDANCE_AMAZON_EQUIVALENT, CORRESPONDANCE_AMAZON_VARIANTE}
)
CORRESPONDANCES_COEUR_META: frozenset[str] = frozenset(
    {CORRESPONDANCE_META_CONCURRENT, CORRESPONDANCE_META_CATEGORIE}
)
"""Cœur du benchmark. `accessoire` est conservé à part comme signal
d'écosystème ; `hors_sujet` est exclu.
"""

TOP_N_CONCURRENTS_ANALYSES: int = 8
"""Concurrents bénéficiant de l'analyse qualitative complète (un appel chacun)."""

TAILLE_LOT_ATTRIBUTS: int = 12
TAILLE_LOT_CLAIMS: int = 10

MAX_AVIS_PREUVE_PAR_OFFRE: int = 10
MAX_CARACTERES_EXTRAIT: int = 300
MAX_CARACTERES_TEXTE_ANNONCE: int = 600
"""Troncature du texte d'annonce soumis à l'extraction de claims."""

MAX_CARACTERES_EXTRAIT_PAGE: int = 1500
"""Début de markdown conservé pour une page web du référentiel."""

MAX_PAGES_REFERENTIEL: int = 25

SEUIL_MIN_OFFRES_FIABLE: int = 5
"""En deçà, `confiance_globale` est plafonnée à « faible »."""

PART_TOP3_CONCENTRATION: int = 3
"""La concentration publiée est la part de volume des N premiers concurrents."""

MULTIPLICATEURS_VOLUME: dict[str, int] = {"K": 1_000, "M": 1_000_000}
"""Suffixes de magnitude rencontrés dans les mentions de volume Amazon.

Amazon publie « 1K+ bought in past month », soit un **plancher de fourchette**
et non un volume exact. La valeur retenue est ce plancher : tout cumul de
volumes est donc une borne inférieure, jamais une estimation.
"""

MIN_PRIX_POUR_SEGMENTS: int = 4
"""En deçà de 4 prix, aucun tercile n'est calculé : seule une fourchette est publiée."""

NB_TENTATIVES_LLM: int = 2

# --------------------------------------------------------------------------- #
# Devises — normalisation d'étiquette, jamais de conversion
# --------------------------------------------------------------------------- #
# `agent_amazon` publie un SYMBOLE (« € ») là où `agent_aliexpress` publie un
# code ISO (« EUR »). Sans table de correspondance, deux benchmarks de la même
# devise seraient étiquetés différemment et `--devise-envisagee EUR` ne
# trouverait jamais le benchmark Amazon. Il ne s'agit PAS d'une conversion :
# aucun montant n'est modifié, seule l'étiquette est normalisée.
SYMBOLES_VERS_ISO: dict[str, str] = {
    "€": "EUR",
    "$": "USD",
    "US$": "USD",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "AED": "AED",
    "MAD": "MAD",
    "DH": "MAD",
    "د.م.": "MAD",
    "zł": "PLN",
    "kr": "SEK",
    "R$": "BRL",
    "TL": "TRY",
    "₺": "TRY",
}
"""Symbole monétaire → code ISO-4217. Toute devise absente est conservée telle
quelle, sans supposition."""

# --------------------------------------------------------------------------- #
# Vocabulaire
# --------------------------------------------------------------------------- #

SOURCE_ALIEXPRESS: str = "aliexpress"
SOURCE_AMAZON: str = "amazon"
SOURCE_META_ADS: str = "meta_ads"
SOURCE_WEB: str = "recherche_web"
SOURCES: tuple[str, ...] = (SOURCE_ALIEXPRESS, SOURCE_AMAZON, SOURCE_META_ADS, SOURCE_WEB)

AXE_CONCURRENCE: str = "axe2"
"""Valeur attendue dans `pages[].axes_servis` pour qu'une page nous concerne."""

TYPE_MARQUE_ETABLIE: str = "marque_etablie"
TYPE_MARQUE_MARKETPLACE: str = "marque_marketplace"
TYPE_ANNONCEUR_SEUL: str = "annonceur_seul"
TYPE_SANS_MARQUE: str = "offres_sans_marque"

CERTITUDE_SURE: str = "sur"
CERTITUDE_PROBABLE: str = "probable"

SEGMENT_ENTREE: str = "entree"
SEGMENT_COEUR: str = "coeur"
SEGMENT_PREMIUM: str = "premium"
SEGMENTS: tuple[str, ...] = (SEGMENT_ENTREE, SEGMENT_COEUR, SEGMENT_PREMIUM)

STATUT_FAIT: str = "fait"
STATUT_HYPOTHESE: str = "hypothese"

MENACE_FORTE: str = "fort"
MENACE_MOYENNE: str = "moyen"
MENACE_FAIBLE: str = "faible"

CONFIANCE_ELEVEE: str = "elevee"
CONFIANCE_MOYENNE: str = "moyenne"
CONFIANCE_FAIBLE: str = "faible"

PORTEE_REGION_ETUDE: str = "region_etude"
PORTEE_MARKETPLACE_PAYS: str = "marketplace_pays"
PORTEE_DIFFUSION_PAYS: str = "diffusion_pays"
PORTEE_MIXTE: str = "mixte"

NOM_GROUPE_SANS_MARQUE: str = "Offres marketplace sans marque identifiable"

# --------------------------------------------------------------------------- #
# Codes de sortie du CLI
# --------------------------------------------------------------------------- #

CODE_SUCCES: int = 0
CODE_ERREUR_IMPREVUE: int = 1
CODE_ENTREE_INEXPLOITABLE: int = 2

REGEX_DEVISE: str = r"^[A-Z]{3}$"

# --------------------------------------------------------------------------- #
# Limites et hypothèses systématiques
# --------------------------------------------------------------------------- #

LIMITES_SYSTEMATIQUES: tuple[str, ...] = (
    "Le corpus concurrentiel n'est pas exhaustif : il hérite intégralement des "
    "biais de collecte amont (requêtes choisies, plafonds d'items, seuils de "
    "pertinence, blocages anti-bot). Aucune part de marché ne peut en être déduite.",
    "Les rapprochements de concurrents reposent sur la seule similarité de nom. "
    "Deux entités distinctes portant un nom voisin peuvent être fusionnées à tort, "
    "et une même entreprise vendant sous deux marques restera séparée.",
    "Les portées régionales sont hétérogènes entre sources : un prix Amazon décrit "
    "le marché de sa marketplace, une annonce Meta décrit un pays de diffusion, "
    "une offre AliExpress un pays de livraison. Ces plans ne sont pas superposables.",
    "La longévité d'une annonce publicitaire mesure une durée de diffusion, jamais "
    "une rentabilité : une campagne peut être diffusée longtemps à perte.",
    "La classification des correspondances et l'extraction des claims sont produites "
    "par des modèles de langage et n'ont pas été validées empiriquement.",
)

HYPOTHESES_SYSTEMATIQUES: tuple[str, ...] = (
    f"Seuil de pertinence amont retenu : {SEUIL_PERTINENCE_AMONT} (heuristique).",
    "Prix de référence d'une offre AliExpress : le prix de vente de l'annonce, "
    "et non le prix du SKU le moins cher ; les SKU ne servent qu'aux fourchettes.",
    "Les segments de prix sont définis par terciles des prix constatés dans la "
    "source, non par des paliers de marché : ce découpage est arbitraire.",
    f"La concentration publiée est la part de volume des {PART_TOP3_CONCENTRATION} "
    f"premiers concurrents, calculée sur les seuls volumes disponibles.",
    "Les codes de devises sont normalisés depuis les symboles émis par les "
    "collecteurs (« € » → « EUR ») ; aucun montant n'est converti.",
)

# --------------------------------------------------------------------------- #
# Plomberie LLM partagée
# --------------------------------------------------------------------------- #


def verifier_cle_api() -> None:
    """Vérifie la présence de la clé API Anthropic.

    Raises:
        RuntimeError: Si la variable d'environnement est absente ou vide.
    """
    if not os.environ.get(NOM_VARIABLE_CLE_API, "").strip():
        raise RuntimeError(
            f"{NOM_VARIABLE_CLE_API} est absente. Renseigne-la dans un fichier .env "
            f"(voir .env.example) ou dans l'environnement."
        )


def construire_modele(nom_modele: str, max_tokens: int) -> Any:
    """Instancie un modèle de discussion Anthropic à température nulle.

    Args:
        nom_modele: Identifiant du modèle.
        max_tokens: Plafond de jetons produits.

    Returns:
        Une instance de `ChatAnthropic`.
    """
    from langchain_anthropic import ChatAnthropic

    options: dict[str, Any] = {}
    if nom_modele in MODELES_SANS_ECHANTILLONNAGE:
        options["thinking"] = RAISONNEMENT_SYNTHESE
    else:
        options["temperature"] = TEMPERATURE

    return ChatAnthropic(
        model=nom_modele,
        max_tokens=max_tokens,
        timeout=None,
        stop=None,
        **options,
    )


def invoquer_structure(
    chaine: Any,
    entree: dict[str, Any],
    libelle: str,
) -> tuple[Any | None, int, str | None]:
    """Invoque une chaîne LCEL à sortie structurée, avec une reprise sur échec.

    Args:
        chaine: Chaîne LCEL dotée de `with_structured_output`.
        entree: Variables du gabarit. La clé `erreur_precedente` est réservée.
        libelle: Libellé de l'étape, pour la journalisation.

    Returns:
        Le triplet `(resultat_ou_None, nb_tentatives, message_erreur)`.
    """
    derniere_erreur: str | None = None
    for tentative in range(1, NB_TENTATIVES_LLM + 1):
        variables = dict(entree)
        variables["erreur_precedente"] = (
            ""
            if derniere_erreur is None
            else (
                "\n\nATTENTION — ta réponse précédente a été rejetée pour cette "
                f"raison :\n{derniere_erreur}\nCorrige-la et respecte strictement "
                "le schéma demandé."
            )
        )
        try:
            resultat = chaine.invoke(variables)
        except Exception as erreur:  # noqa: BLE001 — toute erreur doit dégrader
            derniere_erreur = f"{type(erreur).__name__} : {erreur}"
            logger.warning(
                "%s — tentative %d/%d en échec : %s",
                libelle,
                tentative,
                NB_TENTATIVES_LLM,
                derniere_erreur,
            )
            continue
        if resultat is None:
            derniere_erreur = "le modèle n'a retourné aucune sortie structurée"
            continue
        logger.debug("%s — succès en %d tentative(s)", libelle, tentative)
        return resultat, tentative, None
    return None, NB_TENTATIVES_LLM, derniere_erreur


# --------------------------------------------------------------------------- #
# Comptabilité des jetons
# --------------------------------------------------------------------------- #
# Multiplicateurs appliqués au tarif d'entrée de base pour les jetons de cache.
# Ces rapports sont les mêmes sur tous les modèles Claude : seule la base varie.
MULT_CACHE_LECTURE: float = 0.10
MULT_CACHE_ECRITURE_5MIN: float = 1.25
MULT_CACHE_ECRITURE_1H: float = 2.00


def resumer_consommation(usage: dict[str, Any]) -> str:
    """Résume la consommation de jetons d'une exécution et son coût estimé.

    POURQUOI LE CACHE EST VENTILÉ — `langchain_anthropic` rajoute les jetons de
    cache dans `input_tokens` (l'`input_tokens` d'Anthropic, lui, les exclut).
    Les tarifer au tarif d'entrée plein surfacturerait une lecture de cache d'un
    facteur dix et, surtout, afficherait un coût identique avec et sans mise en
    cache : le rapport ne montrerait aucune économie là où elle serait pourtant
    réelle. Le détail est donc repris de `input_token_details` et tarifé à part.

    Args:
        usage: Dictionnaire `modèle → métadonnées d'usage`, tel que produit par
            `langchain_core.callbacks.get_usage_metadata_callback`.

    Returns:
        Une ligne de récapitulatif, vide si aucun appel n'a été passé.
    """
    if not usage:
        return ""
    morceaux: list[str] = []
    total = 0.0
    tarif_manquant = False
    for modele, metriques in sorted(usage.items()):
        details = metriques.get("input_token_details") or {}
        lecture = int(details.get("cache_read", 0) or 0)
        # `cache_creation` et la ventilation par TTL s'excluent mutuellement :
        # `langchain_anthropic` remet la première à zéro dès que la seconde est
        # renseignée. Les additionner ne double donc jamais le compte.
        ecriture_5min = int(details.get("ephemeral_5m_input_tokens", 0) or 0)
        ecriture_1h = int(details.get("ephemeral_1h_input_tokens", 0) or 0)
        # Écriture dont le TTL n'est pas ventilé : tarifée au TTL par défaut.
        ecriture_indistincte = int(details.get("cache_creation", 0) or 0)
        ecriture = ecriture_5min + ecriture_1h + ecriture_indistincte

        entree_totale = int(metriques.get("input_tokens", 0) or 0)
        # Le solde est ce qui n'a été ni lu ni écrit en cache : plein tarif.
        entree_neuve = max(entree_totale - lecture - ecriture, 0)
        sortie = int(metriques.get("output_tokens", 0) or 0)

        tarifs = TARIFS_USD_PAR_MTOK.get(modele)
        if tarifs is None:
            # Un modèle absent de la table valait auparavant 0 $ sans le dire :
            # une migration de modèle rendait le rapport faux en silence.
            tarif_manquant = True
            tarif_entree, tarif_sortie = 0.0, 0.0
        else:
            tarif_entree, tarif_sortie = tarifs

        cout = (
            entree_neuve * tarif_entree
            + lecture * tarif_entree * MULT_CACHE_LECTURE
            + (ecriture_5min + ecriture_indistincte) * tarif_entree * MULT_CACHE_ECRITURE_5MIN
            + ecriture_1h * tarif_entree * MULT_CACHE_ECRITURE_1H
            + sortie * tarif_sortie
        ) / 1_000_000
        total += cout

        ligne = f"{modele} : {entree_neuve} jetons entrée / {sortie} sortie"
        if lecture or ecriture:
            part_lue = 100.0 * lecture / entree_totale if entree_totale else 0.0
            ligne += (
                f" / {lecture} cache lu ({part_lue:.0f} % de l'entrée)"
                f" / {ecriture} cache écrit"
            )
        ligne += " (tarif inconnu)" if tarifs is None else f" (~{cout:.4f} $)"
        morceaux.append(ligne)

    recapitulatif = " | ".join(morceaux) + f" | total estimé ~{total:.4f} $"
    if tarif_manquant:
        recapitulatif += (
            " | ATTENTION : un modèle absent de TARIFS_USD_PAR_MTOK a été compté "
            "pour 0 $ — le total est sous-estimé"
        )
    return recapitulatif


def normaliser_devise(devise: str | None) -> str | None:
    """Normalise une étiquette de devise en code ISO quand c'est possible.

    Aucune conversion de montant n'est effectuée : seule l'étiquette change, afin
    que deux sources décrivant la même devise soient regroupables.

    Args:
        devise: Étiquette brute émise par un collecteur.

    Returns:
        Le code ISO-4217 si la correspondance est connue, sinon l'étiquette
        nettoyée telle quelle, ou `None` si l'entrée est vide.
    """
    if devise is None:
        return None
    propre = devise.strip()
    if not propre:
        return None
    if len(propre) == 3 and propre.isalpha() and propre.isupper():
        return propre
    return SYMBOLES_VERS_ISO.get(propre, propre)
