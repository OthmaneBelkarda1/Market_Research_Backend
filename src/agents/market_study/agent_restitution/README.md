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
    [--gabarit v2|v1] [--sources-etat sources_etat.json] \
    [--langue-analyse fr] [--sortie output.json] [--stdout] [--verbose]
```

| Argument | Défaut | Rôle |
|---|---|---|
| `--recommandations` | — | **Requis.** Sans verdict ni dossier, il n'y a pas de rapport à écrire |
| `--rapport` | `rapport_etude.md` | Chaîne vide = fichier non produit |
| `--resume` | `resume_executif.md` | Chaîne vide = fichier non produit |
| `--sortie` | `output.json` | Métadonnées `ResultatRestitution` ; chaîne vide = aucun fichier |
| `--gabarit` | `v2` | `v2` : rapport décisionnel en cinq écrans. `v1` : ancien rendu en neuf sections, conservé le temps de la transition |
| `--sources-etat` | — | JSON `{source: {donnees_disponibles, nb_items, raison}}` écrit par l'orchestrateur. Il porte la **raison** d'une collecte vide, que les sorties d'analyse ne conservent pas |

| Code de sortie | Signification |
|---|---|
| `0` | Succès |
| `1` | Erreur imprévue |
| `2` | Sortie F5 absente ou inexploitable, ou produits divergents |
| `4` | **Rédaction impossible** (gabarit v2) : aucun fichier n'est écrit |

Le `4` est le seul code qui laisse le disque intact. Il couvre trois situations, et
toutes trois signifient qu'il n'y a pas de rapport à livrer : un gabarit de prompt
qui diverge de son dictionnaire d'invocation (détecté **au chargement**, avant tout
appel au modèle), une chaîne de rédaction qui n'aboutit pas, et un rapport assemblé
qu'un contrôle bloquant refuse. Le diagnostic part sur `stderr` en JSON, avec les
`statuts_analyse` complets : sans fichier de sortie à relire, c'est la seule trace.

**Pourquoi 4 et non 2** — voir l'amendement A5 au §14.

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

Deux gabarits coexistent, sélectionnés par `--gabarit` (défaut : `v2`). Ils
partagent le chargement, la préparation, la liste blanche, la simulation des
bascules et le calcul de confiance : **seules la rédaction, l'assemblage et la
post-validation diffèrent.** Les garanties de sûreté sont donc les mêmes des deux
côtés, par construction et non par recopie.

### 4.1 — `v2`, le rapport décisionnel (défaut)

Cinq écrans, dans un ordre fixe. Chaque sous-titre est une **question métier**, et
les puces y répondent. Tout narratif est en puces courtes — plus aucun paragraphe.

| # | Écran (`##`) | Sous-blocs (`###`) | Budget | Source |
|---|---|---|--:|---|
| 0 | **Décision** | Pourquoi · Le risque principal · Ce qui ferait changer la décision · Ce qu'il manque pour trancher | 200 mots | F5 |
| 1 | **Le consommateur** | Pourquoi ils achètent — ou non · Ce qu'ils apprécient · Ce qui les dérange · Ce qu'ils aimeraient trouver | 350 mots | F3 |
| 2 | **Le marché et les concurrents** | Dynamique de la demande · Que font les concurrents ? · Exemples observés · Prix pratiqués · Les 5 forces · Ce que personne ne fait | 350 mots | F4 |
| 3 | **Ce que nous recommandons** | Phase de vie du marché · Actions prioritaires · Prix · Entrée sur le marché · Opportunités et risques | 300 mots | F5, F6 |
| 4 | **Méthode et limites** | replié dans un `<details>` | — | toutes |

**Le résumé exécutif n'existe qu'à un seul endroit** : `resume_executif.md` est la
copie exacte de l'écran 0. Le v1 le recopiait à l'identique en section 1 du
rapport, et le lecteur lisait deux fois la même chose.

Le titre de l'écran 0 porte le **libellé métier**, la ligne suivante le **verdict
brut** :

```markdown
## Décision : Go conditionnel
Verdict calculé : indéterminé · score 5/8 · fiabilité faible
```

Les deux sont affichés, et la post-validation contrôle les deux. Traduire sans
montrer l'original serait exactement l'adoucissement que ce module s'interdit.

