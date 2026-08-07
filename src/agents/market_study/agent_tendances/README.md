# Agent de collecte de tendances — Google Trends via Apify

Module autonome, exécutable en ligne de commande. À partir d'une fiche produit et
d'un marché cible, il dérive un mot-clé de recherche pivot, interroge Google
Trends sur deux horizons (12 mois et 5 ans) via l'actor Apify
`data_xplorer/google-trends-fast-scraper`, calcule des indicateurs quantitatifs
et retourne un objet structuré, validé, sérialisé en JSON sur `stdout`.

Aucune persistance, aucun serveur, aucune API HTTP, aucune interface, aucune
suite de tests : l'agent retourne un objet en mémoire et l'affiche.

---

## Installation

```bash
pip install -r requirements.txt
cp .env.example .env   # puis renseigner les deux clés
```

Python ≥ 3.11 requis.

### Variables d'environnement

| Variable | Usage |
|---|---|
| `ANTHROPIC_API_KEY` | Étapes LLM : contrôle qualité de la fiche + dérivation du mot-clé |
| `APIFY_TOKEN` | Appels à l'actor Apify (`APIFY_API_TOKEN` accepté en repli) |

Le `.env` est recherché depuis le répertoire courant puis dans les répertoires
parents.

---

## Usage

Le programme se lance **depuis l'intérieur du dossier** (imports absolus à plat) :

```bash
python main.py \
    --nom "JBL Endurance Peak 4 Open Ear" \
    --description "Écouteurs sans fil Open Ear pour le sport, autonomie 50h, IP68." \
    --categorie "electronics" \
    --geo FR \
    --langue fr \
    --verbose
```

- `stdout` : uniquement le `ResultatTendances` en JSON indenté (parsable tel quel).
- `stderr` : logs de progression, activés par `--verbose`.
- L'encodage UTF-8 de `stdout`/`stderr` est forcé au chargement de `config.py` :
  les accents des mots-clés sont préservés de bout en bout, y compris dans le
  payload envoyé à Apify.

Code de sortie : `0` en cas de succès, `1` si l'analyse n'a pas pu démarrer
(clé API manquante, dérivation du mot-clé impossible). Un échec de **collecte**
ne produit jamais d'exception : il retourne un résultat avec
`donnees_disponibles=false`.

---

## Organisation

```
config.py         constantes, .env, seuils heuristiques, logging
schemas.py        modèles Pydantic v2 (entrée / sortie)
keywords.py       contrôle qualité de la fiche + dérivation du mot-clé (LCEL)
trends_source.py  wrapper de l'actor Apify + gestion d'erreurs
indicators.py     calculs déterministes (aucun LLM)
agent.py          orchestration séquentielle de bout en bout
main.py           point d'entrée CLI
```

Sens des dépendances, sans cycle :
`config ← schemas ← {keywords, trends_source, indicators} ← agent ← main`.

---

## Schéma de sortie réel de l'actor

⚠️ **Le schéma documenté dans le README public de l'actor est faux sur deux
points.** La structure ci-dessous est celle constatée par run réel
(`data_xplorer/google-trends-fast-scraper`, build `stable` 2.8.6, run du
28/07/2026, mode `keyword`, geo `FR`) :

```jsonc
{
  "keyword": "écouteurs open ear",
  "timeframe": "today 12-m",
  "geo": "FR",
  "language": "fr-FR",                    // déduit du geo, non paramétrable
  "trends_url": "https://trends.google.com/trends/explore?geo=FR&q=...&hl=fr-FR",
  "timeline_data": {
    "écouteurs open ear": {               // ← clé = le mot-clé interrogé
      "2025-07-27": 0,
      "2025-08-03": 0,
      "2026-06-07": 100
    },
    "isPartial": {                        // ← carte parallèle, même jeu de clés
      "2025-07-27": false,
      "2026-07-26": true                  // dernière période en cours
    }
  },
  "region_data": [                        // vide si fetchRegionalData = false
    { "rank": 1, "region": "Île-de-France", "value": 100 }
  ],
  "data_granularity": "week"
}
```

Écarts avec le README de l'actor :

1. `timeline_data` n'est **pas** un dictionnaire plat `{"2023-W01": 75}` : c'est
   un dictionnaire à deux entrées, la série étant imbriquée sous une clé égale au
   mot-clé interrogé, aux côtés d'une carte `isPartial`.
2. Les clés temporelles sont des **dates ISO** (`2025-07-27`), pas des libellés
   de semaine ISO (`2023-W01`).
3. Le champ `language` est présent en sortie alors qu'aucun paramètre de langue
   n'existe en entrée.

Le parsing (`indicators.extraire_serie`) est donc écrit sur la structure
constatée : il repère la première valeur de type dictionnaire dont la clé n'est
pas `isPartial`, et **écarte les points marqués partiels** (dernière semaine en
cours, systématiquement sous-estimée).

Volumétrie observée : 53 points hebdomadaires sur `today 12-m`, 262 sur
`today 5-y`, un seul item de dataset par run.

### Ce que l'actor ne renvoie pas

