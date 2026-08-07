# `agent_restitution/` — F7 : rapport final d'étude de marché

**Dernier maillon CLI du pipeline.** Il ne collecte rien et n'analyse rien : il
transforme les sorties JSON des agents d'analyse en un **rapport d'étude de
marché professionnel en Markdown** et en un **résumé exécutif d'une page**,
lisibles par un décideur non technique.

Il consomme la sortie de F5 (**requise**) et, en option, celles de F3, F4 et F6.

> La page React, l'historique et l'export PDF sont **hors périmètre** de ce
> module : il produit le rapport Markdown maître et son résumé.

---

## 1. Place dans le pipeline

```
agent_insights_consommateurs (F3) ─┐
agent_analyse_concurrentielle (F4) ┼─► agent_recommandations_strategiques (F5) ─┐
tendances (collecteur) ────────────┘                                            │
                                                          agent_plc (F6) ───────┤
                                                                                ▼
                                                              agent_restitution (F7)
                                                                    │        │
                                                       rapport_etude.md   resume_executif.md
```

---

## 2. Usage

```bash
python main.py \
    --recommandations ../agent_recommandations_strategiques/output.json \
    [--insights ../agent_insights_consommateurs/output.json] \
    [--concurrence ../agent_analyse_concurrentielle/output.json] \
    [--plc ../agent_plc/output.json] \
    [--rapport rapport_etude.md] [--resume resume_executif.md] \
    [--langue-analyse fr] [--sortie output.json] [--stdout] [--verbose]
```

| Argument | Défaut | Rôle |
|---|---|---|
| `--recommandations` | — | **Requis.** Sans verdict ni dossier, il n'y a pas de rapport à écrire |
| `--rapport` | `rapport_etude.md` | Chaîne vide = fichier non produit |
| `--resume` | `resume_executif.md` | Chaîne vide = fichier non produit |
| `--sortie` | `output.json` | Métadonnées `ResultatRestitution` ; chaîne vide = aucun fichier |

| Code de sortie | Signification |
|---|---|
| `0` | Succès |
| `1` | Erreur imprévue |
| `2` | Sortie F5 absente ou inexploitable, ou produits divergents |

**`stdout` reste du JSON pur** : ce sont les métadonnées et les contrôles. Les
documents ne sortent que dans leurs fichiers Markdown dédiés.

**Prérequis** : `ANTHROPIC_API_KEY` (voir `.env.example`). Aucun jeton Apify.

---

## 3. Principe fondateur — restituer sans réinterpréter

Trois garanties structurent tout le module :

1. **Aucun chiffre absent des entrées.** Chaque nombre du rapport est confronté
   à une liste blanche numérique ; une phrase portant un nombre inconnu est
   retirée.
2. **Le verdict et la phase sont recopiés tels quels.** Jamais adoucis, jamais
   reformulés en substance : si le verdict est « indéterminé », le titre de la
   section dit « indéterminé ».
3. **Les conditions de bascule du verdict sont recalculées par le code.** Elles
   ne sont jamais recopiées du texte libre de F5.

> ⚠️ Le modèle **rédige des transitions et des lectures. Il ne produit aucune
> donnée.** Tout ce qui est chiffré, tabulaire ou structurel est injecté par le
> code depuis les JSON d'entrée.

---

## 4. Le gabarit du rapport

| # | Section | Contenu | Source | Narratif |
|---|---|---|---|---|
| — | En-tête | Titre, produit, marché, date, portée, avertissement de méthode | code | non |
| 1 | Synthèse exécutive | Verdict + fiabilité, faits clés, 3 recommandations majeures, risque principal, **réserves majeures** | F5 | oui |
| 2 | Verdict de potentiel : *mot du verdict* | Grille des 5 critères, règle littérale, **bascules calculées par le code**, données à compléter | F5 | oui |
| 3 | Phase de cycle de vie | Phase, incertitude, signaux, recommandations de phase — ou **encart standard** de non-détermination | F6 | oui |
| 4 | Demande observée | Indicateurs de recherche + rappel d'interprétation | F5 (écho Tendances) | oui |
| 5 | Besoins et attentes exprimés | Besoins, attentes, top 5 des difficultés avec **1 extrait chacune**, sentiment par source, écarts entre sources | F3 (à défaut : écho F5) | oui |
| 6 | Paysage concurrentiel | Intensité, top 8 concurrents, benchmark par source et devise, portée régionale, standards, angles peu exploités | F4 (à défaut : écho F5) | oui |
| 7 | Recommandations | P1 → P3 en tableaux + positionnement prix | F5 (+ F6) | non |
| 8 | Opportunités et risques | Opportunités et conditions de capture ; risques, gravité, atténuation | F5 | non |
| 9 | Annexe | Sources et volumes, période, méthode en 10 points, **limites consolidées verbatim**, hypothèses, glossaire | toutes | non |

