# Baseline de consommation de jetons — pipeline d'étude de marché

> **Chantier 0. Aucune ligne du pipeline n'a été modifiée pour produire ce document.**
> Les jetons sont comptés par `POST /v1/messages/count_tokens`, jamais estimés. Les
> charges utiles ont été capturées telles qu'elles partent réellement, par un
> enregistreur injecté via `PYTHONPATH` (`sitecustomize.py`), qui recopie la charge
> construite par `langchain_anthropic` sans l'altérer.
>
> Exécution : 02/09/2026, 14:31 → 14:51. `langchain-anthropic` 1.5.4, Python 3.12.13.
>
> **Suite — 02/09/2026 :** les chantiers effectivement menés, leurs gains mesurés
> et ce qui a été écarté sont dans [`reduction_jetons.md`](reduction_jetons.md).
> Ce document-ci reste l'état des lieux ; il n'a pas été réécrit après coup.
> Une valeur y est corrigée : « le choix de modèle est hors périmètre » (§8) a été
> levé, et c'est le plus gros levier du dépôt — mais Sonnet 5 tokenise 18,45 %
> plus haut que Sonnet 4.5, ce qui annule les deux tiers de la baisse de tarif.

---

## 1. Jeu de référence

Le jeu prévu (`etudes/ashwagandha-supplement-ES/`) et l'étalon annoncé par
`docs/baseline_latence.md` (`docs/baseline/ashwagandha-ES/`) **n'existent plus** : les
deux chemins sont couverts par `.gitignore` et n'ont jamais été versionnés.

Jeu retenu : **`var/baseline/ceinture-lombaire-FR/`**, reconstruit à partir des fichiers
collecteurs de `var/studies/d340f7e8-…` — exactement le run « ceinture lombaire,
225 unités » cité par le diagnostic. Produit *Ceinture lombaire de maintien*, marché FR/fr,
225 unités consommateurs, 9 documents web, 50 concurrents consolidés.

`tendances.json` est absent de ce workdir : la branche `demande` du dossier F5 vaut `null`,
et F6 n'est pas déclenché (verdict `indetermine`). **Deux familles de chaînes ne sont donc
pas couvertes** : F6 (`orientations`, `recommandations_phase`) et F4 `extraction_claims`
(0 appel). Elles restent à mesurer.

---

## 2. Cartographie complète — 61 appels, 1,9143 $

Décomposition exacte. `PRÉFIXE` = définition d'outil + prompt système rendu.
`COMMUN` = le plus long préfixe **déjà identique d'un appel à l'autre**, c'est-à-dire ce
qui est cachable aujourd'hui sans rien réordonner.

