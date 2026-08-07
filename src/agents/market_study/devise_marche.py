"""Détermination de la devise d'étude d'un marché.

Utilitaire d'orchestration, **pas un agent de collecte** : il ne produit pas le
socle de sortie commun décrit dans AGENTS.md et n'appelle aucune source externe.
Il traduit un code pays ISO 3166-1 alpha-2 en son code monétaire ISO 4217.

POURQUOI UNE TABLE ET NON UN MODÈLE — la devise d'un pays est un fait
administratif. Une table est déterministe, gratuite, instantanée, et surtout
corrigible ligne à ligne le jour où une monnaie change ; une estimation par
modèle serait plus lente, facturée, et faillible sans être auditable. Le module
frère `langues_marche.py` suit la même logique et couvre le même jeu de pays :
les deux tables se contrôlent l'une l'autre.

CE QUE LA DEVISE COMMANDE EN AVAL — elle est transmise à `agent_aliexpress`
(`--devise`), qui **exclut toute ligne de prix libellée dans une autre devise
que celle demandée** plutôt que de la convertir, et à `agent_analyse_
concurrentielle` (`--devise-envisagee`) lorsqu'un prix envisagé est fourni. Si
l'API AliExpress ne sait pas servir la devise locale d'un marché, elle répond
dans la sienne et toutes les lignes sont écartées : la collecte revient vide
sans être techniquement en échec. Le repli est alors de réimposer une devise
que la plateforme sert, typiquement USD ou EUR, par l'option `-Devise` de
l'orchestrateur.

Sortie sur `stdout` :

```json
{"geo": "US",
 "devise": "USD",
 "nom": "dollar des États-Unis",
 "source": "table",
 "date_validite": "2026-08-06",
 "limites": ["…"]}
```

Codes de sortie : `0` succès, `1` pays inconnu de la table, `2` usage argparse.
Un pays inconnu n'est jamais remplacé par un défaut : servir des prix dans une
devise devinée fausserait silencieusement tout le benchmark aval.
"""

from __future__ import annotations

import argparse
import json
import sys

# --------------------------------------------------------------------------- #
# Correctif d'encodage — même motif que les modules du pipeline : sans lui, un
# nom de devise accentué écrit sur stdout devient illisible en cp1252.
# --------------------------------------------------------------------------- #
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

DATE_VALIDITE: str = "2026-08-06"
"""Date à laquelle la table a été vérifiée.

Trois entrées méritent un contrôle en priorité, parce qu'elles ont changé
récemment : la Bulgarie est passée à l'euro le 1er janvier 2026, le Zimbabwe au
ZWG en avril 2024, Curaçao et Sint-Maarten au XCG en mars 2025.
"""

# --------------------------------------------------------------------------- #
# Table pays → devise (ISO 3166-1 alpha-2 → ISO 4217)
# --------------------------------------------------------------------------- #
# Les territoires inhabités ou sans économie de détail (AQ, BV, HM, GS, TF, UM)
# sont volontairement absents : une étude de marché n'y a pas d'objet, et leur
# absence produit une erreur explicite plutôt qu'une devise de façade.

