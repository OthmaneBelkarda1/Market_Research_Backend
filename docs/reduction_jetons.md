# Réduction de la consommation de jetons — ce qui a été fait, et ce qui reste

> Suite de [`baseline_jetons.md`](baseline_jetons.md), qui reste le document de
> mesure de référence. Celui-ci est le document d'exécution : ce qui a changé dans
> le code, ce que ça rapporte, et ce qui a été écarté.
>
> Exécution : 02/09/2026. Toutes les valeurs viennent de
> `POST /v1/messages/count_tokens` (endpoint gratuit) appliqué aux charges utiles
> réellement capturées dans `var/baseline/ceinture-lombaire-FR/_charges*.jsonl`.
> Aucune estimation d'entrée. La seule projection est signalée comme telle (§2).

---

## 1. Périmètre, barre de qualité, référence

| | |
|---|---|
| **Périmètre** | Les 12 agents du dépôt : 11 modules d'étude de marché + `product_extraction`. Plateforme : API Anthropic première partie. |
| **Barre de qualité** | **Aucune évaluation automatisée n'existe.** La suite `tests/` ne couvre pas les sorties LLM et ne s'exécute pas hors d'une base Postgres. Conséquence directe : **aucun levier d'arbitrage n'a été appliqué**, seulement des leviers neutres en information. |
| **Référence** | 1,9143 $ par étude sur le run `ceinture-lombaire-FR` (61 appels F3→F7). `product_extraction` n'est pas dans ce chiffre, et les ~20 appels Haiku des 6 collecteurs non plus : le coût réel d'une étude est **supérieur** à 1,9143 $. |

---

## 2. La correction qui commande tous les chiffres : le tokeniseur

`claude-sonnet-5` n'utilise pas le même tokeniseur que `claude-sonnet-4-5`. La
documentation annonce « environ 30 % de jetons en plus pour le même texte » à
partir de la génération 4.7. **Mesuré sur les charges utiles de ce dépôt, français
et JSON mêlés : +18,45 %**, avec une dispersion large selon les chaînes.

| Chaîne | n | Sonnet 4.5 | Sonnet 5 | ratio |
|---|--:|--:|--:|--:|
| F5 `faits_cles_synthese` | 1 | 25 885 | 32 235 | 1,245 |
| F5 `recommandations` | 1 | 23 161 | 28 522 | 1,231 |
| F4 `analyse_concurrent` | 8 | 24 223 | 26 058 | **1,076** |
| F5 `opportunites_risques` | 1 | 18 542 | 22 920 | 1,236 |
| F3 `synthese_insights` | 1 | 18 512 | 21 507 | 1,162 |
| F7 `redaction_*` | 5 | 14 347 | 16 884 | 1,177 |
| F5 `notation_grille` | 1 | 13 455 | 16 659 | 1,238 |
| F5 `diagnostic_croise` | 1 | 13 300 | 16 438 | 1,236 |
| … | | | | |
| **Total entrée Sonnet** | **25** | **190 934** | **226 169** | **1,1845** |

**Ce ratio annule les deux tiers de la baisse de tarif.** Le calcul naïf
« 3/15 $ → 2/10 $ donc −33 % » est faux. Le gain réel est de −21,4 % sur la
facture Sonnet, pas −33 %. Tout chiffre de ce document en tient compte.

> Le ratio est mesuré sur l'**entrée**. Faute de pouvoir mesurer la sortie sans
> dépenser, il lui est appliqué tel quel : la sortie est de même nature
> (prose française et JSON). **C'est la seule projection du document.** Un run
> réel la remplacera par une mesure.

---

## 3. Ce qui a été appliqué

Quatre diffs, un par levier, tous **neutres en information** : la charge utile
transporte exactement les mêmes faits qu'avant.

### 3.1 Migration `claude-sonnet-4-5-20250929` → `claude-sonnet-5`

Cinq `config.py` : `agent_analyse_concurrentielle`, `agent_insights_consommateurs`,
`agent_plc`, `agent_recommandations_strategiques`, `agent_restitution`.

Le blocage inscrit dans les commentaires du code — *« sonnet-5 rejette
`temperature`, incompatible avec l'exigence de température 0 de la spécification »* —
**était sans objet** : le §7 de la baseline établit que deux exécutions du même
code sur les mêmes entrées produisent déjà des sorties différentes à température 0.
La température 0 n'achetait pas la reproductibilité que la spécification en
attendait ; elle ne la perd donc pas.

- `MODELES_SANS_ECHANTILLONNAGE` : `construire_modele` ne transmet `temperature`
  qu'aux modèles qui l'acceptent encore. Haiku 4.5 la garde, Sonnet 5 ne la reçoit pas.
- `RAISONNEMENT_SYNTHESE = {"type": "disabled"}` : Sonnet 5 pense par défaut, et
  les jetons de raisonnement sont facturés en sortie. Désactivé, le modèle se
  comporte comme Sonnet 4.5 — c'est ce qui rend la baisse de tarif **acquise**
  plutôt qu'espérée. Une constante nommée, pour que le passage à
  `{"type": "adaptive"}` + `output_config={"effort": …}` soit une ligne le jour
  où il y aura une évaluation pour l'arbitrer.