| Verdict de l'analyse | Libellé affiché |
|---|---|
| `positif` | **Go** |
| `negatif` | **No-go** |
| `indetermine` | **Go conditionnel** |

Un « Go conditionnel » impose qu'au moins une condition soit affichée — une
bascule simulée ou un manque identifié. Sans quoi la phrase standard
« Conditions non déterminables à partir des analyses disponibles. » prend le
relais, et l'écart est signalé.

**Bornes de forme**, toutes dans `config.py` et toutes contrôlées : 3 faits clés,
5 points de friction dont les 2 premiers portent un extrait, 6 concurrents,
5 actions P1, 3 opportunités, 3 risques, 30 mots par puce (35 pour « Que font les
concurrents ? »), 12 mots par cellule courte, 40 mots par action.

**Aucune troncature à « … ».** Les cellules trop longues passent par une
compression rédactionnelle ; une compression qui introduirait un chiffre absent
de l'original est rejetée, et l'original coupé au dernier mot entier prend le
relais. Les blocs secondaires ne sont pas supprimés mais **repliés** dans des
`<details>` : le détail reste accessible, il ne s'impose plus.

**Marqueurs pour le frontend** — `<!-- f7:v2 -->` en tête du fichier, et
`<!-- widget:extraits source="…" -->` dans l'écran 2. Le Markdown reste lisible
sans frontend : un tableau de repli des concurrents suit toujours les marqueurs.
Les commentaires de traçabilité `<!-- sources: … -->` et `<!-- extrait: … -->`
sont conservés — ils servent l'audit ; c'est au frontend de les masquer.

### 4.2 — Les 5 forces

Le tableau affiche **toujours ses cinq lignes**, « non évalué » compris : une
ligne absente se lirait comme une force jugée sans intérêt.

L'analyse de synthèse ne publie pas encore de bloc `cinq_forces`. En attendant,
trois forces sont **estimées par une règle déterministe** et portent la mention
« estimation par règle » ; les deux autres sont déclarées non évaluées. Les
seuils sont publiés en hypothèse de travail dans l'écran méthode.

| Force | Règle | Origine |
|---|---|---|
| Rivalité actuelle | élevée si ≥ 30 concurrents **ou** ≥ 100 offres cœur ; moyenne si ≥ 10 **ou** ≥ 30 | F4 |
| Facilité d'entrée | élevée si la médiane du canal le moins cher < 15 (devise du benchmark) **et** aucun annonceur actif ; moyenne si l'une des deux | F4 |
| Pouvoir des clients | élevé si un point de friction de nature économique ou d'accès est documenté **et** ≥ 30 offres cœur ; moyen si l'une des deux | F3 + F4 |
| Pouvoir des fournisseurs | `non évalué` | — |
| Menace des substituts | `non évalué` | — |

> La détection d'un point de friction économique porte sur des **mots entiers**.
> Une recherche par sous-chaîne trouvait « cher » dans « recherche » et « cout »
> dans « couture », et faisait remonter « pouvoir des clients » d'un cran sans
> qu'aucune donnée de prix ne le justifie.

Sur le run de référence : rivalité **élevée** (50 concurrents, 189 offres),
entrée **élevée** (médiane 9,64 EUR, 0 annonceur), clients **moyen** (choix large,
mais aucun point de friction économique documenté).

### 4.3 — `v1`, l'ancien rendu

Neuf sections numérotées, narratif en paragraphes, annexe dépliée. Conservé le
temps de la transition, sans duplication de la logique de sûreté.

| # | Section | Source | Narratif |
|---|---|---|---|
| 1 | Synthèse exécutive | F5 | oui |
| 2 | Verdict de potentiel : *mot du verdict* | F5 | oui |
| 3 | Phase de cycle de vie | F6 | oui |
| 4 | Demande observée | F5 | oui |
| 5 | Besoins et attentes exprimés | F3 | oui |
| 6 | Paysage concurrentiel | F4 | oui |
| 7 | Recommandations | F5 (+ F6) | non |
| 8 | Opportunités et risques | F5 | non |
| 9 | Annexe | toutes | non |

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
narratif a été retiré.