Chaque section porte, en fin de bloc, un commentaire HTML de traçabilité
invisible au rendu :

```html
<!-- sources: recommandations.verdict_potentiel.grille; simulation_bascules (code) -->
```

---

## 5. La liste blanche numérique

Elle est alimentée par deux sources, et par elles seules :

1. les **fichiers d'entrée**, parcourus récursivement — valeurs numériques comme
   nombres présents dans les textes (justifications, commentaires) ;
2. les **blocs générés par le code** — tableaux, badges, encarts —, dont les
   nombres viennent des entrées mais sous une autre écriture.

Pour chaque valeur, les **variantes admises** sont générées : arrondis à 0, 1 et
2 décimales, valeur absolue, et pour tout ratio de module ≤ 1, son expression en
pourcentage (`-0,6818…` → `-68,2 %`). L'appariement tolère un écart de
`TOLERANCE_ARRONDI_PCT = 0,1` point.

Un nombre du rapport qui n'y figure pas n'a pu être produit que par le modèle :
la phrase qui le porte est retirée, et `nb_nombres_retires` est incrémenté. Les
tableaux, générés par le code, sont réputés conformes — ils sont contrôlés quand
même, et un écart y signalerait un défaut de la liste blanche, pas une invention.

**Contrôle vérifié** — un narratif fabriqué contenant des chiffres inventés :

```
entrée   : « Le score total est de 5 sur 10 […] Le marché espagnol pèse
             847 millions d'euros et croît de 12,4 % par an. […] »
           « Les avis analysés font état d'une part de marché de 18 % […] »
sortie   : « Le score total est de 5 sur 10, ce qui place le produit en zone
             d'incertitude. La différenciation reste le seul levier de bascule. »
compteurs: 2 nombres hors liste blanche, 1 terme interdit, 47 % du narratif
           retiré → régénération de la section déclenchée
```

Une **seule régénération** est tentée par section lorsque plus de 30 % du
narratif a été retiré ; au-delà, la section est réduite à ses tableaux avec un
encart « lecture narrative indisponible ».

---

## 6. La simulation des bascules de verdict

L'audit du run n°1 a montré que le texte libre de F5 annonçait des bascules
**incompatibles avec sa propre règle** : trois critères y étaient présentés comme
faisant passer le verdict à « positif », alors que la règle l'interdit tant qu'un
critère est noté 0.

Ce module rejoue donc la règle sur **toutes les mutations mono-critère** de la
grille — chaque critère porté à 1 puis à 2, les critères non évaluables rendus
évaluables — et n'affiche que les mutations qui **changent réellement** le
verdict, en ne retenant pour chaque critère que la moins exigeante.

Les seuils sont relus par expression régulière dans `regle_appliquee`, l'énoncé
littéral publié par F5. Si la relecture échoue, des constantes locales prennent
le relais, le fait est publié en hypothèse, et le besoin est remonté :

> Un bloc structuré `verdict_potentiel.parametres_regle` dans la sortie de F5
> supprimerait cette fragilité.

**Résultat sur les fixtures du run n°1** — grille `demande=1, intensite=1,
differenciation=0, adequation=1, viabilite_prix=2`, score 5, verdict
`indetermine` :

| Mutation | Score | Verdict | Bascule ? |
|---|---|---|---|
| demande → 2 | 6 | `indetermine` (un critère reste à 0) | non |
| intensite → 2 | 6 | `indetermine` | non |
| **differenciation → 1** | **6** | **`positif`** | **oui** |
| adequation → 2 | 6 | `indetermine` | non |
| viabilite_prix → 1 | 4 | `indetermine` | non |

**Seule la bascule « différenciation » est publiée.** Les trois conclusions de
bascule contradictoires du texte libre de F5 sont retirées de
`donnees_a_completer`, et l'écart est tracé dans `statuts_analyse`.

---

## 7. Formulations contrôlées

Les règles de formulation sont injectées dans tous les prompts **et** vérifiées
sur le texte produit :

- toujours « les avis et discussions analysés », « dans le corpus collecté » —
  **jamais** de généralisation à une population ;
- les absences restent relatives au corpus : « non observé dans les 31 annonces
  et 6 pages collectées » ;
- **termes interdits hors annexe** : « part de marché », « volume de demande »,
  « taille du marché », « les consommateurs veulent »… ;
- **jargon interne proscrit** dans le corps : noms d'agents, « référence »,
  identifiants techniques, « pain point », « unité » ;
- réponse d'abord, aucun superlatif promotionnel, aucun adoucissement.

La détection est **tolérante aux négations** : « aucune part de marché ne peut en
être déduite » est précisément l'avertissement que le rapport doit porter, pas
une affirmation interdite.

### Substitutions déterministes sur les textes amont

Les textes des analyses amont injectés dans le corps du rapport subissent trois
traitements de code, comptés et publiés en statut :