| Module | Chaîne | Modèle | n | Outil | Système | **PRÉFIXE** | COMMUN | Charge utile | Entrée facturée | Sortie | Coût |
|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| F3 | `carte_unites` | haiku-4.5 | 15 | 1 275 | 521 | **1 797** | 1 796 | 36 295 | 64 765 | 28 548 | 0,2075 $ |
| F3 | `carte_documents` | haiku-4.5 | 3 | 929 | 290 | **1 220** | 1 219 | 20 837 | 24 800 | 3 151 | 0,0406 $ |
| F3 | `normalisation_libelles` | haiku-4.5 | 2 | 714 | 333 | **1 048** | 737 | 5 099 | 7 391 | 2 481 | 0,0198 $ |
| F3 | `synthese_insights` | sonnet-4.5 | 1 | 1 529 | 531 | **2 061** | — | 16 451 | 18 615 | 6 343 | 0,1510 $ |
| F3 | `lecture_critique` | sonnet-4.5 | 1 | 705 | 327 | **1 033** | — | 3 189 | 4 325 | 1 220 | 0,0313 $ |
| F4 | `extraction_attributs` | haiku-4.5 | 16 | 656 | 305 | **962** | 961 | 15 728 | 32 688 | 10 612 | 0,0857 $ |
| F4 | `consolidation_concurrents` | sonnet-4.5 | 1 | 851 | 443 | **1 295** | — | 7 465 | 8 860 | 5 764 | 0,1130 $ |
| F4 | **`analyse_concurrent`** | sonnet-4.5 | **8** | 1 406 | 412 | **1 819** | **1 818** | 9 671 | 25 023 | 14 762 | **0,2965 $** |
| F4 | `lecture_transversale` | sonnet-4.5 | 1 | 2 180 | 451 | **2 632** | — | 5 028 | 7 763 | 3 836 | 0,0808 $ |
| F4 | `differenciation` | sonnet-4.5 | 1 | 1 661 | 500 | **2 162** | — | 5 990 | 8 250 | 4 599 | 0,0937 $ |
| F4 | `synthese_executive` | sonnet-4.5 | 1 | 587 | 222 | **810** | — | 6 516 | 7 426 | 520 | 0,0301 $ |
| F5 | `diagnostic_croise` | sonnet-4.5 | 1 | 1 239 | 555 | **1 795** | — | 11 505 | 13 396 | 4 985 | 0,1150 $ |
| F5 | `notation_grille` | sonnet-4.5 | 1 | 1 005 | 439 | **1 445** | — | 12 010 | 13 554 | 2 172 | 0,0732 $ |
| F5 | `conditions_reexamen` | sonnet-4.5 | 1 | 578 | 323 | **902** | — | 2 487 | 3 491 | 484 | 0,0177 $ |
| F5 | `recommandations` | sonnet-4.5 | 1 | 2 750 | 954 | **3 705** | — | 19 456 | 23 262 | 9 332 | 0,2098 $ |
| F5 | `opportunites_risques` | sonnet-4.5 | 1 | 1 235 | 535 | **1 771** | — | 16 771 | 18 646 | 6 488 | 0,1533 $ |
| F5 | `faits_cles_synthese` | sonnet-4.5 | 1 | 733 | 364 | **1 098** | — | 24 787 | 25 985 | 2 532 | 0,1159 $ |
| F7 | `redaction_*` | sonnet-4.5 | **5** | 673 | 1 010 | **1 684** | **775** | 5 927 | 14 852 | 2 319 | 0,0793 $ |
| | **TOTAL** | | **61** | | | | | | **323 092** | **110 148** | **1,9143 $** |

| Module | Appels | Coût | Durée LLM |
|---|--:|--:|--:|
| F3 | 22 | 0,4502 $ | 338,8 s |
| F4 | 28 | 0,6998 $ | 513,3 s |
| F5 | 6 | 0,6849 $ | 473,3 s |
| F6 | 0 | — | — |
| F7 | 5 | 0,0793 $ | 48,3 s |
| **Total** | **61** | **1,9143 $** | **1 373,7 s** |

### Le fait qui commande tout le reste

> **La sortie représente 63 % de la facture pour 25 % des jetons.**
> Entrée : 323 092 jetons → 0,7100 $. Sortie : 110 148 jetons → 1,2043 $.

Le diagnostic annonçait 45 % / 13 %. C'est pire que prévu, et cela déplace le centre de
gravité du chantier : **tout levier qui n'agit que sur l'entrée plafonne à 37 % de la
facture.** Le prompt caching en fait partie.

---

## 3. Verdict sur les seuils de cache — définitif

Seuils minimaux de préfixe cachable, revérifiés le 02/09/2026 :

| Modèle | Identifiant | Minimum |
|---|---|--:|
| Haiku 4.5 | `claude-haiku-4-5-20251001` | **4 096** |
| Sonnet 4.5 | `claude-sonnet-4-5-20250929` | **1 024** |
| Opus 5 | `claude-opus-5` (`product_extraction`) | **512** |

Une chaîne n'est un candidat au caching que si elle remplit **deux** conditions : préfixe
au-dessus du seuil, **et** plus d'un appel. Sur un appel unique, le caching coûte 25 % de
plus et ne rapporte rien.