DEVISE_PAR_PAYS: dict[str, str] = {
    # --- Europe ----------------------------------------------------------- #
    "AD": "EUR", "AL": "ALL", "AT": "EUR", "AX": "EUR", "BA": "BAM",
    "BE": "EUR", "BG": "EUR", "BY": "BYN", "CH": "CHF", "CY": "EUR",
    "CZ": "CZK", "DE": "EUR", "DK": "DKK", "EE": "EUR", "ES": "EUR",
    "FI": "EUR", "FO": "DKK", "FR": "EUR", "GB": "GBP", "GG": "GBP",
    "GI": "GIP", "GR": "EUR", "HR": "EUR", "HU": "HUF", "IE": "EUR",
    "IM": "GBP", "IS": "ISK", "IT": "EUR", "JE": "GBP", "LI": "CHF",
    "LT": "EUR", "LU": "EUR", "LV": "EUR", "MC": "EUR", "MD": "MDL",
    "ME": "EUR", "MK": "MKD", "MT": "EUR", "NL": "EUR", "NO": "NOK",
    "PL": "PLN", "PT": "EUR", "RO": "RON", "RS": "RSD", "RU": "RUB",
    "SE": "SEK", "SI": "EUR", "SJ": "NOK", "SK": "EUR", "SM": "EUR",
    "UA": "UAH", "VA": "EUR", "XK": "EUR",
    # --- Amériques -------------------------------------------------------- #
    "AG": "XCD", "AI": "XCD", "AR": "ARS", "AW": "AWG", "BB": "BBD",
    "BL": "EUR", "BM": "BMD", "BO": "BOB", "BQ": "USD", "BR": "BRL",
    "BS": "BSD", "BZ": "BZD", "CA": "CAD", "CL": "CLP", "CO": "COP",
    "CR": "CRC", "CU": "CUP", "CW": "XCG", "DM": "XCD", "DO": "DOP",
    "EC": "USD", "FK": "FKP", "GD": "XCD", "GF": "EUR", "GL": "DKK",
    "GP": "EUR", "GT": "GTQ", "GY": "GYD", "HN": "HNL", "HT": "HTG",
    "JM": "JMD", "KN": "XCD", "KY": "KYD", "LC": "XCD", "MF": "EUR",
    "MQ": "EUR", "MS": "XCD", "MX": "MXN", "NI": "NIO", "PA": "PAB",
    "PE": "PEN", "PM": "EUR", "PR": "USD", "PY": "PYG", "SR": "SRD",
    "SV": "USD", "SX": "XCG", "TC": "USD", "TT": "TTD", "US": "USD",
    "UY": "UYU", "VC": "XCD", "VE": "VES", "VG": "USD", "VI": "USD",
    # --- Afrique ---------------------------------------------------------- #
    "AO": "AOA", "BF": "XOF", "BI": "BIF", "BJ": "XOF", "BW": "BWP",
    "CD": "CDF", "CF": "XAF", "CG": "XAF", "CI": "XOF", "CM": "XAF",
    "CV": "CVE", "DJ": "DJF", "DZ": "DZD", "EG": "EGP", "EH": "MAD",
    "ER": "ERN", "ET": "ETB", "GA": "XAF", "GH": "GHS", "GM": "GMD",
    "GN": "GNF", "GQ": "XAF", "GW": "XOF", "KE": "KES", "KM": "KMF",
    "LR": "LRD", "LS": "LSL", "LY": "LYD", "MA": "MAD", "MG": "MGA",
    "ML": "XOF", "MR": "MRU", "MU": "MUR", "MW": "MWK", "MZ": "MZN",
    "NA": "NAD", "NE": "XOF", "NG": "NGN", "RE": "EUR", "RW": "RWF",
    "SC": "SCR", "SD": "SDG", "SH": "SHP", "SL": "SLE", "SN": "XOF",
    "SO": "SOS", "SS": "SSP", "ST": "STN", "SZ": "SZL", "TD": "XAF",
    "TG": "XOF", "TN": "TND", "TZ": "TZS", "UG": "UGX", "YT": "EUR",
    "ZA": "ZAR", "ZM": "ZMW", "ZW": "ZWG",
    # --- Asie et Moyen-Orient --------------------------------------------- #
    "AE": "AED", "AF": "AFN", "AM": "AMD", "AZ": "AZN", "BD": "BDT",
    "BH": "BHD", "BN": "BND", "BT": "BTN", "CN": "CNY", "GE": "GEL",
    "HK": "HKD", "ID": "IDR", "IL": "ILS", "IN": "INR", "IQ": "IQD",
    "IR": "IRR", "JO": "JOD", "JP": "JPY", "KG": "KGS", "KH": "KHR",
    "KP": "KPW", "KR": "KRW", "KW": "KWD", "KZ": "KZT", "LA": "LAK",
    "LB": "LBP", "LK": "LKR", "MM": "MMK", "MN": "MNT", "MO": "MOP",
    "MV": "MVR", "MY": "MYR", "NP": "NPR", "OM": "OMR", "PH": "PHP",
    "PK": "PKR", "PS": "ILS", "QA": "QAR", "SA": "SAR", "SG": "SGD",
    "SY": "SYP", "TH": "THB", "TJ": "TJS", "TL": "USD", "TM": "TMT",
    "TR": "TRY", "TW": "TWD", "UZ": "UZS", "VN": "VND", "YE": "YER",
    # --- Océanie ---------------------------------------------------------- #
    "AS": "USD", "AU": "AUD", "CC": "AUD", "CK": "NZD", "CX": "AUD",
    "FJ": "FJD", "FM": "USD", "GU": "USD", "KI": "AUD", "MH": "USD",
    "MP": "USD", "NC": "XPF", "NF": "AUD", "NR": "AUD", "NU": "NZD",
    "NZ": "NZD", "PF": "XPF", "PG": "PGK", "PN": "NZD", "PW": "USD",
    "SB": "SBD", "TK": "NZD", "TO": "TOP", "TV": "AUD", "VU": "VUV",
    "WF": "XPF", "WS": "WST",
    # --- Océan Indien ------------------------------------------------------ #
    "IO": "USD",
}
"""Devise d'usage courant du pays, pas nécessairement sa seule monnaie légale.

Plusieurs marchés circulent de fait en dollar américain à côté de leur monnaie
nationale (Panama, Liban, Zimbabwe, Cambodge…). La table retient celle dans
laquelle les prix de détail sont affichés en ligne, puisque c'est la seule
grandeur que le pipeline compare.
"""

