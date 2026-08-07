"""Détermination de la langue d'étude d'un marché.

Utilitaire d'orchestration, **pas un agent de collecte** : il ne produit pas le
socle de sortie commun décrit dans AGENTS.md et n'appelle aucune source externe.
Il traduit un code pays ISO 3166-1 alpha-2 en la langue principale du pays,
par table déterministe.

Il répond à un défaut constaté : une étude lancée sur `--geo ES` avec la langue
par défaut `fr` fait dériver un mot-clé français, interrogé sur le Google Trends
espagnol, où il n'a aucun volume — la collecte revient vide sans que rien ne
soit techniquement en échec.

CE QUE LA TABLE RETIENT — la langue principale du pays, c'est-à-dire celle dans
laquelle sa population écrit au quotidien. Quand la langue officielle ou
cérémonielle diffère de la langue effectivement écrite, c'est l'écrite qui est
retenue : c'est la seule qui produise des requêtes ayant du volume.

CE QU'ELLE NE FAIT PAS — elle ne distingue pas la langue **parlée** de la langue
**tapée dans un moteur de recherche**. Ces deux langues divergent sur plusieurs
marchés : au Nigéria, en Inde, au Pakistan, aux Philippines et dans une grande
partie de l'Afrique francophone et anglophone, les recherches en ligne se font
massivement dans la langue de scolarisation, pas dans la langue maternelle. La
table sert la langue principale du pays, comme demandé ; le repli sur ces
marchés est d'imposer la langue à la main, par l'option `-Langue` de
l'orchestrateur. Les cas connus sont énumérés dans `MARCHES_A_ARBITRER`.

Un pays multilingue n'est jamais étendu d'office à deux études : une seule
langue est retenue. Deux langues restent possibles, mais sur décision explicite,
par l'option `-Langues fr,ar` de l'orchestrateur.

Sortie sur `stdout` :

```json
{"geo": "MA",
 "codes": ["ar"],
 "langues": [{"code": "ar", "nom": "arabe", "role": "principale",
              "justification": "…"}],
 "source": "table",
 "date_validite": "2026-08-06",
 "limites": ["…"]}
```

Codes de sortie : `0` succès, `1` pays inconnu de la table, `2` usage argparse.
Un pays inconnu n'est jamais remplacé par un défaut : une étude lancée sur une
langue devinée à tort ne vaut rien.
"""

from __future__ import annotations

import argparse
import json
import sys

# --------------------------------------------------------------------------- #
# Correctif d'encodage — même motif que les modules du pipeline : sans lui, un
# nom de langue accentué écrit sur stdout devient illisible en cp1252.
# --------------------------------------------------------------------------- #
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

DATE_VALIDITE: str = "2026-08-06"
"""Date à laquelle la table a été vérifiée."""

ROLE_PRINCIPALE: str = "principale"

# --------------------------------------------------------------------------- #
# Table pays → langue (ISO 3166-1 alpha-2 → ISO 639-1)
# --------------------------------------------------------------------------- #
# Même périmètre de pays que `devise_marche.py` : les deux tables se contrôlent
# l'une l'autre, un pays présent dans l'une et absent de l'autre est un défaut.

