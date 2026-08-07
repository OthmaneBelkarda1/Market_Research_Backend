# `agent_recommandations_strategiques/` — Axe 3 : recommandations et verdict

Troisième agent d'**analyse** et point de convergence du pipeline. Il ne
collecte rien et ne relit aucune donnée brute de collecteur : ses entrées sont
les sorties JSON de F3 (insights consommateurs), F4 (analyse concurrentielle) et
du collecteur Tendances.

Il confronte trois faces du marché — **demande**, **consommateurs**, **offre** —
pour produire un diagnostic croisé, un **verdict de potentiel** argumenté, et
des recommandations priorisées, en distinguant strictement faits, hypothèses et
recommandations.

---

## 1. Place dans le pipeline

```
tendances (collecteur) ──────────────┐
agent_insights_consommateurs (F3) ───┼──►  agent_recommandations_strategiques (F5)
agent_analyse_concurrentielle (F4) ──┘                      │
                                                            ▼
                                          verdict_potentiel.declenche_plc
                                          → porte d'entrée du futur module PLC
```

**La classification de phase de cycle de vie est hors sujet ici.** Le mot
« PLC » n'apparaît que dans `declenche_plc` et sa documentation : cet agent dit
seulement si le sujet mérite d'être instruit plus loin.

---

## 2. Usage

```bash
python main.py \
    --insights ../agent_insights_consommateurs/output.json \
    --concurrence ../agent_analyse_concurrentielle/output.json \
    --tendances ../fixtures/tendances.json \
    [--langue-analyse fr] [--sortie output.json] [--stdout] [--verbose]
```

Les trois chemins sont optionnels ; au moins un est requis.

| Code de sortie | Signification |
|---|---|
| `0` | Succès — **y compris pour un verdict `negatif` ou `indetermine`** |
| `1` | Erreur imprévue |
| `2` | Aucune entrée fournie ou exploitable, ou produits divergents |

> ⚠️ **Un verdict défavorable n'est pas une erreur.** Un orchestrateur doit lire
> `verdict_potentiel.verdict`, jamais le code de sortie. Le verdict est aussi
> rappelé sur `stderr` en fin d'exécution, pour la lecture humaine.

`stdout` reste du **JSON pur**.

**Prérequis** : `ANTHROPIC_API_KEY` (voir `.env.example`). Aucun jeton Apify.

---

## 3. ⚠️ La règle de verdict est une hypothèse de travail

**Le « potentiel commercial » n'est défini ni dans le cahier des charges, ni
dans la spécification fonctionnelle générale.** C'est un arbitrage produit resté
ouvert. Ce module implémente donc une hypothèse conservatrice et auditable,
signalée dans chaque sortie par `statut_regle="hypothese_de_travail_a_valider"`.

### La grille — 5 critères, notés 0 / 1 / 2 ou `non_evaluable`

| Id | Critère | Question posée au modèle | Fondé sur |
|---|---|---|---|
| `demande` | Dynamique de la demande | Établie ou en croissance, sans dépendre d'un pic éphémère ? | Tendances |
| `intensite` | Intensité concurrentielle soutenable | Le niveau de concurrence laisse-t-il une place à un entrant ? | F4 |
| `differenciation` | Différenciation crédible | Des attributs distinctifs défendables ? | F4 |
| `adequation` | Adéquation aux besoins avérés | Répond à des pain points documentés ? | F3 ⨯ fiche produit |
| `viabilite_prix` | Viabilité prix | Un positionnement cohérent avec le benchmark est-il possible ? | F4 |

Barème : **0** = signal défavorable net, **1** = mitigé, **2** = favorable net.

### La règle — appliquée par le code, jamais par le modèle

```
si nb_criteres_evalues < 4                     → indetermine (d'office)
sinon si score ≥ 6 et aucun 0 et ≤ 1 non_eval  → positif
sinon si score ≤ 3 ou (demande=0 et differenciation=0) → negatif
sinon                                          → indetermine
```