Au-delà, le comportement dépend du gabarit, et c'est la différence de fond entre
les deux. En **v1**, la section est réduite à ses tableaux avec un encart
« lecture narrative indisponible » : un mode dégradé assumé, et ce contrat ne
change pas. En **v2**, cet encart n'existe plus — le module sort en code `4` sans
écrire de rapport.

Ce n'est pas une préférence de forme. Le run *8609db9e* a rendu un rapport dont
les sept sous-blocs narratifs portaient cet encart, en code de sortie `0` : l'étude
a été marquée `completed`, le frontend l'a affichée comme les autres, et un
décideur a reçu un document sans une phrase d'analyse. Un rapport manquant se voit ;
un rapport vide qui a l'air complet, non.

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
| Chaîne de rédaction en échec après reprise (**v1**) | Section réduite à ses tableaux + encart « lecture narrative indisponible » + statut |
| Chaîne de rédaction en échec après reprise (**v2**) | **Arrêt** : code 4, aucun fichier écrit, `statuts_analyse` sur `stderr` |
| Sous-bloc hors contrat après régénération et compression (**v2**) | **Arrêt** : code 4 |
| Contrôle bloquant en échec sur le rapport assemblé (**v2**) | **Arrêt** : code 4 |
| Source collectée à vide, signalée par `--sources-etat` | Source **nommée avec sa raison** dans la ligne « Sources analysées » + encart « Étude partielle » |
| F5 inexploitable | Code 2 |

Une **donnée** manquante dégrade : le sous-bloc affiche sa phrase standard, et le
cas est tracé. Un échec de **rédaction** ne dégrade pas, il arrête. La distinction
est celle-ci : une absence de donnée est un résultat d'étude, que le lecteur doit
voir ; une panne de rédaction est un défaut du module, que le lecteur ne peut ni
détecter ni corriger.

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

## 11. Coûts, durée et volume

### 11.1 — Mesures v1 (06/08/2026, run *ashwagandha-supplement-ES*)

| Scénario | Appels LLM | Coût estimé | Durée | Nombres vérifiés / retirés |
|---|---|---|---|---|
| Quatre entrées (F5 + F3 + F4 + F6) | 6 | ≈ 0,10 $ | 75 s | 604 / **0** |
| F5 seule — toutes sections dégradées | 5 | ≈ 0,08 $ | 57 s | 496 / **0** |

### 11.2 — Volume v1 / v2 (03/09/2026, run *ceinture-lombaire-FR*)

| | v1 | v2 |
|---|--:|--:|
| Sections / écrans | 9 | 5 |
| Document rendu | 430 lignes, 47 505 car. | **379 lignes, 33 500 car.** |
| Appels de rédaction | 6 (une par section narrative) | **4 écrans + 1 compression** |
| Budget de narratif | non borné | **1 200 mots, contrôlés** |
| Annexe | dépliée, ≈ 1/3 du document | **repliée** |
| Résumé exécutif | recopié en section 1 | **présent une seule fois** |

> **Mesure honnête du volume rendu.** Hors tableaux, blocs repliés et titres, le
> rapport v2 porte **892 mots produits par le code** — puces recopiées des
> analyses amont, bascules simulées, lignes standard — auxquels s'ajoute le
> narratif du modèle, plafonné à 1 200 mots. Le budget de 1 200 mots gouverne
> **le narratif**, pas le document entier : la cible de 1 200 mots pour le rendu
> complet n'est donc pas atteinte, et ne peut pas l'être sans couper dans des
> contenus amont que le rapport a pour rôle de restituer. Les puces injectées par
> le code sont en revanche ramenées à leur **première phrase**, plafonnée, ce qui
> a retiré 139 mots sans retirer une seule information portée par un énoncé.

Les chiffres de volume viennent d'un essai à blanc — préparation et assemblage
réels, puces factices — donc ils ne comptent pas le narratif final. Ils sont à
reprendre après le premier run complet en v2.

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

---

## 13. Points ouverts amont

Ces défauts appartiennent à F3, F4 ou F5. **F7 ne les corrige pas** : il les rend
visibles, et les déclare en hypothèse ou en limite.