Le mode `keyword` **ne renvoie ni requêtes associées, ni requêtes en progression
(*rising queries*), ni sujets associés**. Ces données n'existent que dans le mode
`trending`, qui répond à une tout autre question (top des recherches du moment
pour un pays) et dont les résultats ne sont pas rattachables à un mot-clé.

Conséquence sur le contrat de sortie, signalée explicitement dans `limites` :

| Champ | Valeur | Motif |
|---|---|---|
| `requetes_emergentes` | toujours `[]` | non fourni par la source |
| `sujets_associes` | toujours `[]` | non fourni par la source |
| `indicateurs.nb_breakout` | toujours `0` | dérivé des requêtes en progression |

Les modèles et la fonction `compter_breakout` sont conservés et conformes à la
spécification : si une source fournissant ces requêtes est branchée plus tard,
le calcul fonctionne sans modification. Aucun parsing spéculatif n'a été écrit
sur des noms de champs qui n'existent pas dans la réponse observée.

### Autres limites de la source

- **Un seul mot-clé par run.** `keyword` est une chaîne : la comparaison
  multi-termes est impossible. Aucune logique de comparaison n'est implémentée —
  les indices de deux runs distincts sont normalisés séparément et donc
  mathématiquement incomparables.
- **Aucun filtre de catégorie** en mode `keyword` : les homonymes du terme
  interrogé ne peuvent pas être écartés (« souris » informatique vs animal).
- **Pas de paramètre de langue** : l'interface est déduite du code pays.
  `ParametresMarche.langue` ne sert qu'à faire rédiger le mot-clé dans la bonne
  langue par le LLM.
- **Granularité hebdomadaire sur 5 ans** (et non mensuelle) : la pente annuelle
  et la saisonnalité sont calculées après agrégation mensuelle des semaines.

---

## Déroulé de l'agent

1. Contrôle qualité de la fiche produit → `alertes_qualite_input` (informatif,
   ne bloque jamais).
2. Dérivation du terme pivot et des termes de repli.
3. Collecte **12 mois** (`fetchRegionalData=true`).
4. **Pause de 20 s.**
5. Collecte **5 ans** (`fetchRegionalData=false`).
6. Si l'indice moyen 12 mois est `< SEUIL_INDICE_BRUIT` ou si la collecte 12 mois
   a échoué : passage au terme de repli suivant, retour à l'étape 3, dans la
   limite de 2 replis.
7. Calcul des indicateurs sur les données de la tentative retenue.

**Exécution strictement séquentielle.** Aucun parallélisme entre les appels à
Google Trends : deux requêtes simultanées depuis le même pool de proxies
déclenchent la détection anti-bot et provoquent un blocage reCAPTCHA.

Tentative retenue : la première dont l'indice moyen 12 mois dépasse le seuil de
bruit ; à défaut celle dont l'indice moyen est le plus élevé ; à défaut la
première ayant produit une série. `statuts_collecte` conserve **toutes** les
tentatives, y compris celles écartées.

### Robustesse

- **Proxies résidentiels explicites** (`RESIDENTIAL`). Si le groupe est
  indisponible sur le compte, l'erreur est remontée telle quelle dans
  `message_erreur`, jamais absorbée.
- **Succès silencieux détecté** : un run terminé en `SUCCEEDED` mais renvoyant un
  dataset vide, ou un item sans série temporelle, produit `succes=false` avec un
  message explicite — jamais une série de valeurs nulles. « Aucune tendance
  mesurée » et « tendance nulle mesurée » conduisent à des décisions opposées.
  À l'inverse, une série **présente et intégralement à zéro** est un succès :
  c'est une mesure, qui déclenche le repli de mot-clé et non une erreur.
- **Retries** : 2 tentatives par appel, attente de 5 s puis 20 s. Le payload est
  logué en UTF-8 avant chaque tentative.
- **Timeout de run** : 600 s.
- **Dégradation gracieuse** : si une seule des deux collectes réussit, les
  indicateurs calculables sont produits et les autres sont listés dans `limites`.
  Si les deux échouent, le résultat est retourné avec `indicateurs=null` et
  `donnees_disponibles=false`, sans exception.

---

## Indicateurs

