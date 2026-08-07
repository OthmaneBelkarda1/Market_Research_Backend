"""Appels aux deux méthodes métier de l'API Dropshipping AliExpress.

Deux fonctions seulement, toutes deux synchrones et toutes deux muettes en
matière d'exception : `rechercher_produits` (phase A) et `detailler_produit`
(phase B). Toute erreur — réseau, signature, token, quota, produit introuvable —
devient un `StatutCollecte(succes=False, ...)` que l'agent consigne et
interprète.

Les paramètres régionaux proviennent EXCLUSIVEMENT de l'objet `ParametresMarche`
reçu en argument. Aucun défaut, aucune constante de repli : c'est la garantie
que le prix collecté est bien celui de la région d'étude.

Trois cas méritent d'être distingués dans les statuts, car ils s'interprètent
différemment en aval :
    * échec technique (réseau, token, quota) → `succes=False` ;
    * réponse valide sans aucun produit → `succes=True`, `nb_items=0` : le
      produit n'est pas disponible à la livraison dans la région, ou la requête
      ne correspond à rien. C'est une information sur le marché, pas une panne ;
    * produit introuvable en phase B → `succes=False` ciblé sur cet itemId, sans
      conséquence sur les autres produits.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from auth import obtenir_access_token, rafraichir_token, horodatage_ms, signer
from config import (
    ALIEXPRESS_APP_KEY,
    ALIEXPRESS_APP_SECRET,
    BACKOFF_TENTATIVES_SECS,
    BACKOFF_TRANSITOIRE_SECS,
    CLE_CODE_DETAIL,
    CLE_CODE_RECHERCHE,
    CLE_CONTENEUR_PRODUITS,
    CLE_DONNEES_RECHERCHE,
    CLE_ENVELOPPE_DETAIL,
    CLE_ENVELOPPE_ERREUR,
    CLE_ENVELOPPE_RECHERCHE,
    CLE_ERREUR_CODE,
    CLE_ERREUR_TYPE,
    CLE_LISTE_PRODUITS,
    CLE_MESSAGE_DETAIL,
    CLE_RESULTAT_DETAIL,
    CLE_TOTAL_ANNONCE,
    CODE_PRODUIT_INTROUVABLE,
    CODE_SUCCES_DETAIL,
    CODE_SUCCES_RECHERCHE,
    CODE_TOKEN_INVALIDE,
    CODES_ERREUR_TRANSITOIRE,
    CLES_ERREUR_MESSAGE,
    ETAPE_DETAIL,
    ETAPE_RECHERCHE,
    ETAPE_REAUTORISATION_OAUTH,
    MARQUEURS_ERREUR_QUOTA,
    MESSAGE_PRODUIT_INTROUVABLE,
    MESSAGE_QUOTA,
    MESSAGE_RECHERCHE_VIDE,
    METHODE_DETAIL,
    METHODE_RECHERCHE,
    METHODE_SIGNATURE,
    NB_TENTATIVES_MAX,
    NB_TENTATIVES_MAX_TRANSITOIRE,
    PAUSE_ENTRE_APPELS_SECS,
    PAUSE_ENTRE_RECHERCHES_SECS,
    TAILLE_PAGE,
    TIMEOUT_APPEL_SECS,
    URL_PASSERELLE_SYNC,
    masquer,
    obtenir_logger,
    url_autorisation,
)
from schemas import ParametresMarche, StatutCollecte

_LOG = obtenir_logger(__name__)

_nb_appels_api: int = 0
"""Compteur d'appels métier réellement émis, nouvelles tentatives comprises.