| Chaîne | Appels | Préfixe commun | Seuil | Verdict |
|---|--:|--:|--:|---|
| F3 `carte_unites` | 15 | 1 796 | 4 096 | **Non — 44 % du seuil** |
| F3 `carte_documents` | 3 | 1 219 | 4 096 | **Non — 30 %** |
| F3 `normalisation_libelles` | 2 | 737 | 4 096 | **Non — 18 %** |
| F4 `extraction_attributs` | 16 | 961 | 4 096 | **Non — 23 %** |
| **F4 `analyse_concurrent`** | **8** | **1 818** | 1 024 | **OUI — 1,8 × le seuil** |
| F7 `redaction_*` | 5 | 775 | 1 024 | Non en l'état — **oui après réordonnancement (1 327)** |
| Toutes les autres | 1 | — | — | Sans objet : appel unique |

### L'hypothèse n°2 était fausse dans les deux sens

Le prompt annonçait un préfixe haiku de **≈ 3 400 jetons**, donc « juste sous le seuil ».
Il est de **1 797**. Deux corrections à en tirer :

1. **Le préfixe est deux fois plus petit qu'annoncé** — donc bien plus loin du seuil.
2. **Sa composition n'est pas celle qu'on croit** : sur les 1 797 jetons, **1 275 (71 %)
   sont la définition d'outil**, contre 521 pour les consignes rédigées. Le schéma JSON se
   tokenise très mal : 1 799 caractères deviennent 1 275 jetons, soit **1,4 caractère par
   jeton**, quand la prose française en fait 3,7. **Les `description=` des `Field(...)` et
   la structure du schéma sont le vrai poids du préfixe, et personne ne les relit jamais.**

### Chantier 4.3 — à abandonner, recalculé

Le prompt projetait : épaissir le préfixe haiku de 3 400 à 4 200 jetons, gain
**81 600 → 14 900 jetons, −82 %**. Refait sur la valeur mesurée, pour les 15 appels de
`carte_unites` :

| | Jetons d'entrée facturés |
|---|--:|
| Aujourd'hui : 15 × 1 797 | **26 955** |
| Épaissi à 4 200 puis caché : 1,25 × 4 200 + 14 × 0,1 × 4 200 | **11 130** |
| **Gain** | **15 825 jetons = 0,0158 $** |

**1,6 centime par étude, en échange de 2 400 jetons de contenu inventé injectés dans le
prompt de l'étage de classification, en Tier C.** C'est le seul sous-chantier capable de
déplacer les chiffres publiés, pour 0,8 % de la facture. **Recommandation ferme :
abandonner le Chantier 4.3.** Je ne l'exécuterai pas sans instruction contraire.

### Chantier 4 — tout ce qu'il peut rapporter, exactement

| Chaîne | n | Préfixe | Aujourd'hui | Avec cache | Gain | Gain $ |
|---|--:|--:|--:|--:|--:|--:|
| F4 `analyse_concurrent` (4.1, tier A) | 8 | 1 818 | 14 544 | 3 546 | 10 998 | **0,0330 $** |
| F7 `redaction_*` (4.2, tier B) | 5 | 1 327 | 6 635 | 2 190 | 4 445 | **0,0133 $** |
| | | | | | **Total** | **0,0463 $** |

> **Le levier présenté comme « de plus fort volume » vaut 2,4 % de la facture.**
> Il reste à faire — c'est de l'argent gratuit et sans risque sur F4 — mais il ne portera
> pas la cible.

### Chantier 4.2 sur F5 — inapplicable, pour une raison structurelle

Le dossier de synthèse pèse **10 147 jetons** (pas 17 000) et part vers **cinq** chaînes
sur six — `conditions_reexamen` ne le reçoit pas :

| Chaîne F5 | Charge utile | dont dossier |
|---|--:|--:|
| `diagnostic_croise` | 11 505 | 10 147 |
| `notation_grille` | 12 010 | 10 147 |
| `recommandations` | 19 456 | 10 147 |
| `opportunites_risques` | 16 771 | 10 147 |
| `faits_cles_synthese` | 24 787 | 10 147 |
| `conditions_reexamen` | 2 487 | 0 |

**50 735 jetons réémis, soit 16 % de toute l'entrée du pipeline et 0,152 $.** La cible est
réelle. Mais le cache ne peut pas l'atteindre : l'appariement se fait sur le préfixe, dans
l'ordre `tools` → `system` → `messages`, et **les cinq chaînes ont cinq schémas de sortie
différents, donc cinq définitions d'outil différentes**. Un changement de définition
d'outil invalide les trois niveaux de cache. Déplacer le dossier dans le bloc système n'y
change rien : ce qui suit un outil divergent ne peut pas être lu depuis le cache.