- Tables de tarifs : `(3.00, 15.00)` → `(2.00, 10.00)`.

**Gain mesuré : 0,3342 $ par étude (−17,5 %).**

### 3.2 Point de cache sur F4 `analyse_concurrent`

[`analyse.py`](../src/agents/market_study/agent_analyse_concurrentielle/analyse.py) —
`cache_control` explicite sur le bloc système.

C'est la **seule chaîne du dépôt** qui remplit les deux conditions du caching :
plus d'un appel (8, un par concurrent) **et** un préfixe au-dessus du seuil du
modèle. Mesuré : **1 778 jetons** de préfixe `tools` + `system`, contre 1 024
exigés par Sonnet 5. Le bloc système est identique d'un concurrent à l'autre —
`produit_nom` et `langue_analyse` sont constants sur une exécution — donc le
premier appel écrit (×1,25) et les sept suivants lisent (×0,10).

Le marqueur est posé sur le bloc système et non au niveau requête : l'appariement
se fait sur le préfixe rendu dans l'ordre `tools → system → messages`, et un
marqueur automatique se serait placé après la charge utile propre à chaque
concurrent — il aurait écrit huit entrées et n'en aurait lu aucune.

**Gain mesuré : 0,0215 $ par étude.**

*Limite connue* : `erreur_precedente` termine le bloc système et devient non vide
sur une reprise, ce qui invalide le cache pour cet appel. C'est rare et sans
conséquence ; le déplacer changerait la charge utile.

### 3.3 Hygiène d'entrée — JSON compact dans les prompts

26 sites dans 11 fichiers. `indent=1` → `separators=(",", ":")`, et
`model_dump_json(indent=1)` → `model_dump_json()`.

**L'indentation est facturée.** Mesuré par `count_tokens` sur les 61 charges
utiles réelles : **33 335 jetons d'entrée, soit 9,5 % de toute l'entrée du
pipeline**, pour zéro information — le modèle reçoit le même objet dans les deux
cas. C'est un levier que la baseline n'avait pas identifié, et il rapporte plus
que le caching.

Les documents de **sortie** restent indentés (`main.py`, `indent=2`) : eux sont
lus par des humains.

**Gain mesuré : 0,0574 $ par étude** (0,0481 $ Sonnet + 0,0093 $ Haiku).

### 3.4 `product_extraction` — caching de la boucle agentique

[`agent.py`](../src/agents/product_extraction/agent.py) — deux points de cache, et
un relèvement du plafond de sortie.

C'est la forme d'appel que la documentation Anthropic mesure comme la plus
rentable à cacher : une boucle qui réémet toute la conversation à chaque pas, avec
des résultats d'outil plafonnés à 18 000 caractères. Le coût croît comme le carré
du nombre de pas. **Aucun `cache_control` n'existait.**

1. `cache_control` au niveau requête : le pas *N+1* relit le préfixe du pas *N* à
   ×0,10 au lieu de ×1. C'est ce qui casse la croissance quadratique.
2. Point explicite sur le bloc système : cache le préfixe **statique** — prompt
   système et les trois schémas d'outils — identique d'une extraction à l'autre
   (`country` et les règles de variantes sont fixés par configuration, l'URL vit
   dans le message humain, après la coupure). Sans lui, le marqueur automatique se
   placerait après le premier message humain, dont l'URL change à chaque run, et
   aucune seconde extraction ne pourrait relire le préfixe.

`summarize_usage` ventile déjà lectures et écritures de cache : l'effet est
visible dans la CLI sans instrumentation supplémentaire.

`PRODUCT_MAX_OUTPUT_TOKENS` : 8 000 → 16 000. Ce n'est pas un réglage de coût mais
un garde-fou : le modèle ne le voit pas, et un run qui l'atteint est facturé
intégralement pour une réponse jetée. Opus 5 raisonne par défaut et mord sur le
même plafond.

**Gain non mesuré** — `product_extraction` n'a jamais été instrumenté. C'est
structurellement le plus gros gisement par extraction ; le chiffrer demande un run.

---

## 4. Résultat mesuré

Sur les 61 appels du run de référence, charges utiles réelles recomptées :

| | Entrée | Sortie | Coût |
|---|--:|--:|--:|
| Avant | 323 092 | 110 148 | **1,9143 $** |
| Après | 318 896 | 122 206 | **1,4976 $** |
| | | | **−0,4167 $ (−21,8 %)** |

La sortie **augmente en nombre de jetons** (+11 %, effet du tokeniseur) et
**baisse en coût** : c'est le tarif qui a changé, pas l'analyse. Aucune ligne
d'analyse n'a été retirée.

`product_extraction` s'ajoute à ce gain, non chiffré.

---

## 5. Ce qui a été écarté, et pourquoi

### Chantier 3.1 — « vues par chaîne sur le dossier F5 » : **l'hypothèse ne tient pas**