| Point | Effet constaté | Où le corriger |
|---|---|---|
| **Offres AliExpress « sans marque » regroupées en un seul concurrent** | fausse la concentration des volumes (98,9 % sur le top 3) et le critère « intensité concurrentielle » | F4, puis F5 |
| **Volumes de ventes de canaux différents sur un champ unique** | Amazon publie un volume **mensuel**, AliExpress un **cumul** depuis la mise en ligne ; `volume_ventes_cumule` les mélange | F4 |
| **Limite F3 mentionnant une SERP États-Unis sur une étude FR** | limite recopiée verbatim, incohérente avec le marché étudié | F3 |
| **Pas de bloc `cinq_forces`** | trois forces estimées par règle locale à la restitution, deux non évaluées | F5 |
| **Pas de `clientele_cible` au comparatif** | le sous-bloc « Leur clientèle » affiche sa phrase standard | F4 |
| **Échelle d'intensité des points de friction** | l'échelle va de **1 à 3** (« 1 = gêne, 2 = problème net, 3 = rédhibitoire »), pas de 1 à 5 ; le rapport affiche donc `x/3` | F3 (documentation) |

Les deux premiers champs — `cinq_forces` et `clientele_cible` — sont **déjà
déclarés en optionnel** dans `schemas.py` : F7 les consommera sans modification
le jour où les analyses amont les publieront.

F7 rétablit l'unité des volumes quand une ligne du comparatif ne porte qu'un
canal ; quand elle en porte plusieurs, l'unité est déclarée **indéterminée**
plutôt que devinée.

---

## 14. Contrats de sous-blocs et amendements au gabarit v2

### 14.1 — Le gabarit est vérifié par le code

Jusqu'au run *8609db9e*, le gabarit v2 n'était tenu que par les consignes envoyées
au modèle et par les budgets de mots. Rien ne vérifiait qu'un « Pourquoi » sortait
avec ses trois puces chiffrées, ni que les cinq puces des concurrents portaient
leurs libellés. `CONTRAT_SOUS_BLOCS` (dans `config.py`) le rend vérifiable :

| Sous-bloc | Puces | Mots/puce | Autre |
|---|---|---|---|
| `pourquoi` | 3 | 30 | un chiffre par puce, **quand le fait clé en porte un** (A6) |
| `risque_principal` | 1 | 30 | — |
| `pourquoi_achat` | 2–4 | 30 | — |
| `apprecient` | 2–4 | 30 | — |
| `derange` | 1–5 | 30 | une phrase par point de friction, appariée par rang |
| `aimeraient` | 2–4 | 30 | — |
| `dynamique_demande` | 1 | 30 | puce de lecture des quatre indicateurs |
| `que_font_concurrents` | 5 | 35 | libellés imposés, dans l'ordre |
| `prix_pratiques` | 1 | 30 | puce de lecture du tableau des prix |
| `prix` | 2–3 | 30 | — |
| `entree_marche` | 3–5 | 30 | — |

Les clés de cette table sont exactement celles de `SOUS_BLOCS_REDIGES` : la
cohérence des deux est vérifiée **au chargement** de `redaction_v2`, pas laissée à
la relecture. Un sous-bloc qui affiche sa phrase standard, faute de donnée, sort du
contrat — il n'est pas rédigé, il n'a rien à respecter.

### 14.2 — Les quatre contrôles bloquants

`gabarit_conforme`, `aucun_repli_interdit`, `aucune_troncature` et
`ligne_sources_complete` n'ajoutent pas une mention au rapport : ils l'empêchent de
partir. Leur point commun est qu'un lecteur ne peut pas voir le défaut qu'ils
détectent, et ne le corrigera donc jamais de lui-même. Un budget dépassé se voit ;
un « Pourquoi » à deux puces au lieu de trois, non.

Les autres contrôles restent informatifs : ils décrivent un rapport qui part.

### 14.3 — Amendements