LANGUE_PAR_PAYS: dict[str, str] = {
    # --- Europe ----------------------------------------------------------- #
    "AD": "ca", "AL": "sq", "AT": "de", "AX": "sv", "BA": "bs",
    "BE": "nl", "BG": "bg", "BY": "ru", "CH": "de", "CY": "el",
    "CZ": "cs", "DE": "de", "DK": "da", "EE": "et", "ES": "es",
    "FI": "fi", "FO": "fo", "FR": "fr", "GB": "en", "GG": "en",
    "GI": "en", "GR": "el", "HR": "hr", "HU": "hu", "IE": "en",
    "IM": "en", "IS": "is", "IT": "it", "JE": "en", "LI": "de",
    "LT": "lt", "LU": "fr", "LV": "lv", "MC": "fr", "MD": "ro",
    "ME": "sr", "MK": "mk", "MT": "mt", "NL": "nl", "NO": "no",
    "PL": "pl", "PT": "pt", "RO": "ro", "RS": "sr", "RU": "ru",
    "SE": "sv", "SI": "sl", "SJ": "no", "SK": "sk", "SM": "it",
    "UA": "uk", "VA": "it", "XK": "sq",
    # --- Amériques -------------------------------------------------------- #
    "AG": "en", "AI": "en", "AR": "es", "AW": "nl", "BB": "en",
    "BL": "fr", "BM": "en", "BO": "es", "BQ": "nl", "BR": "pt",
    "BS": "en", "BZ": "en", "CA": "en", "CL": "es", "CO": "es",
    "CR": "es", "CU": "es", "CW": "nl", "DM": "en", "DO": "es",
    "EC": "es", "FK": "en", "GD": "en", "GF": "fr", "GL": "kl",
    "GP": "fr", "GT": "es", "GY": "en", "HN": "es", "HT": "ht",
    "JM": "en", "KN": "en", "KY": "en", "LC": "en", "MF": "fr",
    "MQ": "fr", "MS": "en", "MX": "es", "NI": "es", "PA": "es",
    "PE": "es", "PM": "fr", "PR": "es", "PY": "es", "SR": "nl",
    "SV": "es", "SX": "nl", "TC": "en", "TT": "en", "US": "en",
    "UY": "es", "VC": "en", "VE": "es", "VG": "en", "VI": "en",
    # --- Afrique ---------------------------------------------------------- #
    "AO": "pt", "BF": "fr", "BI": "fr", "BJ": "fr", "BW": "en",
    "CD": "fr", "CF": "fr", "CG": "fr", "CI": "fr", "CM": "fr",
    "CV": "pt", "DJ": "fr", "DZ": "ar", "EG": "ar", "EH": "ar",
    "ER": "ti", "ET": "am", "GA": "fr", "GH": "en", "GM": "en",
    "GN": "fr", "GQ": "es", "GW": "pt", "KE": "sw", "KM": "fr",
    "LR": "en", "LS": "en", "LY": "ar", "MA": "ar", "MG": "mg",
    "ML": "fr", "MR": "ar", "MU": "fr", "MW": "en", "MZ": "pt",
    "NA": "en", "NE": "fr", "NG": "en", "RE": "fr", "RW": "rw",
    "SC": "fr", "SD": "ar", "SH": "en", "SL": "en", "SN": "fr",
    "SO": "so", "SS": "en", "ST": "pt", "SZ": "en", "TD": "fr",
    "TG": "fr", "TN": "ar", "TZ": "sw", "UG": "en", "YT": "fr",
    "ZA": "en", "ZM": "en", "ZW": "en",
    # --- Asie et Moyen-Orient --------------------------------------------- #
    "AE": "ar", "AF": "fa", "AM": "hy", "AZ": "az", "BD": "bn",
    "BH": "ar", "BN": "ms", "BT": "dz", "CN": "zh", "GE": "ka",
    "HK": "zh", "ID": "id", "IL": "he", "IN": "hi", "IQ": "ar",
    "IR": "fa", "JO": "ar", "JP": "ja", "KG": "ky", "KH": "km",
    "KP": "ko", "KR": "ko", "KW": "ar", "KZ": "kk", "LA": "lo",
    "LB": "ar", "LK": "si", "MM": "my", "MN": "mn", "MO": "zh",
    "MV": "dv", "MY": "ms", "NP": "ne", "OM": "ar", "PH": "tl",
    "PK": "ur", "PS": "ar", "QA": "ar", "SA": "ar", "SG": "en",
    "SY": "ar", "TH": "th", "TJ": "tg", "TL": "pt", "TM": "tk",
    "TR": "tr", "TW": "zh", "UZ": "uz", "VN": "vi", "YE": "ar",
    # --- Océanie ---------------------------------------------------------- #
    "AS": "en", "AU": "en", "CC": "en", "CK": "en", "CX": "en",
    "FJ": "en", "FM": "en", "GU": "en", "KI": "en", "MH": "en",
    "MP": "en", "NC": "fr", "NF": "en", "NR": "en", "NU": "en",
    "NZ": "en", "PF": "fr", "PG": "en", "PN": "en", "PW": "en",
    "SB": "en", "TK": "en", "TO": "to", "TV": "en", "VU": "bi",
    "WF": "fr", "WS": "sm",
    # --- Océan Indien ------------------------------------------------------ #
    "IO": "en",
}
"""Langue principale du pays, en ISO 639-1.

Plusieurs marchés sont réellement bilingues (Belgique, Suisse, Canada, Maroc,
Cameroun…). La table n'en retient qu'une : celle de la population la plus
nombreuse. Étudier l'autre segment linguistique se demande explicitement, par
`-Langues`.
"""