La baseline proposait de donner à chacune des cinq chaînes F5 la seule vue du
dossier dont elle a besoin, sur l'hypothèse que « la moitié » des 50 735 jetons
réémis serait économisable. Lecture des cinq prompts système :

| Chaîne | Ce qu'elle exige |
|---|---|
| `diagnostic_croise` | « confronte trois familles de signaux » — croise les trois branches par définition |
| `notation_grille` | note *chaque* critère de la grille, qui couvre demande, consommateur et concurrence |
| `recommandations` | produit (pain points), prix (benchmark), positionnement, marketing (plateformes) |
| `opportunites_risques` | « croise un angle peu exploité, un besoin non couvert ou une fenêtre de demande » |
| `faits_cles_synthese` | « les données du dossier les plus déterminantes », `ref` exacte à l'appui |

Les cinq citent des `ref` qui peuvent venir de n'importe quelle branche, et le
code rejette toute `ref` absente du dossier transmis. **Restreindre la vue d'une
chaîne, c'est lui retirer de l'information qu'elle a pour consigne d'utiliser.**
Le chantier est incompatible avec la règle « sans perte d'information ». Écarté.

Le §3.3 ci-dessus s'applique au même endroit et rapporte davantage, sans arbitrage.

### Caching sur F5 : structurellement impossible

Confirmé. Les cinq chaînes ont cinq schémas de sortie donc cinq définitions
d'outil ; `tools` est rendu **avant** `system`, et une définition d'outil
divergente empêche toute lecture de cache en aval. Aucun placement de marqueur ne
contourne cela. L'échappatoire `tool_choice` évoquée par la baseline exposerait
cinq schémas à cinq chaînes de jugement : c'est un arbitrage, pas un levier neutre.

### Blocs `RÉFÉRENCES CITABLES` : duplication, mais utile

3 490 caractères de refs, déjà présentes dans le dossier du même prompt, envoyées
sur quatre chaînes. La liste explicite sert cependant à borner l'ensemble citable
(« toute autre ref sera rejetée ») ; la retirer augmenterait probablement les refs
inventées, que le code retire ensuite — donc une perte d'information. Non touché.

### Tous les leviers d'arbitrage

`effort`, choix de modèle par chaîne, plafonds de listes, `thinking: adaptive` :
**aucun n'a été appliqué**, faute d'évaluation permettant de distinguer une
économie d'une régression (§1).

---

## 6. Ce qui reste sur la table

| Levier | Type | Plafond | Prérequis |
|---|---|--:|---|
| **Batch API** sur les 5 éventails indépendants (`carte_unites` ×15, `carte_documents` ×3, `extraction_attributs` ×16, `analyse_concurrent` ×8, `redaction_*` ×5) | gratuit | **−50 % sur ces appels**, cumulable avec le cache | accepter l'asynchrone (fenêtre 24 h) et refondre les modules en soumettre/interroger |
| **Chantier 2** — émission par index au lieu de recopie (F4 puis F3) | gratuit **+ correction** | ≈ −0,04 $ | corrige au passage les **67 % de preuves F4** que la post-validation doit réécrire |
| **Mesurer `product_extraction`** | mesure | — | un run instrumenté ; `summarize_usage` est déjà en place |
| **Balayage `effort` sur Sonnet 5** | arbitrage | inconnu | une évaluation (§1) |
| **Table de tarifs unique** | dette | 0 $ | 12 exemplaires dans le dépôt, dont deux viennent d'être corrigés à la main |

---

## 7. Validation — ce qu'il faut avant de considérer ceci acquis

Rien de ce qui précède n'a été exécuté contre l'API en inférence. Les mesures
viennent de `count_tokens`, qui ne fait pas tourner le modèle.

1. **Un run complet sur `ceinture-lombaire-FR`** (≈ 1,50 $) confirmerait le coût
   mesuré et remplacerait la projection du §2 par une mesure de la sortie.
2. **Le protocole de non-régression du §7 de la baseline reste valable** : Tier A
   par comparaison de charges utiles, Tier B en bande de tolérance. Le levier 3.1
   (JSON compact) change la charge utile : il relève du Tier B, pas du Tier A.
3. **Vérifier `cache_read_input_tokens` > 0** sur les appels 2 à 8 de
   `analyse_concurrent` et sur les pas 2+ de `product_extraction`. Un zéro
   signifierait un invalidateur silencieux, pas une économie manquée.

---

## 8. Reproduire les mesures

Scripts hors dépôt (répertoire de session) :

| Script | Rôle |
|---|---|
| `tokeniseur.py` | Ratio de tokenisation Sonnet 4.5 → Sonnet 5, chaîne par chaîne |
| `compact.py` | Coût en jetons de l'indentation JSON, sur les 61 charges utiles |
| `recalcul.py` | Gain du levier 1 et taille du préfixe cachable de F4 |
| `final.py` | Effet combiné, avant/après |

Ils lisent tous `var/baseline/ceinture-lombaire-FR/_charges*.jsonl`, produits par
l'enregistreur décrit au §11 de la baseline. **Ce dossier est gitignoré** : c'est
ainsi que l'étalon précédent a disparu.