NOMS_DEVISES: dict[str, str] = {
    "AED": "dirham des Émirats arabes unis", "AFN": "afghani",
    "ALL": "lek albanais", "AMD": "dram arménien", "AOA": "kwanza angolais",
    "ARS": "peso argentin", "AUD": "dollar australien", "AWG": "florin arubais",
    "AZN": "manat azerbaïdjanais", "BAM": "mark convertible de Bosnie",
    "BBD": "dollar barbadien", "BDT": "taka bangladais", "BHD": "dinar bahreïni",
    "BIF": "franc burundais", "BMD": "dollar bermudien", "BND": "dollar de Brunei",
    "BOB": "boliviano", "BRL": "réal brésilien", "BSD": "dollar bahaméen",
    "BTN": "ngultrum bhoutanais", "BWP": "pula botswanais",
    "BYN": "rouble biélorusse", "BZD": "dollar bélizien", "CAD": "dollar canadien",
    "CDF": "franc congolais", "CHF": "franc suisse", "CLP": "peso chilien",
    "CNY": "yuan chinois", "COP": "peso colombien", "CRC": "colón costaricien",
    "CUP": "peso cubain", "CVE": "escudo cap-verdien", "CZK": "couronne tchèque",
    "DJF": "franc djiboutien", "DKK": "couronne danoise", "DOP": "peso dominicain",
    "DZD": "dinar algérien", "EGP": "livre égyptienne", "ERN": "nakfa érythréen",
    "ETB": "birr éthiopien", "EUR": "euro", "FJD": "dollar fidjien",
    "FKP": "livre des Malouines", "GBP": "livre sterling", "GEL": "lari géorgien",
    "GHS": "cedi ghanéen", "GIP": "livre de Gibraltar", "GMD": "dalasi gambien",
    "GNF": "franc guinéen", "GTQ": "quetzal guatémaltèque",
    "GYD": "dollar guyanien", "HKD": "dollar de Hong Kong",
    "HNL": "lempira hondurien", "HTG": "gourde haïtienne", "HUF": "forint hongrois",
    "IDR": "roupie indonésienne", "ILS": "shekel israélien", "INR": "roupie indienne",
    "IQD": "dinar irakien", "IRR": "rial iranien", "ISK": "couronne islandaise",
    "JMD": "dollar jamaïcain", "JOD": "dinar jordanien", "JPY": "yen japonais",
    "KES": "shilling kényan", "KGS": "som kirghize", "KHR": "riel cambodgien",
    "KMF": "franc comorien", "KPW": "won nord-coréen", "KRW": "won sud-coréen",
    "KWD": "dinar koweïtien", "KYD": "dollar des îles Caïmans",
    "KZT": "tenge kazakh", "LAK": "kip laotien", "LBP": "livre libanaise",
    "LKR": "roupie srilankaise", "LRD": "dollar libérien", "LSL": "loti lesothan",
    "LYD": "dinar libyen", "MAD": "dirham marocain", "MDL": "leu moldave",
    "MGA": "ariary malgache", "MKD": "denar macédonien", "MMK": "kyat birman",
    "MNT": "tugrik mongol", "MOP": "pataca de Macao", "MRU": "ouguiya mauritanien",
    "MUR": "roupie mauricienne", "MVR": "rufiyaa maldivien", "MWK": "kwacha malawite",
    "MXN": "peso mexicain", "MYR": "ringgit malaisien", "MZN": "metical mozambicain",
    "NAD": "dollar namibien", "NGN": "naira nigérian", "NIO": "córdoba nicaraguayen",
    "NOK": "couronne norvégienne", "NPR": "roupie népalaise",
    "NZD": "dollar néo-zélandais", "OMR": "rial omanais", "PAB": "balboa panaméen",
    "PEN": "sol péruvien", "PGK": "kina papouan", "PHP": "peso philippin",
    "PKR": "roupie pakistanaise", "PLN": "zloty polonais", "PYG": "guarani paraguayen",
    "QAR": "rial qatari", "RON": "leu roumain", "RSD": "dinar serbe",
    "RUB": "rouble russe", "RWF": "franc rwandais", "SAR": "riyal saoudien",
    "SBD": "dollar des îles Salomon", "SCR": "roupie seychelloise",
    "SDG": "livre soudanaise", "SEK": "couronne suédoise", "SGD": "dollar de Singapour",
    "SHP": "livre de Sainte-Hélène", "SLE": "leone sierra-léonais",
    "SOS": "shilling somalien", "SRD": "dollar surinamais",
    "SSP": "livre sud-soudanaise", "STN": "dobra santoméen", "SYP": "livre syrienne",
    "SZL": "lilangeni swazi", "THB": "baht thaïlandais", "TJS": "somoni tadjik",
    "TMT": "manat turkmène", "TND": "dinar tunisien", "TOP": "pa'anga tongien",
    "TRY": "livre turque", "TTD": "dollar de Trinité-et-Tobago",
    "TWD": "dollar taïwanais", "TZS": "shilling tanzanien", "UAH": "hryvnia ukrainienne",
    "UGX": "shilling ougandais", "USD": "dollar des États-Unis",
    "UYU": "peso uruguayen", "UZS": "sum ouzbek", "VES": "bolívar vénézuélien",
    "VND": "dong vietnamien", "VUV": "vatu vanuatuan", "WST": "tala samoan",
    "XAF": "franc CFA d'Afrique centrale", "XCD": "dollar des Caraïbes orientales",
    "XCG": "florin caribéen", "XOF": "franc CFA d'Afrique de l'Ouest",
    "XPF": "franc Pacifique", "YER": "rial yéménite", "ZAR": "rand sud-africain",
    "ZMW": "kwacha zambien", "ZWG": "or zimbabwéen (ZiG)",
}
"""Libellé français de chaque devise, pour que l'orchestrateur affiche un code
qu'un humain peut relire et démentir."""