Plus un **plafond appliqué par le code** : si Tendances signale un effet de mode
(`signal_effet_de_mode` levé ou `profil_courbe="effet_de_mode"`), le critère
`demande` est ramené à 1 au maximum, avec `plafonnement_applique="effet_de_mode"`
inscrit dans la note.

`declenche_plc` vaut vrai **uniquement** si `verdict == "positif"`.

Tous ces seuils vivent dans `config.py`. `regle_appliquee` publie l'énoncé
littéral **avec les seuils effectifs** : si vous les modifiez, la sortie le dit.

### Le partage des rôles

1. **Le modèle note** chaque critère, avec justification et fondements référencés.
2. **Le code corrige** : critère absent → non évaluable ; critère noté alors que
   toutes ses sources manquent → **forcé** non évaluable ; plafond effet de mode.
3. **Le code décide** : `appliquer_regle` est une fonction pure — aucun appel
   réseau, aucun état, aucun aléa.
4. **La post-validation recalcule** le verdict depuis la grille publiée : toute
   divergence est corrigée au profit du code et tracée en alerte.

---

## 4. Le dossier de synthèse : la condition de la traçabilité

`signaux.py` construit, **sans LLM**, un dossier compact borné. C'est le **seul**
contenu transmis aux chaînes LLM — les fichiers d'entrée complets ne les
atteignent jamais.

Chaque élément porte une `ref` stable, qui est à la fois son adresse de citation
et la clé de récupération de sa valeur exacte :

```
tendances.indicateurs.momentum_90j
insights.pain_points[2]
concurrence.benchmark_prix[amazon][EUR].mediane
concurrence.positionnement.angles_peu_exploites[0]
```

Conséquences directes :

- un fondement de type `fait` **doit** citer une `ref` du dossier ; sans ref
  valide, il est **retiré** par la post-validation ;
- `faits_cles.valeur` est **recopiée depuis le dossier**, écrasant toute valeur
  produite par le modèle ;
- le dossier est renvoyé intégralement dans la sortie (`dossier_synthese`), ce
  qui rend chaque citation vérifiable sans rouvrir les fichiers amont.

Bornes (dans `config.py`) : 8 pain points, 6 besoins, 6 attentes, 6 angles,
8 concurrents, 6 requêtes émergentes.

---

## 5. Adaptation au verdict

Le prompt de recommandations change selon le verdict calculé :

| Verdict | Comportement imposé |
|---|---|
| `positif` | Jeu complet de recommandations hiérarchisées, ton sobre — la règle reste une hypothèse. |
| `negatif` | La **première** recommandation produit est une recommandation de **non-lancement en P1**, argumentée par les critères défaillants. Pivots en P2/P3. Les autres domaines se réduisent à l'essentiel défendable. |
| `indetermine` | `donnees_a_completer` rempli avec précision — **quel agent relancer, quel signal manque, pourquoi il ferait basculer le verdict**. Recommandations limitées au « sans regret ». |

Deux filets posés par le code, indépendamment du modèle :

- **risque `effet_de_mode` obligatoire** dès que le drapeau est posé ;
- **risque `donnees`** ajouté dès qu'une entrée est absente ou dégradée :
  décider sur données incomplètes *est* un risque.

---

## 6. Lecture de la sortie

| Champ | Contenu |
|---|---|
| `dossier_synthese` | Écho intégral — le vocabulaire de citation de tous les fondements. |
| `diagnostic` | Convergences, **contradictions avec `lecture_prudente`**, lecture de marché, fenêtre d'opportunité. |
| `verdict_potentiel` | Verdict, `declenche_plc`, score, grille notée, `regle_appliquee`, `statut_regle`, confiance, conditions de réexamen. |
| `recommandations_produit` / `_marketing` | Priorité, horizon, impact, effort, risques associés, indicateurs de suivi mesurables. |
| `recommandation_prix` | Fourchettes **uniquement dans les devises du benchmark F4**, bornes vérifiées dans son étendue. `null` sans F4. |
| `recommandation_positionnement` | Segment cible, angle, promesse. |
| `opportunites`, `risques` | Ancrés, typés, avec conditions de capture / atténuation. |
| `donnees_a_completer` | Rempli si verdict indéterminé. |
| `faits_cles` | 5 à 10 données déterminantes ; `valeur` recopiée par le code. |
| `synthese_executive` | ≤ 15 lignes, structure imposée. |