Le budget nominal de config.py ne compte que les appels prévus ; ce compteur
mesure ce qui a effectivement été consommé, et alimente `StatsCollecte`."""


def compteur_appels() -> int:
    """Retourne le nombre d'appels métier émis depuis la réinitialisation.

    Returns:
        Le compteur d'appels, nouvelles tentatives incluses.
    """
    return _nb_appels_api


def reinitialiser_compteur() -> None:
    """Remet à zéro le compteur d'appels métier, au début d'une collecte."""
    global _nb_appels_api
    _nb_appels_api = 0


@dataclass
class _Analyse:
    """Lecture d'une réponse de la passerelle.

    Attributes:
        donnees: Charge utile extraite en cas de succès.
        message_erreur: Cause de l'échec, sinon `None`.
        token_invalide: Vrai si l'échec vient d'un access token périmé.
        transitoire: Vrai si l'échec est connu pour se résorber de lui-même.
        definitif: Vrai si toute nouvelle tentative est inutile (produit
            introuvable, par exemple).
        total_annonce: Nombre total de résultats annoncé par la recherche.
        nb_items: Nombre d'items effectivement renvoyés.
    """

    donnees: Any = None
    message_erreur: str | None = None
    token_invalide: bool = False
    transitoire: bool = False
    definitif: bool = False
    total_annonce: int | None = None
    nb_items: int = 0

    @property
    def succes(self) -> bool:
        """Indique si l'appel a abouti.

        Returns:
            Vrai si aucune erreur n'a été relevée.
        """
        return self.message_erreur is None


def _est_erreur_quota(code: str, message: str) -> bool:
    """Reconnaît un dépassement de débit ou de quota.

    La liste officielle des codes n'étant pas publiée, la détection repose sur
    des fragments de texte : elle peut manquer un cas ou en signaler un à tort.

    Args:
        code: Code d'erreur renvoyé par la passerelle.
        message: Message d'erreur associé.

    Returns:
        Vrai si l'erreur évoque un dépassement de flux.
    """
    sujet = f"{code} {message}".casefold()
    return any(marqueur in sujet for marqueur in MARQUEURS_ERREUR_QUOTA)


def _analyser_erreur(charge: dict[str, Any]) -> _Analyse | None:
    """Détecte une erreur de passerelle, quelle que soit son enveloppe.

    Forme réellement observée le 03/08/2026 sur les deux méthodes :
    `{"error_response": {"type": "ISV", "code": "IllegalAccessToken",
    "msg": "..."}}`. La forme racine `{"type", "code", "message"}` est acceptée
    en repli.

    Args:
        charge: Corps JSON de la réponse.

    Returns:
        L'analyse correspondante, ou `None` si la réponse n'est pas une erreur
        de cette famille.
    """
    enveloppe = charge.get(CLE_ENVELOPPE_ERREUR)
    bloc = enveloppe if isinstance(enveloppe, dict) else charge

    code = bloc.get(CLE_ERREUR_CODE)
    if code is None or CLE_ERREUR_TYPE not in bloc:
        return None

    code_texte = str(code)
    message = next(
        (str(bloc[cle]) for cle in CLES_ERREUR_MESSAGE if bloc.get(cle)), ""
    )
    if code_texte == CODE_TOKEN_INVALIDE:
        return _Analyse(
            message_erreur=f"{code_texte} : {message}", token_invalide=True
        )
    if _est_erreur_quota(code_texte, message):
        return _Analyse(message_erreur=f"{MESSAGE_QUOTA} ({code_texte} : {message})")
    return _Analyse(
        message_erreur=f"Erreur passerelle {code_texte} : {message}",
        transitoire=code_texte in CODES_ERREUR_TRANSITOIRE,
    )


def _analyser_recherche(charge: dict[str, Any]) -> _Analyse:
    """Lit une réponse de `aliexpress.ds.text.search`.

    Le succès se reconnaît au code « 00 » (chaîne) ; une réponse valide sans
    produit est un succès à zéro item.

    Args:
        charge: Corps JSON de la réponse.

    Returns:
        L'analyse de la réponse.
    """
    erreur = _analyser_erreur(charge)
    if erreur:
        return erreur

    enveloppe = charge.get(CLE_ENVELOPPE_RECHERCHE)
    if not isinstance(enveloppe, dict):
        return _Analyse(
            message_erreur=(
                "Réponse de forme inattendue : enveloppe "
                f"« {CLE_ENVELOPPE_RECHERCHE} » absente."
            )
        )

    code = enveloppe.get(CLE_CODE_RECHERCHE)
    if code != CODE_SUCCES_RECHERCHE:
        code_texte = str(code)
        if _est_erreur_quota(code_texte, ""):
            return _Analyse(message_erreur=f"{MESSAGE_QUOTA} (code {code_texte})")
        return _Analyse(
            message_erreur=f"Recherche en échec, code={code_texte!r}.",
            transitoire=code_texte in CODES_ERREUR_TRANSITOIRE,
        )

    donnees = enveloppe.get(CLE_DONNEES_RECHERCHE) or {}
    conteneur = donnees.get(CLE_CONTENEUR_PRODUITS) or {}
    produits = conteneur.get(CLE_LISTE_PRODUITS) or []
    if not isinstance(produits, list):
        return _Analyse(
            message_erreur=(
                f"Réponse de forme inattendue : « {CLE_LISTE_PRODUITS} » n'est "
                "pas une liste."
            )
        )

    total = donnees.get(CLE_TOTAL_ANNONCE)
    return _Analyse(
        donnees=[item for item in produits if isinstance(item, dict)],
        total_annonce=int(total) if isinstance(total, (int, float)) else None,
        nb_items=len(produits),
    )


def _analyser_detail(charge: dict[str, Any]) -> _Analyse:
    """Lit une réponse de `aliexpress.ds.product.get`.

    Le succès se reconnaît au code `rsp_code` valant 200 (entier) — forme
    différente de celle de la recherche, ce que la passerelle ne documente pas.

    Args:
        charge: Corps JSON de la réponse.

    Returns:
        L'analyse de la réponse.
    """
    erreur = _analyser_erreur(charge)
    if erreur:
        return erreur

    enveloppe = charge.get(CLE_ENVELOPPE_DETAIL)
    if not isinstance(enveloppe, dict):
        return _Analyse(
            message_erreur=(
                "Réponse de forme inattendue : enveloppe "
                f"« {CLE_ENVELOPPE_DETAIL} » absente."
            )
        )

    code = enveloppe.get(CLE_CODE_DETAIL)
    message = str(enveloppe.get(CLE_MESSAGE_DETAIL) or "")
    if code != CODE_SUCCES_DETAIL:
        if code == CODE_PRODUIT_INTROUVABLE or MESSAGE_PRODUIT_INTROUVABLE in message:
            return _Analyse(
                message_erreur=(
                    "Produit introuvable pour cette région "
                    f"({CODE_PRODUIT_INTROUVABLE} : {message or MESSAGE_PRODUIT_INTROUVABLE})."
                ),
                definitif=True,
            )
        if _est_erreur_quota(str(code), message):
            return _Analyse(message_erreur=f"{MESSAGE_QUOTA} ({code} : {message})")
        return _Analyse(message_erreur=f"Détail en échec, rsp_code={code!r} : {message}")

    resultat = enveloppe.get(CLE_RESULTAT_DETAIL)
    if not isinstance(resultat, dict):
        return _Analyse(
            message_erreur=(
                f"Réponse de forme inattendue : « {CLE_RESULTAT_DETAIL} » absent "
                "malgré un code de succès."
            )
        )
    return _Analyse(donnees=resultat, nb_items=1)


def _appeler(methode: str, parametres_metier: dict[str, str]) -> dict[str, Any]:
    """Émet un appel signé vers la passerelle métier.

    Args:
        methode: Nom de la méthode `aliexpress.ds.*`.
        parametres_metier: Paramètres propres à la méthode, déjà convertis en
            chaînes.

    Returns:
        Le corps JSON de la réponse.

    Raises:
        RuntimeError: Si les identifiants d'application ou le token sont absents.
        httpx.HTTPError: Si l'appel réseau échoue.
        ValueError: Si la réponse n'est pas du JSON.
    """
    global _nb_appels_api

    if not ALIEXPRESS_APP_KEY or not ALIEXPRESS_APP_SECRET:
        raise RuntimeError(
            "ALIEXPRESS_APP_KEY et ALIEXPRESS_APP_SECRET sont requis dans .env."
        )
    access_token = obtenir_access_token()
    if not access_token:
        raise RuntimeError(
            "Aucun access token disponible : renseigner ALIEXPRESS_ACCESS_TOKEN "
            "dans .env."
        )

    charge = {
        "method": methode,
        "app_key": ALIEXPRESS_APP_KEY,
        "access_token": access_token,
        "sign_method": METHODE_SIGNATURE,
        "timestamp": horodatage_ms(),
        **parametres_metier,
    }
    charge["sign"] = signer(charge, ALIEXPRESS_APP_SECRET)

    # Le token est masqué et la signature omise : les logs doivent rester
    # partageables. Le secret d'application n'apparaît nulle part, par
    # construction — il ne fait pas partie de la requête.
    _LOG.info(
        "Appel %s — %s (access_token=%s)",
        methode,
        json.dumps(parametres_metier, ensure_ascii=False),
        masquer(access_token),
    )

    _nb_appels_api += 1
    reponse = httpx.post(URL_PASSERELLE_SYNC, params=charge, timeout=TIMEOUT_APPEL_SECS)
    return reponse.json()


def _executer(
    methode: str,
    parametres_metier: dict[str, str],
    analyser: Callable[[dict[str, Any]], _Analyse],
    etape: str,
    cible: str,
    pause: float,
) -> tuple[Any, StatutCollecte]:
    """Exécute un appel métier avec reprises, auto-refresh et statut.

    Le rafraîchissement de token est tenté UNE SEULE FOIS par appel : si
    l'erreur persiste ensuite, l'échec est définitif. Une ré-autorisation OAuth
    requise interrompt immédiatement les reprises, aucune n'ayant de chance
    d'aboutir.

    Args:
        methode: Nom de la méthode `aliexpress.ds.*`.
        parametres_metier: Paramètres propres à la méthode.
        analyser: Fonction de lecture de la réponse.
        etape: Étape reportée dans le statut.
        cible: Objet de l'appel, reporté dans le statut.
        pause: Pause appliquée avant l'appel, par prudence de débit.

    Returns:
        Un couple `(donnees, statut)` ; `donnees` vaut `None` en cas d'échec.
    """
    tentative = 0
    refresh_tente = False
    rejeu_apres_refresh = False
    analyse = _Analyse(message_erreur="Échec inconnu.")

    while True:
        if rejeu_apres_refresh:
            # Rejeu immédiat après un token renouvelé : ni attente ni tentative
            # consommée, l'appel précédent n'a pas été jugé sur le fond.
            rejeu_apres_refresh = False
        else:
            tentative += 1
            if tentative > 1:
                sequence = (
                    BACKOFF_TRANSITOIRE_SECS
                    if analyse.transitoire
                    else BACKOFF_TENTATIVES_SECS
                )
                attente = sequence[min(tentative - 2, len(sequence) - 1)]
                _LOG.warning(
                    "Nouvelle tentative %s pour %s « %s » dans %s s (%s)",
                    tentative,
                    etape,
                    cible,
                    attente,
                    analyse.message_erreur,
                )
                time.sleep(attente)
            elif pause:
                time.sleep(pause)

        try:
            analyse = analyser(_appeler(methode, parametres_metier))
        except Exception as exception:  # noqa: BLE001 — aucune exception ne remonte
            analyse = _Analyse(message_erreur=f"{type(exception).__name__}: {exception}")

        if analyse.succes:
            return analyse.donnees, StatutCollecte(
                etape=etape,
                cible=cible,
                succes=True,
                message_erreur=(
                    MESSAGE_RECHERCHE_VIDE
                    if etape == ETAPE_RECHERCHE and analyse.nb_items == 0
                    else None
                ),
                nb_items=analyse.nb_items,
                nb_tentatives=tentative,
                total_annonce=analyse.total_annonce,
            )

        if analyse.token_invalide and not refresh_tente:
            refresh_tente = True
            resultat = rafraichir_token()
            if resultat.access_token:
                _LOG.info("Token rafraîchi, l'appel est rejoué une fois.")
                rejeu_apres_refresh = True
                continue
            statut_message = resultat.message_erreur or "Rafraîchissement en échec."
            _LOG.error("Rafraîchissement du token impossible : %s", statut_message)
            if resultat.reautorisation_requise:
                # Aucune reprise ne peut aboutir : seul un clic humain le peut.
                # L'étape dédiée permet à l'agent d'interrompre la collecte au
                # lieu de rejouer 23 appels voués à l'échec.
                return None, StatutCollecte(
                    etape=ETAPE_REAUTORISATION_OAUTH,
                    cible=cible,
                    succes=False,
                    message_erreur=(
                        f"{statut_message} URL d'autorisation à ouvrir "
                        f"manuellement : {url_autorisation()}"
                    ),
                    nb_items=0,
                    nb_tentatives=tentative,
                )
            return None, StatutCollecte(
                etape=etape,
                cible=cible,
                succes=False,
                message_erreur=statut_message,
                nb_items=0,
                nb_tentatives=tentative,
            )

        if analyse.definitif:
            break

        plafond = (
            NB_TENTATIVES_MAX_TRANSITOIRE if analyse.transitoire else NB_TENTATIVES_MAX
        )
        if tentative >= plafond:
            break

    _LOG.error("%s « %s » en échec : %s", etape, cible, analyse.message_erreur)
    return None, StatutCollecte(
        etape=etape,
        cible=cible,
        succes=False,
        message_erreur=analyse.message_erreur,
        nb_items=0,
        nb_tentatives=tentative,
    )


def rechercher_produits(
    requete: str, marche: ParametresMarche, page_index: int
) -> tuple[list[dict], StatutCollecte]:
    """Interroge `aliexpress.ds.text.search` pour une requête et une page.

    Les quatre paramètres régionaux — `countryCode`, `currency`, `local` — sont
    dérivés du seul objet `marche`.

    Args:
        requete: Requête marketplace, dans la langue du marché.
        marche: Région d'étude, propagée telle quelle à la passerelle.
        page_index: Numéro de page, à partir de 1.

    Returns:
        Un couple `(items_bruts, statut)`. Les items sont renvoyés tels que la
        passerelle les a produits ; leur normalisation relève de `normalize`.
    """
    if not requete.strip():
        return [], StatutCollecte(
            etape=ETAPE_RECHERCHE,
            cible=requete,
            succes=False,
            message_erreur="Requête vide.",
        )

    parametres = {
        "keyWord": requete,
        "countryCode": marche.geo,
        "currency": marche.devise,
        "local": marche.local,
        "pageSize": str(TAILLE_PAGE),
        "pageIndex": str(page_index),
    }
    donnees, statut = _executer(
        METHODE_RECHERCHE,
        parametres,
        _analyser_recherche,
        ETAPE_RECHERCHE,
        f"{requete} (page {page_index})",
        PAUSE_ENTRE_RECHERCHES_SECS,
    )
    return (donnees or []), statut


def detailler_produit(
    item_id: str, marche: ParametresMarche
) -> tuple[dict | None, StatutCollecte]:
    """Interroge `aliexpress.ds.product.get` pour un produit.

    Args:
        item_id: Identifiant du produit AliExpress.
        marche: Région d'étude, propagée en `ship_to_country`,
            `target_currency` et `target_language`.

    Returns:
        Un couple `(resultat_brut, statut)` ; le résultat vaut `None` en cas
        d'échec.
    """
    if not item_id.strip():
        return None, StatutCollecte(
            etape=ETAPE_DETAIL,
            cible=item_id,
            succes=False,
            message_erreur="Identifiant produit vide.",
        )

    parametres = {
        "product_id": item_id,
        "ship_to_country": marche.geo,
        "target_currency": marche.devise,
        "target_language": marche.langue,
    }
    return _executer(
        METHODE_DETAIL,
        parametres,
        _analyser_detail,
        ETAPE_DETAIL,
        item_id,
        PAUSE_ENTRE_APPELS_SECS,
    )