LIMITES: list[str] = [
    "La devise retenue est celle du pays, pas celle qu'une plateforme marchande "
    "sait servir. AliExpress exclut toute ligne de prix libellée dans une autre "
    "devise que celle demandée : sur un marché dont la monnaie n'est pas servie, "
    "la collecte revient vide. Le repli est d'imposer USD ou EUR.",
    "Plusieurs marchés circulent de fait en devise étrangère à côté de leur "
    "monnaie nationale. La table retient une seule devise d'affichage : les prix "
    "réellement pratiqués dans l'autre monnaie ne sont pas couverts.",
    f"Table vérifiée le {DATE_VALIDITE} et jamais interrogée en ligne. Une "
    f"réforme monétaire postérieure à cette date n'y figure pas.",
]


def resoudre_devise(geo: str) -> dict:
    """Détermine la devise d'étude d'un marché.

    Args:
        geo: Code pays ISO 3166-1 alpha-2, casse indifférente.

    Returns:
        Le résultat sérialisable : code pays, devise, nom et limites.

    Raises:
        KeyError: Si le pays est absent de la table. Aucun défaut n'est servi :
            un benchmark de prix libellé dans une devise devinée serait faux
            sans qu'aucun contrôle aval ne puisse le détecter.
    """
    code_pays = geo.strip().upper()
    if code_pays not in DEVISE_PAR_PAYS:
        raise KeyError(code_pays)

    devise = DEVISE_PAR_PAYS[code_pays]
    return {
        "geo": code_pays,
        "devise": devise,
        "nom": NOMS_DEVISES.get(devise, devise),
        "source": "table",
        "date_validite": DATE_VALIDITE,
        "limites": LIMITES,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _analyser_arguments() -> argparse.Namespace:
    """Déclare et lit les arguments de la ligne de commande.

    Returns:
        Les arguments analysés.
    """
    parseur = argparse.ArgumentParser(
        description=(
            "Détermine la devise d'étude d'un marché, à partir de son code pays. "
            "Émet un objet JSON sur stdout."
        ),
        epilog="Exemple : python devise_marche.py --geo US",
    )
    parseur.add_argument("--geo", required=True, help="Code pays ISO-2, ex. US.")
    return parseur.parse_args()


def main() -> int:
    """Point d'entrée de la ligne de commande.

    Returns:
        Le code de sortie du processus.
    """
    arguments = _analyser_arguments()
    try:
        resultat = resoudre_devise(arguments.geo)
    except KeyError as erreur:
        print(
            f"Pays « {erreur.args[0]} » absent de la table des devises. Vérifie le "
            f"code ISO 3166-1 alpha-2, ou impose la devise explicitement.",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(resultat, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
