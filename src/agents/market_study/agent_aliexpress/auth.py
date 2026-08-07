"""Signature des appels et gestion du cycle de vie des tokens AliExpress.

Le module lit les identifiants dans l'environnement (`.env`) et, s'il existe,
dans `.tokens.json` — état de session écrit ici même après un rafraîchissement,
et qui PRIME sur `.env`. C'est la seule écriture disque du module ; `.env`
n'est jamais modifié.

Le secret d'application ne sert qu'au calcul local de la signature : il
n'apparaît dans aucune requête, aucun log, aucun fichier écrit.

Statut d'application « Test » (constaté en console le 03/08/2026) :
    * `access_token`  : valide 24 h → rafraîchi automatiquement ici ;
    * `refresh_token` : valide 48 h → sa péremption ne peut PAS être réparée par
      le module (le flux OAuth initial exige un clic humain). Le cas est signalé
      explicitement, sans jamais boucler.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from config import (
    ALIEXPRESS_ACCESS_TOKEN,
    ALIEXPRESS_APP_KEY,
    ALIEXPRESS_APP_SECRET,
    ALIEXPRESS_REFRESH_TOKEN,
    CHEMIN_FICHIER_TOKENS,
    CHEMIN_REST_CREATE,
    CHEMIN_REST_REFRESH,
    METHODE_SIGNATURE,
    TIMEOUT_APPEL_SECS,
    URL_TOKEN_CREATE,
    URL_TOKEN_REFRESH,
    masquer,
    obtenir_logger,
)

_LOG = obtenir_logger(__name__)

_CLE_ACCESS_TOKEN = "access_token"
_CLE_REFRESH_TOKEN = "refresh_token"
_CLE_HORODATAGE = "rafraichi_le"

_CONTENEURS_REPONSE_TOKEN = ("data", "result", "top_auth_token_create_response")
"""Enveloppes possibles de la réponse d'authentification. La forme observée est
plate, mais la passerelle est connue pour imbriquer selon les endpoints : la
lecture est donc tolérante, faute d'avoir pu observer toutes les variantes."""

_ENCODAGE_FICHIER = "utf-8"

MESSAGE_REAUTORISATION = (
    "Le refresh token est expiré ou invalide : le module ne peut pas le "
    "renouveler seul, le flux OAuth initial exige une autorisation humaine. "
    "Voir la section « Ré-autorisation OAuth » du README."
)


@dataclass(frozen=True)
class ResultatRafraichissement:
    """Issue d'une tentative de rafraîchissement du token.

    Attributes:
        access_token: Nouveau token, ou `None` en cas d'échec.
        message_erreur: Cause de l'échec, ou `None` en cas de succès.
        reautorisation_requise: Vrai si seul un flux OAuth humain peut débloquer
            la situation — auquel cas toute nouvelle tentative est inutile.
    """

    access_token: str | None
    message_erreur: str | None
    reautorisation_requise: bool


def signer(params: dict[str, str], secret: str, chemin_rest: str | None = None) -> str:
    """Calcule la signature HMAC-SHA256 d'un appel.

    Args:
        params: Paramètres de l'appel, système et métier, hors `sign`.
        secret: Secret d'application, utilisé localement uniquement.
        chemin_rest: Chemin REST à préfixer à la base de signature, requis pour
            les endpoints `/rest/auth/*` et interdit pour `/sync`.

    Returns:
        La signature en hexadécimal majuscule.
    """
    base = "".join(cle + params[cle] for cle in sorted(params))
    if chemin_rest:
        base = chemin_rest + base
    return hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest().upper()


def horodatage_ms() -> str:
    """Retourne l'horodatage attendu par la passerelle.

    Returns:
        L'epoch en millisecondes, sous forme de chaîne.
    """
    return str(int(time.time() * 1000))