| # | Amendement | Raison |
|---|---|---|
| **A1** | L'intensité des points de friction s'affiche `x/3`, pas `x/5` | F3 produit une échelle 1–3 (§13). Le code l'affichait déjà correctement ; c'est le gabarit de référence qui portait l'erreur |
| **A2** | Un échec de rédaction n'a plus de repli : code `4`, aucun fichier écrit | Un rapport dégradé silencieux coûte plus cher qu'une étude visiblement échouée — c'est l'incident *8609db9e* |
| **A3** | Les six sources sont **toujours** citées, une source vide avec sa raison | AliExpress a rapporté zéro offre et n'était pas citée : rien ne le disait au lecteur |
| **A4** | Les indicateurs de demande hors des quatre du gabarit vont en `<details>` ; « non calculable » est une valeur affichée | Le tableau à neuf lignes noyait les quatre qui portent la décision, et le momentum 90 j — absent en amont — s'y lisait comme un oubli |
| **A5** | `REDACTION_IMPOSSIBLE` = code **4**, et non 2 | Le 2 est pris : les onze modules du pipeline l'emploient pour « entrée inexploitable », et l'orchestrateur y voit un défaut de câblage. Réutiliser 2 rendrait indiscernables un gabarit de prompt cassé et un JSON F5 manquant |
| **A6** | Le chiffre est exigé **puce par puce**, selon ce que porte le fait clé source | Un fait clé amont est parfois qualitatif — « signal d'effet de mode confirmé », dont la valeur est un booléen. Exiger un chiffre là où la source n'en porte aucun revient à en demander l'invention, que la liste blanche retirerait ensuite |
| **A7** | Un dépassement de mots est signalé et compressé, il ne **bloque pas** | Le modèle dépasse de quelques mots à chaque exécution en français. Refuser un rapport entier parce qu'une puce fait 34 mots au lieu de 30 rendrait F7 inutilisable ; le dépassement reste compté par `budgets_respectes`, qui n'a jamais bloqué |

### 14.4 — Aucune coupe mécanique

Le v2 avait remplacé la troncature à « … » par sa suppression pure et simple. Le
remède était pire : le signe de la coupe disparaissait, la coupe restait. Le run
*8609db9e* a livré « … la crédibilité acquise ou », « … un kit complet plug and »,
« … mentionnent explicitement en » — des textes coupés que plus rien ne signalait.

Trois règles depuis :

1. **Un titre n'est jamais coupé.** Titres d'opportunité et de risque, énoncés
   d'action : la première *phrase* les borne, jamais un n-ième mot.
2. **Toute réduction passe par `compresser_cellules`**, qui réécrit plus court. Une
   compression rejetée par la liste blanche rend le texte **intégral** — long, mais
   exact.
3. **Une coupe résiduelle recule** jusqu'à un point d'arrêt : dernière ponctuation
   forte, sinon dernier mot qui ne soit pas un mot-outil. Le contrôle
   `aucune_troncature` cherche les trois signatures d'une coupe machine — ellipse,
   virgule finale, mot-outil final — et il est bloquant.

---

## 15. Vocabulaire — le rapport se lit sans dictionnaire (v2.1)

### 15.1 — Le lecteur, et la règle unique

**Le lecteur cible est un e-commerçant ou un dirigeant de PME, sans culture
marketing ni statistique.** Il lit les écrans 0 à 3 de bout en bout, une fois,
et décide. Il ne consulte pas de glossaire et ne relit pas une phrase.

> **Si un terme ne serait pas compris par un commerçant qui n'a jamais fait
> d'étude de marché, il n'apparaît pas dans les écrans 0 à 3 — et il n'y est pas
> expliqué en note, il est remplacé.**

Trois traitements, dans cet ordre de préférence :

1. **Remplacer** par un équivalent courant — le cas par défaut, `LEXIQUE_INTERDIT` ;
2. **Garder avec une glose** de huit mots au plus, quand le terme n'a pas
   d'équivalent (« cœur de marché ») ou que le client le rencontrera ailleurs
   (« place de marché ») — `TERMES_GLOSES` ;
3. **Reléguer à l'écran 4**, où le vocabulaire technique reste admis et où le
   glossaire fait foi.

**Ce qui n'est jamais réécrit** : les citations clients, dans leur langue
d'origine ; les noms de marques et de produits ; les libellés de sources ; les
limites recopiées verbatim à l'écran 4. Et la règle qui prime sur toutes les
autres : **une reformulation n'ajoute, ne retire et n'arrondit aucun chiffre.**
La liste blanche numérique s'applique à toute sortie de modèle, y compris aux
libellés réécrits.

### 15.2 — Où le vocabulaire est tenu, et dans quel ordre

Trois endroits, et leur ordre est celui de leur qualité :