MARCHES_A_ARBITRER: dict[str, str] = {
    "IN": "Le hindi est la première langue maternelle, mais le commerce en ligne "
          "indien se cherche massivement en anglais. Envisager -Langue en.",
    "NG": "Aucune langue maternelle nigériane n'est la langue de recherche : "
          "l'anglais l'est, et c'est ce que sert la table.",
    "PK": "L'ourdou est la langue véhiculaire, l'anglais celle du commerce en "
          "ligne urbain. Envisager -Langue en.",
    "PH": "Le filipino est maternel, l'anglais domine la recherche marchande. "
          "Envisager -Langue en.",
    "MA": "Arabe retenu. Le français porte une part substantielle de la "
          "recherche marchande marocaine : envisager -Langues ar,fr.",
    "DZ": "Arabe retenu, même réserve qu'au Maroc pour le français.",
    "TN": "Arabe retenu, même réserve qu'au Maroc pour le français.",
    "BE": "Néerlandais retenu (population la plus nombreuse). Le segment "
          "francophone est un marché distinct : -Langues nl,fr.",
    "CH": "Allemand retenu. Segments francophone et italophone distincts.",
    "CA": "Anglais retenu. Le Québec est un marché distinct : -Langues en,fr.",
    "ZA": "Anglais retenu : c'est la langue de la recherche en ligne, devant "
          "l'afrikaans et le zoulou.",
    "KE": "Swahili retenu ; l'anglais est très présent en recherche marchande.",
    "TZ": "Swahili retenu ; même réserve qu'au Kenya.",
    "LU": "Français retenu ; l'allemand et le luxembourgeois coexistent.",
    "PY": "Espagnol retenu ; le guarani est co-officiel mais peu écrit en ligne.",
    "BO": "Espagnol retenu ; quechua et aymara sont peu écrits en ligne.",
    "ET": "Amharique retenu ; l'oromo est la première langue maternelle mais "
          "l'amharique domine l'écrit.",
}
"""Marchés où le choix de la table est contestable, et pourquoi.

Émis dans les limites de la sortie quand le pays y figure : le doute doit
apparaître à l'écran au moment de lancer l'étude, pas dans une note de bas de
page que personne ne relit.
"""

NOMS_LANGUES: dict[str, str] = {
    "am": "amharique", "ar": "arabe", "az": "azéri",
    "bg": "bulgare", "bi": "bichelamar", "bn": "bengali", "bs": "bosnien",
    "ca": "catalan", "cs": "tchèque", "da": "danois", "de": "allemand",
    "dv": "maldivien", "dz": "dzongkha", "el": "grec", "en": "anglais",
    "es": "espagnol", "et": "estonien", "fa": "persan", "fi": "finnois",
    "fo": "féroïen", "fr": "français", "he": "hébreu", "hi": "hindi",
    "hr": "croate", "ht": "créole haïtien", "hu": "hongrois", "hy": "arménien",
    "id": "indonésien", "is": "islandais", "it": "italien", "ja": "japonais",
    "ka": "géorgien", "kk": "kazakh", "kl": "groenlandais", "km": "khmer",
    "ko": "coréen", "ky": "kirghize", "lo": "lao", "lt": "lituanien",
    "lv": "letton", "mg": "malgache", "mk": "macédonien", "mn": "mongol",
    "ms": "malais", "mt": "maltais", "my": "birman", "ne": "népalais",
    "nl": "néerlandais", "no": "norvégien", "pl": "polonais", "pt": "portugais",
    "ro": "roumain", "ru": "russe", "rw": "kinyarwanda", "si": "cingalais",
    "sk": "slovaque", "sl": "slovène", "sm": "samoan", "so": "somali",
    "sq": "albanais", "sr": "serbe", "sv": "suédois", "sw": "swahili",
    "tg": "tadjik", "th": "thaï", "ti": "tigrigna", "tk": "turkmène",
    "tl": "filipino", "to": "tongien", "tr": "turc", "uk": "ukrainien",
    "ur": "ourdou", "uz": "ouzbek", "vi": "vietnamien", "zh": "chinois",
}
"""Libellé français de chaque langue, pour que l'orchestrateur affiche un code
qu'un humain peut relire et démentir."""