def _lire_fichier_tokens() -> dict[str, Any]:
    """Lit l'état de session persisté, s'il existe.

    Returns:
        Le contenu du fichier, ou un dictionnaire vide s'il est absent ou
        illisible — l'environnement prend alors le relais.
    """
    if not CHEMIN_FICHIER_TOKENS.exists():
        return {}
    try:
        contenu = json.loads(CHEMIN_FICHIER_TOKENS.read_text(encoding=_ENCODAGE_FICHIER))
    except (OSError, json.JSONDecodeError) as exception:
        _LOG.warning(
            "Fichier de tokens « %s » illisible (%s) : repli sur l'environnement.",
            CHEMIN_FICHIER_TOKENS.name,
            exception,
        )
        return {}
    return contenu if isinstance(contenu, dict) else {}


def _ecrire_fichier_tokens(access_token: str, refresh_token: str | None) -> None:
    """Persiste l'état de session après un rafraîchissement.

    Args:
        access_token: Nouveau token d'accès.
        refresh_token: Nouveau refresh token si la passerelle en a renvoyé un,
            sinon l'ancien est conservé.
    """
    charge = {
        _CLE_ACCESS_TOKEN: access_token,
        _CLE_REFRESH_TOKEN: refresh_token or obtenir_refresh_token(),
        _CLE_HORODATAGE: horodatage_ms(),
    }
    try:
        CHEMIN_FICHIER_TOKENS.write_text(
            json.dumps(charge, indent=2), encoding=_ENCODAGE_FICHIER
        )
    except OSError as exception:
        # L'appel en cours peut continuer avec le token en mémoire ; seule la
        # prochaine exécution en pâtira, en repartant du token de `.env`.
        _LOG.error(
            "Écriture de « %s » impossible : %s", CHEMIN_FICHIER_TOKENS, exception
        )
        return
    _LOG.info(
        "Tokens rafraîchis et persistés dans %s (access=%s).",
        CHEMIN_FICHIER_TOKENS.name,
        masquer(access_token),
    )


def obtenir_access_token() -> str | None:
    """Retourne le token d'accès courant.

    `.tokens.json` prime sur `.env` : il porte l'état le plus récent.

    Returns:
        Le token d'accès, ou `None` si aucun n'est disponible.
    """
    return _lire_fichier_tokens().get(_CLE_ACCESS_TOKEN) or ALIEXPRESS_ACCESS_TOKEN


def obtenir_refresh_token() -> str | None:
    """Retourne le refresh token courant.

    Returns:
        Le refresh token, ou `None` si aucun n'est disponible.
    """
    return _lire_fichier_tokens().get(_CLE_REFRESH_TOKEN) or ALIEXPRESS_REFRESH_TOKEN


def _extraire_tokens(charge: dict[str, Any]) -> tuple[str | None, str | None]:
    """Extrait le couple de tokens d'une réponse d'authentification.

    Args:
        charge: Corps JSON de la réponse.

    Returns:
        Le couple `(access_token, refresh_token)`, chaque membre pouvant être nul.
    """
    candidats: list[dict[str, Any]] = [charge]
    for cle in _CONTENEURS_REPONSE_TOKEN:
        valeur = charge.get(cle)
        if isinstance(valeur, dict):
            candidats.append(valeur)

    for candidat in candidats:
        access = candidat.get(_CLE_ACCESS_TOKEN)
        if access:
            return str(access), (
                str(candidat[_CLE_REFRESH_TOKEN])
                if candidat.get(_CLE_REFRESH_TOKEN)
                else None
            )
    return None, None


def _appeler_authentification(
    url: str, chemin_rest: str, parametres: dict[str, str]
) -> dict[str, Any]:
    """Exécute un appel signé vers un endpoint `/rest/auth/*`.

    Args:
        url: URL complète de l'endpoint.
        chemin_rest: Chemin à préfixer à la base de signature.
        parametres: Paramètres métier de l'appel.

    Returns:
        Le corps JSON de la réponse.

    Raises:
        RuntimeError: Si les identifiants d'application sont absents.
        httpx.HTTPError: Si l'appel réseau échoue.
        ValueError: Si la réponse n'est pas du JSON.
    """
    if not ALIEXPRESS_APP_KEY or not ALIEXPRESS_APP_SECRET:
        raise RuntimeError(
            "ALIEXPRESS_APP_KEY et ALIEXPRESS_APP_SECRET sont requis dans .env."
        )

    charge = {
        "app_key": ALIEXPRESS_APP_KEY,
        "sign_method": METHODE_SIGNATURE,
        "timestamp": horodatage_ms(),
        **parametres,
    }
    charge["sign"] = signer(charge, ALIEXPRESS_APP_SECRET, chemin_rest)

    reponse = httpx.post(url, params=charge, timeout=TIMEOUT_APPEL_SECS)
    return reponse.json()


