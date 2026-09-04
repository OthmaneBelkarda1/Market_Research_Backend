"""Une dégradation du modèle dit pourquoi elle a lieu, quand la cause appelle une action.

Sans réseau : seule la reconnaissance de la cause est vérifiée.

Le service continue de produire une fiche quand le modèle est indisponible — les
champs déterministes suffisent à une fiche utilisable, et un 503 la retirerait pour
rien. Mais l'avertissement générique « LLM normalization unavailable » ne dit pas à
l'exploitant que recharger un compte suffirait, et cette panne-là frappe TOUTES les
extractions jusqu'à ce que quelqu'un intervienne.
"""

from __future__ import annotations

import pytest
from src.agents.product_extraction import _cause_de_compte

CREDIT_EPUISE = (
    "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
    "'message': 'Your credit balance is too low to access the Anthropic API. "
    "Please go to Plans & Billing to upgrade or purchase credits.'}}"
)
CLE_INVALIDE = (
    "Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error', "
    "'message': 'API key is invalid.'}}"
)


def test_un_credit_epuise_est_reconnu() -> None:
    """Relevé en production le 04/09/2026 : un 400, la classe d'une requête malformée.

    Seul le message sépare « votre compte est vide » de « votre requête est
    invalide », d'où une reconnaissance sur le texte plutôt que sur la classe.
    """
    cause = _cause_de_compte(Exception(CREDIT_EPUISE))
    assert cause and "crédit" in cause and "Billing" in cause


def test_une_cle_refusee_est_reconnue() -> None:
    cause = _cause_de_compte(Exception(CLE_INVALIDE))
    assert cause and "ANTHROPIC_API_KEY" in cause


@pytest.mark.parametrize(
    "message",
    [
        "Connection reset by peer",
        "Read timed out",
        "Error code: 529 - overloaded_error",
        "recursion limit reached",
    ],
)
def test_une_panne_ordinaire_n_est_pas_une_panne_de_compte(message: str) -> None:
    """Une saturation ou une coupure réseau se répare seule, ou par une reprise.

    Les confondre ferait crier au compte vide à chaque hoquet du réseau, et le
    signal ne voudrait plus rien dire.
    """
    assert _cause_de_compte(Exception(message)) is None