**Une contradiction n'est jamais tranchée.** Quand la demande décline selon
Tendances mais que la pression publicitaire est forte selon F4, la
`lecture_prudente` expose les explications concurrentes et dit ce qu'il faudrait
observer pour départager. Choisir la lecture la plus flatteuse est explicitement
interdit par le prompt.

---

## 7. Organisation du code

```
config.py           → (aucune dépendance interne) constantes, GRILLE_CRITERES, seuils
schemas.py          → config
chargement.py       → config, schemas   lecture, cohérence, fraîcheur
signaux.py          → config, schemas   dossier de synthèse — SANS LLM
diagnostic.py       → config, schemas   diagnostic croisé (LLM)
potentiel.py        → config, schemas   notation (LLM) + RÈGLE DE VERDICT (code pur)
recommandations.py  → config, schemas   recommandations, opportunités/risques, restitution (LLM)
validation.py       → config, schemas, potentiel   post-validation — SANS LLM
agent.py            → tous les précédents
main.py             → config, schemas, agent
```

`validation.py` importe `potentiel.appliquer_regle` : c'est délibéré — le
recalcul du verdict doit passer par **exactement** la même fonction que le
calcul initial, sinon la garantie ne vaut rien.

---

## 8. Un seul niveau de modèle

`MODELE_SYNTHESE = "claude-sonnet-4-5-20250929"`, température 0, pour **toutes**
les chaînes. Écart assumé vis-à-vis de la convention « haiku » des collecteurs :
il n'existe ici aucune étape mécanique — diagnostic, notation, recommandations
et restitution relèvent toutes du jugement. Les extractions du dossier de
synthèse sont, elles, du code pur.

Identifiant **vérifié disponible le 05/08/2026**. La génération courante
`claude-sonnet-5` rejette toute `temperature` non par défaut (erreur 400) : elle
est incompatible avec l'exigence de température 0.

---

## 9. Dégradation

**Invariant : l'agent produit toujours un verdict et une sortie complète.**

| Situation | Comportement |
|---|---|
| Une entrée absente | Critères concernés **forcés** non évaluables ; sous 4 critères évalués, verdict `indetermine` ; confiance dégradée. |
| F4 absent | `recommandation_prix=null` + limite explicite : recommander un prix sans référence de marché n'aurait aucune valeur. |
| Tendances absent | Critère `demande` non évaluable ; sa fraîcheur n'était de toute façon pas qualifiable. |
| Entrée avec `donnees_suffisantes=false` ou confiance amont faible | Consignée dans `qualite_donnees`, confiance globale dégradée, risque `donnees` ajouté. |
| Diagnostic ou recommandations en échec | Bloc à `null` / listes vides + statut. **Le verdict est produit malgré tout.** |
| Notation elle-même en échec | `verdict="indetermine"`, `grille=[]`, statut en échec, limite explicite disant que l'indétermination vient d'un échec technique et non d'une analyse. |
| Conditions de réexamen en échec | Générées par **gabarits de code** depuis la grille. |
| Restitution en échec | Synthèse factuelle **générée par le code**. |
| Aucune entrée exploitable | `stderr` explicite + **code 2**. |

### Fraîcheur : ce qui est mesurable et ce qui ne l'est pas