Une échappatoire existe — `tool_choice` préserve les caches `tools` et `system` — et
consisterait à déclarer les cinq outils sur les cinq appels en forçant le bon. Elle change
ce que voient cinq chaînes de jugement et sort de `with_structured_output`. **C'est un
arbitrage, il vous revient (§7).**

**Le levier praticable sur F5 n'est donc pas le Chantier 4.2, c'est le Chantier 3.1** :
donner à chaque chaîne la seule vue dont elle a besoin.

---

## 4. Les huit hypothèses

| # | Hypothèse | Verdict |
|---|---|---|
| 1 | Aucun `cache_control` | **Confirmée.** Aucune occurrence dans `src/`. |
| 2 | Préfixe haiku ≈ 3 400 jetons | **Infirmée.** 1 797 mesurés, dont 71 % de définition d'outil. |
| 3 | Dossier F5 ≈ 17 000 jetons, six chaînes | **Infirmée deux fois.** 10 147 jetons, cinq chaînes. |
| 4 | Les schémas font émettre identifiants et extraits recopiés | **Confirmée pour F4, à nuancer pour F3.** Voir §5. |
| 5 | Charges utiles polluées de champs non consommés | **Infirmée.** Projections manuelles champ par champ, avec troncature explicite (titres 160 car., annonces 400, pages 600). Rien à dégraisser. |
| 6 | `resumer_consommation()` sans ventilation du cache | **Infirmée — déjà corrigée**, non commitée. Les 12 `config.py` portent la ventilation, les multiplicateurs 0,10 / 1,25 / 2,00 et l'alerte tarif manquant. |
| 7 | Table de tarifs en cinq exemplaires | **Confirmée, et pire : douze.** 5 modules d'analyse + 6 collecteurs + `product_extraction`. |
| 8 | `product_extraction` : pas de comptabilité, Opus 5, 20 pas, outils non plafonnés | **Modèle et pas confirmés ; le reste infirmé.** La comptabilité existe déjà (non commitée), et les résultats d'outil **sont plafonnés** à 18 000 caractères (`MAX_PAGE_TEXT_CHARS`, `MAX_RAW_RECORD_CHARS`). |

**Chantier de latence** : la baseline existe, les chantiers suivants non. Aucune
parallélisation de lots dans les cinq modules d'analyse. Le parallélisme F3 ∥ F4 existe,
mais au niveau de l'orchestrateur. **Le caching sera donc implémenté en séquentiel**, avec
la contrainte d'amorçage inscrite en commentaire là où la parallélisation viendra.

---

## 5. Ce que le modèle recopie — le gisement du Chantier 2

**Correction importante sur F3.** Les 30 verbatims publiés dans `insights.json` **ne sont
pas émis par le modèle** : `SortieSyntheseInsights` n'a aucun champ verbatim, et
`agent.py:190` les injecte depuis `reduction.verbatims_par_pain_point`. Le Chantier 2 est
donc largement **déjà fait sur F3**, sans que le prompt le sache.

Ce que le modèle émet réellement, mesuré :

| Chaîne | Ce qui est émis | Volume | Coût |
|---|---|--:|--:|
| F3 `carte_unites` | 225 `id_unite` opaques recopiés | 2 523 j sortie haiku | 0,0126 $ |
| F3 `synthese_insights` | 83 `preuves_id` opaques | 1 036 j sortie sonnet | 0,0155 $ |
| F4 `analyse_concurrent` | **87 `Preuve.extrait` recopiés du corpus** | 3 499 j sortie sonnet | **0,0525 $** |
| F4 `analyse_concurrent` | 180 `id_reference` opaques | 1 898 j sortie sonnet | 0,0285 $ |
| | **Total récupérable** | | **≈ 0,10 $** |

### Le Chantier 2 sur F4 n'est pas une optimisation, c'est une correction

La post-validation du run de référence rapporte, sur `concurrence.json` :