LIMITES: list[str] = [
    "La table sert la langue principale du pays, pas nécessairement celle dans "
    "laquelle ses consommateurs TAPENT leurs recherches. Ces deux langues "
    "divergent sur les marchés où la scolarisation se fait dans une langue "
    "étrangère : un corpus vide sur un tel marché se corrige en imposant la "
    "langue à la main.",
    "Un pays multilingue n'est représenté que par une langue. Le segment "
    "linguistique écarté n'est pas couvert par l'étude, et son absence ne "
    "produit aucune alerte : elle est silencieuse par construction.",
    f"Table vérifiée le {DATE_VALIDITE} et jamais interrogée en ligne. Elle ne "
    f"repose sur aucune statistique d'usage mesurée.",
]

JUSTIFICATION_TABLE: str = (
    "Langue principale du pays, retenue par table déterministe : aucune "
    "estimation, aucun appel à un modèle de langue."
)


def resoudre_langue(geo: str) -> dict:
    """Détermine la langue d'étude d'un marché.

    Args:
        geo: Code pays ISO 3166-1 alpha-2, casse indifférente.

    Returns:
        Le résultat sérialisable. `codes` est une liste d'un seul élément : la
        forme est conservée pour que l'orchestrateur puisse itérer sur plusieurs
        langues lorsqu'elles lui sont imposées explicitement.

    Raises:
        KeyError: Si le pays est absent de la table. Aucun défaut n'est servi :
            lancer les six collecteurs sur une langue devinée à tort produit un
            corpus vide sans qu'aucun module ne soit techniquement en erreur.
    """
    code_pays = geo.strip().upper()
    if code_pays not in LANGUE_PAR_PAYS:
        raise KeyError(code_pays)

    code = LANGUE_PAR_PAYS[code_pays]
    limites = list(LIMITES)
    reserve = MARCHES_A_ARBITRER.get(code_pays)
    if reserve:
        limites.insert(0, f"Marché à arbitrer — {reserve}")

    return {
        "geo": code_pays,
        "codes": [code],
        "langues": [
            {
                "code": code,
                "nom": NOMS_LANGUES.get(code, code),
                "role": ROLE_PRINCIPALE,
                "justification": JUSTIFICATION_TABLE,
            }
        ],
        "justification": (
            f"{NOMS_LANGUES.get(code, code).capitalize()} retenu comme langue "
            f"principale de {code_pays}."
        ),
        "reserve": reserve,
        "source": "table",
        "date_validite": DATE_VALIDITE,
        "limites": limites,
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
            "Détermine la langue d'étude d'un marché, à partir de son code pays. "
            "Émet un objet JSON sur stdout."
        ),
        epilog="Exemple : python langues_marche.py --geo MA",
    )
    parseur.add_argument("--geo", required=True, help="Code pays ISO-2, ex. MA.")
    return parseur.parse_args()


def main() -> int:
    """Point d'entrée de la ligne de commande.

    Returns:
        Le code de sortie du processus.
    """
    arguments = _analyser_arguments()
    try:
        resultat = resoudre_langue(arguments.geo)
    except KeyError as erreur:
        print(
            f"Pays « {erreur.args[0]} » absent de la table des langues. Vérifie le "
            f"code ISO 3166-1 alpha-2, ou impose la langue explicitement.",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(resultat, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