def rafraichir_token() -> ResultatRafraichissement:
    """Rafraîchit le token d'accès et persiste le nouvel état de session.

    N'est jamais rappelée en boucle : un refresh token expiré est un état
    définitif du point de vue du module.

    Returns:
        L'issue de la tentative.
    """
    refresh_token = obtenir_refresh_token()
    if not refresh_token:
        return ResultatRafraichissement(
            access_token=None,
            message_erreur=(
                "Aucun refresh token disponible (ni dans .env, ni dans "
                f"{CHEMIN_FICHIER_TOKENS.name}). {MESSAGE_REAUTORISATION}"
            ),
            reautorisation_requise=True,
        )

    _LOG.info("Rafraîchissement du token (refresh=%s).", masquer(refresh_token))
    try:
        charge = _appeler_authentification(
            URL_TOKEN_REFRESH, CHEMIN_REST_REFRESH, {"refresh_token": refresh_token}
        )
    except Exception as exception:  # noqa: BLE001 — converti en résultat explicite
        return ResultatRafraichissement(
            access_token=None,
            message_erreur=f"Appel de rafraîchissement en échec : {exception}",
            reautorisation_requise=False,
        )

    access_token, nouveau_refresh = _extraire_tokens(charge)
    if not access_token:
        # La passerelle ne distingue pas toujours « token expiré » d'une autre
        # erreur d'authentification : dans le doute, on exige la ré-autorisation
        # plutôt que de rejouer un appel voué à échouer.
        detail = charge.get("message") or charge.get("error_description") or charge
        return ResultatRafraichissement(
            access_token=None,
            message_erreur=f"{MESSAGE_REAUTORISATION} Réponse de la passerelle : {detail}",
            reautorisation_requise=True,
        )

    _ecrire_fichier_tokens(access_token, nouveau_refresh)
    return ResultatRafraichissement(
        access_token=access_token, message_erreur=None, reautorisation_requise=False
    )


def creer_token(code: str, redirect_uri: str | None = None) -> ResultatRafraichissement:
    """Échange un code d'autorisation OAuth contre un couple de tokens.

    Fournie pour ré-amorcer une session après péremption du refresh token, à
    exécuter manuellement depuis un interpréteur Python. Cette fonction n'est
    JAMAIS appelée automatiquement par le module : obtenir le code exige un
    clic humain dans un navigateur.

    Args:
        code: Code d'autorisation renvoyé par le navigateur après le clic.
        redirect_uri: URI de redirection déclarée dans la console, si la
            passerelle l'exige pour cet échange.

    Returns:
        L'issue de l'échange ; en cas de succès, `.tokens.json` est écrit.
    """
    parametres = {"code": code}
    if redirect_uri:
        parametres["redirect_uri"] = redirect_uri

    try:
        charge = _appeler_authentification(
            URL_TOKEN_CREATE, CHEMIN_REST_CREATE, parametres
        )
    except Exception as exception:  # noqa: BLE001 — converti en résultat explicite
        return ResultatRafraichissement(
            access_token=None,
            message_erreur=f"Appel de création de token en échec : {exception}",
            reautorisation_requise=True,
        )

    access_token, nouveau_refresh = _extraire_tokens(charge)
    if not access_token:
        return ResultatRafraichissement(
            access_token=None,
            message_erreur=f"Aucun token dans la réponse : {charge}",
            reautorisation_requise=True,
        )

    _ecrire_fichier_tokens(access_token, nouveau_refresh)
    return ResultatRafraichissement(
        access_token=access_token, message_erreur=None, reautorisation_requise=False
    )