```
58 extrait(s) de preuve absent(s) du texte source, remplacé(s) par le début réel du texte
17 constat(s) déclaré(s) « fait » sans preuve valide rétrogradé(s) en « hypothese »
```

**58 extraits sur 87 — 67 % — ne correspondaient pas au texte source.** La garantie n°3 a
fait son travail, mais elle a dû réécrire les deux tiers des preuves publiées. Faire
émettre un index borné (`Field(ge=1, le=N)`) et injecter le texte par le code supprime ce
mode d'échec **et** les 5 400 jetons de sortie. C'est le meilleur rapport du chantier, et
il contredit le prompt : ces garde-fous ne sont pas silencieux sur un run sain.

**Priorité : F4 avant F3.**

---

## 6. Champs orphelins — la moisson est maigre

Un champ est orphelin s'il est produit par un LLM, **et** jamais lu par le code, **et**
jamais publié dans le JSON. Vérifié par parcours récursif des 21 schémas passés à
`with_structured_output`, croisé avec le code puis avec les sorties réelles.

| Champ | Verdict |
|---|---|
| `AnalyseDocument.position_editoriale` (F3) | **Seul orphelin du pipeline.** 0 occurrence en sortie, aucune lecture. Le prompt lui consacre 3 lignes, le schéma une `description`, facturé sur 3 appels. |
| `Diagnostic.lecture_marche`, `.fenetre_opportunite`, `convergences.constat`… (F5) | **Pas orphelins.** Non publiés, mais `diagnostic.model_dump()` est resérialisé dans trois prompts F5 aval : facturés 4 fois, mais ils servent. |
| `proposition_valeur`, `impact_attendu`, `risques_associes`, `indicateurs_suivi`, `detail`… | **Publiés** (7 à 177 occurrences). Hors périmètre — les retirer retirerait de l'information. |

**Le Chantier 3.2 se réduit à un champ.** Les schémas sont propres.

---

## 7. Le protocole de non-régression, tel qu'écrit, ne peut pas s'appliquer

C'est le constat le plus gênant de ce chantier, et il faut le poser avant tout commit.

**Deux exécutions du même code, sur les mêmes fichiers d'entrée, avec les mêmes modèles à
température 0, ne produisent pas la même sortie.** Comparaison entre le run
`var/studies/d340f7e8-…` et le run de référence produit aujourd'hui — code identique côté
prompts, entrées identiques :

| | Run précédent | Run de référence | |
|---|--:|--:|---|
| Sentiment global | 48 / 13 / 6 / 4, base **71** | 49 / 14 / 5 / 5, base **73** | **écart** |
| `besoins` | 9 | **7** | **liste plus courte** |
| `faits_cles` | 10 | **9** | **liste plus courte** |
| `themes` | 12 | 12 | identique (plafond saturé) |
| `pain_points` | 15 | 15 | identique (plafond saturé) |
| `attentes`, `signaux_positifs`, `concurrents` | 8 / 8 / 50 | 8 / 8 / 50 | identiques |
| Verdict, score | `indetermine`, 5 | `indetermine`, 5 | identiques |

Conséquences directes sur les critères d'acceptation du prompt :