**Aucun champ d'horodatage n'est garanti par les contrats amont.** Constat fait
sur les sorties réelles : F3 et F4 publient un `horodatage_utc` (enrichissement
introduit avec eux), **le collecteur Tendances n'en publie aucun**. En son
absence, `fraicheur_qualifiable=false` et un avertissement le dit. Rien n'est
inventé — et surtout rien n'est déduit de la date de modification du fichier,
qui ne dit rien de la date de collecte.

Au-delà de `SEUIL_FRAICHEUR_JOURS = 30`, une entrée horodatée est signalée comme
potentiellement périmée.

---

## 10. Coûts, durée et volumétrie mesurés

Toutes les mesures du 05/08/2026, sur les sorties réelles de F3 et F4 et les
fixtures Tendances du dépôt.

| Cas | Appels | Jetons (entrée / sortie) | Coût | Durée |
|---|---|---|---|---|
| Trois entrées | 6 | 103 161 / 26 947 | **≈ 0,71 $** | **503 s** |
| Sans F4 | 6 | 64 687 / 21 700 | ≈ 0,52 $ | 395 s |
| Verdict défavorable | 6 | — | ≈ 0,6 $ | 441 s |

Les 6 appels sont : diagnostic, notation de la grille, conditions de réexamen,
recommandations, opportunités/risques, restitution. Le coût est dominé par la
taille du dossier de synthèse, transmis à cinq chaînes sur six — abaisser les
constantes `MAX_*_DOSSIER` est le levier direct.

Le récapitulatif de consommation est émis sur `stderr` à chaque exécution. Les
tarifs de `config.py` sont saisis à la main : **à vérifier avant tout usage
budgétaire**.

---

## 11. Exécutions de validation réalisées

### La règle de verdict — test manuel rejouable

10 grilles de référence, exécutées hors LLM. **Chaque grille produit toujours le
même verdict** ; le rejeu d'une même grille 5 fois donne un résultat identique.

| Grille | Score | Évalués | Verdict |
|---|---|---|---|
| Tout au maximum (2,2,2,2,2) | 10 | 5 | `positif` |
| Seuil positif exact (1,1,2,1,1) | 6 | 5 | `positif` |
| Score 7 **mais un critère à 0** (0,2,2,2,1) | 7 | 5 | `indetermine` |
| Score 6 mais **2 non évaluables** (2,2,2,–,–) | 6 | 3 | `indetermine` |
| Score 6 avec **1 non évaluable** (2,2,1,1,–) | 6 | 4 | `positif` |
| Seuil négatif exact (1,1,1,0,0) | 3 | 5 | `negatif` |
| `demande`=0 **et** `differenciation`=0 malgré score 6 | 6 | 5 | `negatif` |
| Zone grise (1,1,1,1,0) | 4 | 5 | `indetermine` |
| 3 critères seulement | 6 | 3 | `indetermine` |
| Grille vide | 0 | 0 | `indetermine` |

Vérifié sur l'ensemble : `declenche_plc == (verdict == "positif")`.

Vérifié également, hors LLM : le plafond effet de mode ramène `demande` de 2 à 1
et fait chuter le score d'un point ; un critère noté malgré l'absence de son
entrée est **forcé** non évaluable.

### Exécutions de bout en bout

| Cas | Résultat observé |
|---|---|
| (a) Trois entrées | Verdict `indetermine` (score 4, 4 critères évalués, `viabilite_prix` non évaluable faute de benchmark en MAD). **Post-validation quasi silencieuse** : aucune référence inventée, seules 8 valeurs de faits clés réécrites depuis le dossier — c'est le mécanisme d'écrasement qui fonctionne. |