| Rang | Mécanisme | Ce qu'il produit |
|---|---|---|
| 1 | **La consigne de rédaction** (`REGLES_LANGUE_V21`, en tête du prompt) | Du français correct, écrit du premier coup |
| 2 | **La régénération** (`agent._tenir_le_lexique`), avec la liste des termes fautifs | Du français correct, réécrit |
| 3 | **La substitution** (`config.appliquer_lexique`) | Le bon mot, pas toujours le bon accord |

**La substitution ne fait pas de grammaire.** Remplacer un nom change son genre :
les entrées qui portent leur article traitent le cas (« le claim » → « la
promesse publicitaire »), les autres non — « 30 contributions positives » devient
« 30 avis et messages positives ». C'est un filet, et `nb_termes_substitues` le
compte : un chiffre non nul ne dit pas que le rapport est bon, il dit que la
consigne de rédaction demande à être resserrée.

Les textes que le **code** recopie des analyses amont — tableaux, puces de
bascules, opportunités, risques — n'ont ni consigne ni régénération : ils
n'ont que la substitution (`preparation_v2.CHAMPS_LEXIQUE_V2`). C'est pourquoi
la réparation durable de ce vocabulaire est **en amont**, dans les analyses.

### 15.3 — Ajouter un terme au lexique

1. Ajouter l'entrée à `LEXIQUE_INTERDIT`, en minuscules.
2. Si le remplacement **change le genre du nom**, ajouter aussi les formes qui
   portent l'article (`le X` → `la Y`, `un X` → `une Y`, `du X` → `de la Y`).
   Sans elles, la substitution produira un article faux.
3. Si le terme en contient un autre déjà listé, vérifier que la forme longue est
   bien la plus longue : la substitution traite les termes du plus long au plus
   court, et c'est ce qui garantit que « corpus collecté » passe avant « corpus ».
4. Rejouer un run de référence et lire la section concernée à voix haute.

### 15.4 — Les indicateurs disent leur échelle

Chaque chiffre du tableau de la demande porte un texte de lecture qui donne
**l'échelle et le sens**, jamais la définition mathématique :
`TEXTES_LECTURE_INDICATEURS`. Ces textes sont écrits en dur et ne sont **jamais**
produits par le modèle — ce sont des explications de méthode, elles ne doivent
pas varier d'un rapport à l'autre.

Le mois de saisonnalité est rendu en toutes lettres, les séparateurs décimaux
sont des virgules, et une puce de lecture apparaît **automatiquement** lorsque
l'évolution sur 90 jours et la tendance de fond sur 5 ans sont de signes opposés
(`PUCE_TENDANCES_OPPOSEES`) : deux chiffres qui se contredisent sans un mot
d'explication n'informent pas, ils annulent la confiance dans les deux.

### 15.5 — Le libellé de décision

Le titre porte le libellé métier **puis** sa traduction en clair :
`## Décision : No-go — **ne pas lancer ce produit en l'état**`.

Choix conservateur assumé. `LIBELLES_VERDICT` est comparé au verdict amont par
le contrôle `verdict_conforme` ; remplacer « No-go » par « Ne pas lancer »
romprait ce lien de traçabilité pour un gain de forme. Un basculement complet en
français est une **décision métier**, pas un changement de code : elle touche la
constante, le contrôle et le frontend.

### 15.6 — Les contrôles de vocabulaire

| Contrôle | Ce qu'il vérifie |
|---|---|
| `lexique_conforme` | Aucun terme de `LEXIQUE_INTERDIT` dans les écrans 0 à 3 — citations et commentaires HTML exclus du périmètre |
| `valeurs_techniques_absentes` | Aucun identifiant à tiret bas (`effet_de_mode`), aucun sigle interne (`CDC`, `F3`…) |
| `coherence_inter_ecrans` | Aucun écran n'affirme l'absence d'une source qu'un autre exploite |
| `nb_termes_substitues` | Combien de fois le filet a dû servir |

`coherence_inter_ecrans` **ne bloque pas** le rapport : la puce contredite est
retirée et le retrait est tracé. Le défaut est en amont (voir §16, anomalie 1),
et arrêter la restitution ne le réparerait pas — cela priverait seulement le
lecteur des quatre écrans corrects.

---

## 16. Points ouverts amont — les dix anomalies du run `4faf8699`