- **Tier A (« identiques à l'octet près ») est inatteignable, même à code inchangé.** Le
  critère ne peut pas servir de condition de validation, y compris pour les Chantiers 1
  et 4.1 dont la neutralité est pourtant garantie par construction.
- **Tier B : « tous les champs issus de `reduction.py` à l'octet près » inclut « toutes les
  répartitions de sentiment » — qui bougent déjà d'elles-mêmes.** La base de calcul est
  passée de 71 à 73 unités sans qu'une ligne de code change.
- **« Aucune liste de sortie plus courte qu'en baseline » refuserait la baseline
  elle-même** : `besoins` 9 → 7 et `faits_cles` 10 → 9.

La cause est connue et hors de notre portée : la température 0 borne l'échantillonnage,
elle ne rend pas l'inférence bit-reproductible côté fournisseur. Les agrégats de
`reduction.py` sont déterministes *à analyses données*, mais les analyses viennent du LLM.

**Ce que je propose à la place**, et sur quoi j'attends votre accord :

1. **Tier A → comparaison de la séquence de jetons, pas de la sortie.** Pour les Chantiers
   1 et 4.1, la preuve de neutralité est que la charge utile envoyée est identique octet
   pour octet (l'enregistreur de charges le vérifie directement, sans dépendre de la
   réponse). C'est plus fort que comparer des sorties, et c'est vérifiable sans dépenser.
2. **Tier B → bande de tolérance, calibrée sur le bruit mesuré ci-dessus.** Les invariants
   durs restent : verdict, score total, plafonds saturés, nombre de concurrents. Les
   effectifs de listes non plafonnées et les répartitions de sentiment sont comparés à
   ±3 % relatif, la valeur observée aujourd'hui entre deux runs identiques.
3. **Un second run à code inchangé** resserrerait cette bande (≈ 0,45 $ pour F3 seul). Je
   ne l'ai pas lancé : c'est une dépense que je ne prends pas seul.

---

## 8. L'écart à la cible, et ce qu'il reste

Cible : **≤ 1,45 $**. Mesuré : **1,9143 $**. Écart à combler : **0,4643 $, soit −24 %.**

Ce que les chantiers autorisés rapportent, chiffré sur les mesures ci-dessus :

| Chantier | Gain estimé | Assise |
|---|--:|---|
| **2** — émission par index (F4 puis F3) | **≈ 0,10 $** | Mesuré : 5 400 j sortie sonnet sur F4, 3 559 j sur F3 |
| **3.1** — vues par chaîne sur le dossier F5 | **0,06 – 0,09 $** | Mesuré : 50 735 j réémis = 0,152 $ ; hypothèse de moitié économisable |
| **4.1 + 4.2** — caching F4 et F7 | **0,046 $** | Calculé exactement, §3 |
| **3.2 + 3.3** — orphelin F3, mutualisation qualité | **≈ 0,01 $** | Un champ ; six appels collecteurs mutualisés |
| **5** — bornes de forme, déduplication `facteurs` | **0,02 – 0,05 $** | Non chiffrable avant implémentation |
| | **≈ 0,24 – 0,30 $** | |

> **La cible de 1,45 $ n'est pas atteignable avec le périmètre autorisé.**
> Les chantiers 1 à 6 conduisent à **≈ 1,62 – 1,68 $**, soit **−13 à −16 %**.

Ce n'est pas un échec d'exécution, c'est une conséquence de ce que la mesure a établi : la
facture est à 63 % de la sortie, et la sortie est l'analyse elle-même. Le prompt pose la
règle — « le gain vient du prix des jetons, jamais d'une coupe dans l'analyse » — et cette
règle plafonne mécaniquement le gain.

**Je ne prendrai aucune initiative de coupe.** Les options restantes relèvent toutes d'un
arbitrage qui vous appartient, et je les pose sans en recommander aucune :

- Le `tool_choice` sur F5 (§3), qui rendrait le dossier cachable : ≈ 0,12 $, au prix de
  cinq schémas exposés à cinq chaînes de jugement.
- Les plafonds de listes, saturés dans les sorties (12/12 thèmes, 15/15 pain points) :
  hors périmètre par votre décision, et je la maintiens telle quelle.
- Le choix de modèle par chaîne : hors périmètre, avec son propre protocole.

---

## 9. Constats de rigueur, tous vérifiés sur le run de référence

- **`confiance_globale.facteurs` : 20 entrées, aucun doublon exact, plusieurs quasi-doublons
  littéraux.** Deux listes concaténées sans déduplication — `facteurs_confiance` et
  `biais_probables` de `lecture_critique`. Appariements manifestes : *« Corpus
  majoritairement anglophone (Reddit)… »* / *« Biais linguistique : corpus majoritairement
  anglophone (Reddit)… »* ; *« Portée régionale (166/225)… »* / *« Biais géographique :
  SERP géolocalisée États-Unis… »*. **Chantier 5.4 confirmé.**
- **Un pain point publié avec `"description": ""`**, porteur d'un `score_priorite`, sans
  aucune alerte : `post_validation` en succès, `alertes_coherence` vide. **Chantier 5.2
  confirmé.**
- **Dénominateur des fréquences** : `nb_unites_analysees = 225`, `sentiment.global.base_nb
  = 71`, et toutes les fréquences sont calculées sur 71 (vérifié : 10/71 = 14,08 % ;
  8/71 = 11,27 % ; 6/71 = 8,45 %). Un lecteur du rapport F7 lira « 23,94 % » à côté de
  « 225 unités analysées ». **Chantier 7.1 confirmé** — la convention doit être déclarée
  dans `hypotheses`.
- **F3 passe 2 appels de `normalisation_libelles`** (thèmes et pain points), pas 1. Le
  document F3 a raison, le §2 du diagnostic a tort. **Chantier 7.2 confirmé.**
- **Plafonds saturés** : 12/12 thèmes, 15/15 pain points, sur les deux runs. Les toucher
  retirerait de l'information publiée.
- **`max_tokens` uniforme à 16 000** pour des chaînes dont la sortie réelle va de 484 à
  9 332 jetons. Aucune économie directe, mais aucun garde-fou non plus. **Chantier 5.3
  dimensionnable sur ces mesures.**

---

## 10. Ordre d'exécution proposé

| # | Chantier | Tier | Pourquoi ici |
|--:|---|---|---|
| 1 | **1** — comptabilité | A (par charge utile) | La ventilation du cache est déjà écrite, non commitée, jamais exécutée. La relire et l'exécuter d'abord : sans elle, les chantiers 4 économiseraient sans le montrer. Puis : attribution par chaîne, table de tarifs unique (12 exemplaires), champs jetons dans `StatutAnalyse`. |
| 2 | **2 sur F4** | B | Meilleur rapport du dépôt : 0,08 $ **et** suppression d'un mode d'échec qui frappe 67 % des preuves publiées. |
| 3 | **2 sur F3** | B | 0,03 $. Plus petit que prévu : les verbatims sont déjà injectés par le code. |
| 4 | **4.1 — F4 `analyse_concurrent`** | A | Le seul caching sans réordonnancement du dépôt. Gratuit, neutre par construction. |
| 5 | **3.1 — vues par chaîne sur le dossier F5** | B | Le plus gros poste d'entrée du pipeline : 50 735 jetons réémis. |
| 6 | **4.2 — F7** | B | Réordonnancement du système : 775 → 1 327 jetons de préfixe commun, au-dessus du seuil. |
| 7 | **5** — bornes de forme | B | `max_length`, `min_length`, `max_tokens` par chaîne, déduplication de `facteurs`. |
| 8 | **3.2 / 3.3** | B / C | Un champ orphelin ; mutualisation du contrôle qualité des six collecteurs. |
| 9 | **6** — `product_extraction` | B | Comptabilité et plafonds déjà faits. Restent le caching de la boucle agentique — **le seul préfixe du dépôt qui dépasse certainement son seuil** (512 sur Opus 5) — et le `use_agent=False` conditionnel. |
| 10 | **7** — corrections de rigueur | — | Dénominateur des fréquences ; correction documentaire du diagnostic. |
| — | ~~**4.3**~~ | ~~C~~ | **Abandonné** : 0,0158 $ pour 2 400 jetons inventés dans l'étage de classification. |

---

## 11. Reproduire ces mesures

Sorties de référence : `var/baseline/ceinture-lombaire-FR/` (gitignoré — **c'est ainsi que
l'étalon précédent a disparu ; à verser dans `docs/baseline/` si vous voulez qu'il
survive**). Charges utiles brutes : `_charges*.jsonl` dans le même dossier.

Scripts, hors dépôt (répertoire de session) :

| Script | Rôle |
|---|---|
| `sitecustomize.py` | Enregistreur de charges utiles, injecté par `PYTHONPATH`. N'altère rien. |
| `depouiller.py` | Décomposition exacte outil / système / charge utile par `count_tokens`, et détection du préfixe réellement commun à chaque famille d'appels. |
| `gisement.py` | Ce que le modèle recopie, en jetons de sortie mesurés. |
| `statique.py` | Tailles système / outil / `description`, par chaîne. |
| `orphelins.py` | Champs de sortie structurée jamais lus par le code. |