Contrôle de traçabilité rejoué sur la sortie du cas (a), en repartant du seul
`dossier_synthese` publié : **55 références citées, 0 orpheline** sur les 94
disponibles. Chaque fondement de type `fait`, chaque fait clé, chaque constat du
diagnostic pointe un élément réellement présent dans le dossier. La fourchette
de prix recommandée (54,41–89,95 EUR) est bien dans l'étendue du benchmark F4
(2,17–199,99 EUR) et dans une devise qu'il publie.
| (b) Sans F4 | `intensite`, `differenciation` et `viabilite_prix` **forcés non évaluables** ; 2 critères évalués → `indetermine` d'office ; `recommandation_prix=null` + limite explicite ; risque `donnees` en gravité élevée ; `donnees_a_completer` nomme l'agent à relancer pour chaque manque. |
| (c) Sans Tendances | Validé hors LLM : le dossier ne porte pas de bloc `demande`, `entrees_manquantes` le signale, et `corriger_notes` force le critère en non évaluable. |
| (d) Verdict défavorable | Voir ci-dessous. |

### Cas (d) — verdict défavorable, en détail

Fixture F3 délibérément dégradée (sentiment 44 négatifs / 2 positifs, pain
points tous en intensité 3, aucun besoin couvert) croisée avec la fixture
Tendances « effet de mode ». Sortie dans `output_defavorable.json` — **441 s,
6 appels**.

| Vérification | Résultat |
|---|---|
| Verdict | `negatif`, score **2**, 4 critères évalués, `declenche_plc=false` |
| Grille | `demande`=0, `intensite`=1, `differenciation`=1, `adequation`=0, `viabilite_prix` non évaluable |
| Recommandation P1 | `reco-produit-1` = **« Recommander le NON-LANCEMENT du JBL Endurance Peak 4 Open Ear sur le marché FR dans sa version actuelle »**, justifiée par les quatre critères défaillants |
| Pivots | 3 recommandations en P2/P3 (refonte de la fixation, contrôle qualité de la charge, montée en Bluetooth 5.4) |
| Risque `effet_de_mode` | **Présent**, gravité élevée, atténuation chiffrée (fenêtre de stock 3-6 mois, critère d'arrêt quantitatif) |
| Risque `donnees` | Présent |
| Conditions de réexamen | 6, toutes observables |
| Post-validation | 2 fondements « fait » sans référence valide **retirés** ; 8 valeurs de faits clés réécrites |

Le plafond « effet de mode » n'a pas eu à s'appliquer ici : le modèle avait déjà
noté `demande` à 0, en dessous du plafond de 1. Son effet est démontré à part,
hors LLM (2 → 1, score −1).

> Note de traçabilité : ce run a révélé qu'un modèle peut émettre une valeur hors
> énumération (`gravite="critique"`). La normalisation des vocabulaires
> contrôlés a été ajoutée à `validation.py` **après** ce run, puis rejouée sur
> l'artefact — la post-validation étant déterministe et idempotente, le fichier
> stocké est identique à ce qu'un run neuf produirait. 5 valeurs corrigées, le
> verdict et le score étant inchangés.

---

## 12. Limites méthodologiques

Injectées systématiquement :

- **La règle de verdict est une hypothèse de travail**, pas un arbitrage validé
  (§3). Ses seuils doivent être recalibrés sur des cas réels avant tout usage
  décisionnel.
- La qualité des recommandations est **bornée par celle des analyses amont** :
  un pain point mal caractérisé ou un benchmark biaisé se propage ici sans être
  détecté.
- Corpus non exhaustif hérité des biais de collecte : **aucune part de marché,
  aucun volume de demande, aucune projection de vente**.
- **Aucune donnée financière interne** (coûts, marges, logistique, budget
  publicitaire) : les recommandations de prix sont des **positionnements de
  marché, jamais des calculs de rentabilité**.

---

## 13. Ce que ce module ne fait pas

Aucune collecte ; aucune lecture des sorties brutes des collecteurs autres que
Tendances ; **aucune classification de phase de cycle de vie** ; **aucune
conversion de devise** — et le prompt interdit même de suggérer d'obtenir un
taux de change, le remède à un benchmark manquant étant de collecter des prix
dans cette devise ; aucune recommandation de prix hors des devises du benchmark
F4 ; aucune persistance hors `--sortie` ; aucun serveur ni interface ; aucune
suite de tests automatisés.