| Indicateur | Calcul |
|---|---|
| `indice_moyen_12m` | moyenne arithmétique de la série 12 mois |
| `profil_mensuel_12m` | moyenne mensuelle **datée** de la série 12 mois, clés `AAAA-MM` triées chronologiquement. À ne pas confondre avec `saisonnalite.indice_par_mois`, qui regroupe les mois **calendaires** sur 5 ans. Les mois d'extrémité de la fenêtre sont partiels (moins de semaines agrégées) |
| `momentum_90j` | `moyenne(90 derniers jours) / moyenne(90 jours précédents) - 1` ; `null` si < 180 jours d'historique ou si la fenêtre de référence est nulle |
| `pente_annuelle_5ans` | `numpy.polyfit` degré 1 sur la série agrégée en mois, exprimée en points d'indice **par an** ; `null` sous 12 mois d'historique |
| `volatilite` | écart-type / moyenne (coefficient de variation) sur la série 5 ans |
| `saisonnalite` | moyenne par mois calendaire sur 5 ans ; `amplitude = (max - min) / moyenne` ; `null` si les 12 mois ne sont pas tous couverts |
| `nb_breakout` | requêtes en progression valant « Breakout » ou ≥ 5000 % (voir *Ce que l'actor ne renvoie pas*) |
| `concentration_geo` | top 5 des zones de `region_data`, `part = valeur_zone / somme_valeurs` |
| `signal_effet_de_mode` | pic historique daté de plus de 12 mois **et** indice actuel (moyenne des 90 derniers jours) < 30 % du pic |

### Seuils heuristiques

⚠️ **Aucun de ces seuils n'est validé empiriquement.** Ils n'ont fait l'objet
d'aucune calibration statistique sur un échantillon de référence et sont
ajustables dans `config.py` sans toucher au reste du code.

| Constante | Valeur | Rôle |
|---|---|---|
| `SEUIL_INDICE_BRUIT` | 5 | en dessous, la série 12 mois est traitée comme du bruit → repli de mot-clé |
| `NB_REPLIS_MAX` | 2 | nombre maximal de termes de repli essayés |
| `SEUIL_PENTE_POSITIVE` | +2 pts/an | seuil de qualification « croissance » |
| `SEUIL_PENTE_NEUTRE` | ±1 pt/an | pente considérée comme plate |
| `SEUIL_PENTE_NEGATIVE` | −2 pts/an | seuil de qualification « déclin » |
| `SEUIL_INDICE_MOYEN_FAIBLE` | 20 | indice moyen bas (candidat « émergent ») |
| `SEUIL_INDICE_MOYEN_ELEVE` | 40 | indice moyen haut (candidat « maturité ») |
| `SEUIL_MOMENTUM_EMERGENT` | 0,5 | +50 % entre les deux fenêtres de 90 jours |
| `SEUIL_VOLATILITE_ELEVEE` | 0,6 | coefficient de variation « élevé » |
| `SEUIL_ANCIENNETE_PIC_MOIS` | 12 | ancienneté du pic déclenchant la suspicion d'effet de mode |
| `RATIO_EFFONDREMENT_MODE` | 0,3 | indice actuel < 30 % du pic |

Classification de `profil_courbe`, dans cet ordre : `effet_de_mode` →
`emergent` → `croissance` → `maturite` → `declin` → `indetermine`.

---

## Coût observé par run

Modèle de facturation de l'actor depuis le 28/06/2026 : **pay-per-event**.

| Événement | Prix unitaire | Occurrences par run |
|---|---|---|
| `apify-actor-start` | 0,02 $ par Go de mémoire (min. 1) | 1 (l'actor tourne en 256 Mo → 1 événement) |
| `apify-default-dataset-item` | 0,0005 $ à 0,002 $ selon le plan | 1 |

**Coût constaté : 0,02 $ par run** (`usage_total_usd` relevé sur les runs réels),
la part « résultat » étant marginale devant les frais de démarrage.

| Scénario | Runs | Coût Apify |
|---|---|---|
| Nominal (aucun repli) | 2 | ≈ 0,04 $ |
| 1 repli | 4 | ≈ 0,08 $ |
| 2 replis (maximum) | 6 | ≈ 0,12 $ |

Durée de run observée : 5 à 45 s (12 mois avec régional : ~25-45 s ; 5 ans sans
régional : ~5-10 s). Durée totale d'une analyse nominale : ~1 min, pause de 20 s
comprise ; ~6 min dans le pire cas avec deux replis et des retries.

Coût LLM : deux appels `claude-haiku-4-5-20251001` à sortie courte par analyse,
négligeable devant le coût Apify.

---

## Limites méthodologiques

Systématiquement injectées dans le champ `limites` du résultat :

1. L'indice Google Trends est **relatif (0–100)** : il ne représente ni un volume
   de recherche, ni une taille de marché. Le module ne produit **aucune**
   affirmation de volume, de taille de marché ou de chiffre d'affaires.
2. Les valeurs sont normalisées **par requête** : deux exécutions distinctes ne
   sont pas comparables entre elles.
3. Les données sont **échantillonnées** : les résultats ne sont pas strictement
   reproductibles d'un appel à l'autre.
4. `profil_courbe` est une **heuristique**, pas une méthode statistiquement
   validée.
5. Aucun filtre de catégorie n'est disponible : les homonymes ne peuvent pas être
   écartés.
6. **L'absence de données de tendance n'est pas un signal négatif** sur le
   potentiel du produit.

S'y ajoutent, selon le run : l'indisponibilité de `requetes_emergentes` /
`sujets_associes` / `nb_breakout`, l'agrégation hebdomadaire → mensuelle, la
non-paramétrabilité de la langue, les collectes échouées, les indicateurs non
calculables, le maintien sous le seuil de bruit, et — point important —
**la perte de l'attribut différenciant** lorsqu'un repli a été appliqué : les
indicateurs décrivent alors la catégorie générique et non le segment spécifique
du produit.