Relevées pendant la revue de lisibilité du 04/09/2026. **Celles qui ne relèvent
pas de F7 sont tracées ici, pas corrigées en silence** : les réécrire dans la
restitution masquerait un défaut qui continuerait de produire de mauvaises
données pour tous les autres consommateurs de ces analyses.

| # | Constat | Ressort | État |
|---|---|---|---|
| 1 | « Aucun avis client n'est présent dans les données fournies » (angles inexploités) contredit l'écran 1, qui analyse 26 avis Amazon. F4 raisonne sur *son* corpus — annonces et pages — et écrit une phrase fausse hors de ce contexte | **F4** | Contourné dans F7 : la puce est retirée et le retrait tracé (`coherence_inter_ecrans`). **Ticket amont à ouvrir** : F4 doit borner son affirmation à ses propres sources |
| 2 | Écran 1 : « 181 unités analysées sur 3 sources » alors que le tableau de tonalité totalise 55. Le 181 est le volume **collecté** | **F7** | Corrigé : le badge dit « collectés », le tableau dit « analysés » |
| 3 | Écran 0 : « Web (10 pages) » alors que l'écran 4 déclare 5 documents | À investiguer (F24 ou comptage F7) | Un **seul** compteur est désormais utilisé (`referentiel_stats.nb_pages`) et `compteurs_coherents` vérifie l'égalité. Si l'écart réapparaît, il vient de F24 |
| 4 | Écran 0 : « Amazon (58 offres) » sans le nombre d'avis | **F7** | Corrigé : « Amazon (58 offres, 26 avis) » |
| 5 | Intensité des reproches sur 3 alors que le gabarit v2 spécifiait `/5` | **F7** | Tranché : l'échelle de F3 va de 1 à 3, le gabarit portait l'erreur (amendement A1). Le mot « intensité » devient « gravité », et son texte de lecture donne les bornes |
| 6 | Vignettes AliExpress : « −−6 % », prix barré inférieur au prix courant, « 1 commandes », prix au format anglo-saxon | **Frontend + F21** | Tracé. Voir le message Lovable |
| 7 | Segment d'entrée AliExpress démarrant à 0,37 € : très probablement un accessoire, pas un micro. Fausse la fourchette et la recommandation de prix | **F4** | **Ticket amont à ouvrir** : filtrage des valeurs aberrantes du benchmark |
| 8 | Évolution 90 jours à −54,6 % mais tendance de fond à +5,6 points/an, sans explication | **F7** | Corrigé : une puce de lecture apparaît automatiquement quand les deux indicateurs sont de signes opposés |
| 9 | Titres de produits Amazon affichés en anglais dans une étude FR | **Frontend / F21** | Signalé, **non traduit** : c'est la donnée source |
| 10 | Volume concurrent « 49 538 · unité indéterminée » sur une colonne qui porte ailleurs « /mois » | **F4** | Déjà tracé en v2, à relancer |

### Le jargon d'outillage dans les limites

Les limites produites par les collecteurs contiennent **SERP**, **actor**,
**serpProxyGroup**, **TLD**, **échantillonnage stratifié**, **re-scoring**. F7
n'a pas le droit de les réécrire — les limites sont recopiées verbatim, c'est un
invariant du module — et fait donc deux choses : il précède chaque famille d'une
phrase de résumé en langage courant (`RESUMES_FAMILLES_LIMITES`) et définit dans
son glossaire les termes réellement rencontrés (`TERMES_GLOSSAIRE_TECHNIQUE`).

**Ticket amont à ouvrir** : ces textes doivent être réécrits **à la source**, en
français d'affaires, sans nom d'outil ni de paramètre technique. F7 les
recopiera alors proprement, sans que l'invariant bouge.

### La double négation de F4

Les angles inexploités arrivent sous la forme « Aucun claim publicitaire ne met
en avant… hormis un seul annonceur ». F7 les repasse à l'affirmative par une
chaîne dédiée (`redaction_v2.reformuler_affirmatif`), qui préserve les réserves
de méthode et rejette toute réécriture introduisant un chiffre.

**Ticket amont à ouvrir** : demander à F4 une formulation affirmative
(« Personne ne met en avant… »). L'appel LLM de F7 disparaîtrait alors.