| Traitement | Exemple |
|---|---|
| Retrait des références techniques | `… (9,3 %, intensité 2,57, ref: insights.pain_points[1])` → `… (9,3 %, intensité 2,57)` |
| Retrait du préfixe d'agent | `Agent F3 (Insights consommateur) : Collecter…` → `Collecter…` |
| Suppression des généralisations | `Les consommateurs recherchent…` → `Les personnes dont les avis ont été analysés recherchent…` |
| Traduction du vocabulaire interne | `pain points` → `difficultés rapportées` |

Ces substitutions **ne s'appliquent jamais aux limites**, restituées verbatim en
annexe, ni aux extraits, conservés dans leur langue d'origine, ni à l'énoncé
littéral de la règle de verdict, qui doit rester auditable.

> **Défaut amont signalé.** Les descriptions de besoins et d'attentes produites
> par F3 emploient massivement « les consommateurs … », c'est-à-dire exactement
> la généralisation que le rapport s'interdit. La substitution ci-dessus la
> neutralise à la restitution ; **le correctif appartient à F3**.

---

## 8. Les extraits (verbatims)

Pour chacune des 5 premières difficultés rapportées, **le code** — jamais le
modèle — sélectionne un extrait : le plus court tenant sous 200 caractères, à
défaut le plus court tronqué à la limite d'un mot. La langue d'origine est
conservée (un extrait traduit n'est plus un verbatim), et l'identifiant de
l'unité de corpus figure en commentaire HTML.

```markdown
> « Stop ash its not worth it bro, speaking from experience took me 10months to recover. »
<!-- extrait: rd-c-opjfd76 (reddit) -->
```

---

## 9. Matrice de dégradation

| Situation | Comportement |
|---|---|
| F3 absente | Section 5 construite depuis l'écho `dossier_synthese.consommateur`, marquée `degradee`, **avec mention explicite** ; pas d'extraits |
| F4 absente | Section 6 construite depuis l'écho `dossier_synthese.concurrence`, marquée `degradee`, **avec mention explicite** |
| F6 absente ou phase non déterminée | Section 3 remplacée par un **encart standard**, motif recopié |
| Chaîne de rédaction en échec après reprise | Section réduite à ses tableaux + encart « lecture narrative indisponible » + statut |
| F5 inexploitable | **Seul cas d'arrêt** : code 2 |

Une entrée manquante ne produit **jamais** une section silencieusement vide
(exigence F7.3). La mention est vérifiée par la post-validation et publiée dans
`controles.mentions_etude_partielle`.

---

## 10. Sortie — `ResultatRestitution`

| Champ | Contenu |
|---|---|
| `sections_produites[]` | Par section : `entrees_utilisees`, `badge_confiance`, `nb_mots_narratif`, `degradee`, `refs_sources` |
| `controles` | `nb_nombres_verifies`, `nb_nombres_retires`, `verdict_conforme`, `phase_conforme`, `bascules_recalculees`, `termes_interdits_retires`, `mentions_etude_partielle` |
| `chemin_rapport` / `chemin_resume` | Fichiers écrits, ou `null` |
| `synthese_executive` | Recopie du narratif de synthèse |
| `confiance_globale` | **Minimum** des confiances des entrées utilisées |

Sur un run sain : `nb_nombres_retires == 0`, `verdict_conforme == true`,
post-validation silencieuse.

---

## 11. Coûts et durée observés

Mesuré le 06/08/2026 sur le run *ashwagandha-supplement-ES* (F5 verdict
`indetermine`, F6 exécutée avec `--forcer`) :

| Scénario | Appels LLM | Coût estimé | Durée | Nombres vérifiés / retirés |
|---|---|---|---|---|
| Quatre entrées (F5 + F3 + F4 + F6) | 6 | ≈ 0,10 $ | 75 s | 604 / **0** |
| F5 seule — toutes sections dégradées | 5 | ≈ 0,08 $ | 57 s | 496 / **0** |

Modèle : `claude-sonnet-4-5-20250929`, température 0, une chaîne par section
narrative. Préparation, assemblage et validation sont du **code pur**. Tarif
saisi à la main dans `config.py`, à vérifier avant tout usage budgétaire.

---

## 12. Ce que ce module ne fait pas

- Aucune **analyse nouvelle** : ni re-scoring, ni re-classification, ni
  re-calcul de sentiment ou de benchmark, ni recommandation inédite.
- Aucun recours à une **connaissance externe** aux fichiers d'entrée.
- Aucun **graphique image**, HTML, PDF, DOCX ni React : la sortie documentaire
  est du **Markdown pur** (tableaux Markdown compris).
- Aucune **collecte**, aucun appel réseau hors API Anthropic, aucune conversion
  de devises, aucune persistance hors fichiers de sortie.
- Aucun **adoucissement éditorial** : si le verdict est « indéterminé », le
  rapport titre « indéterminé » ; si une donnée est de fiabilité faible, le badge
  le dit.
