# `agent_plc/` — F6 : classification de phase de cycle de vie

Module d'analyse **conditionnel**, en aval de F5. Il ne collecte rien et ne relit
aucune donnée brute de collecteur : ses entrées sont les sorties JSON de F5
(requise), F4 et F3 (optionnelles).

Lorsqu'un produit a été jugé à **potentiel positif** par F5, cet agent situe le
**marché de la catégorie** sur **une seule** phase de cycle de vie —
`introduction`, `croissance`, `maturite` ou `declin` — en croisant quatre
familles de signaux temporels, puis produit des recommandations **dédiées à
cette phase**.

Il ne remet jamais en cause le verdict de F5 : il le prolonge.

---

## 1. Place dans le pipeline

```
agent_insights_consommateurs (F3) ──┐
agent_analyse_concurrentielle (F4) ─┼──►  agent_recommandations_strategiques (F5)
tendances (collecteur) ─────────────┘                    │
                                            verdict_potentiel.declenche_plc
                                                         ▼
                                                   agent_plc (F6)
                                                         │
                                                         ▼
                                              agent_restitution (F7)
```

---

## 2. Usage

```bash
python main.py \
    --recommandations ../agent_recommandations_strategiques/output.json \
    [--insights ../agent_insights_consommateurs/output.json] \
    [--concurrence ../agent_analyse_concurrentielle/output.json] \
    [--forcer] [--langue-analyse fr] [--sortie output.json] [--stdout] [--verbose]
```

| Argument | Défaut | Rôle |
|---|---|---|
| `--recommandations` | — | **Requis.** Porte le verdict, `declenche_plc` et l'écho du dossier de synthèse |
| `--insights` | — | Alimente la famille « corpus d'avis » |
| `--concurrence` | — | Alimente les familles « dynamique publicitaire » et « structure de l'offre » |
| `--forcer` | absent | Classe malgré un verdict amont non positif. **Étude et test uniquement** |
| `--sortie` | `output.json` | Chaîne vide = n'écrire aucun fichier |

| Code de sortie | Signification |
|---|---|
| `0` | Succès — **y compris pour un non-déclenchement** |
| `1` | Erreur imprévue |
| `2` | Sortie F5 absente ou inexploitable, ou produits divergents |

`stdout` reste du **JSON pur**. **Prérequis** : `ANTHROPIC_API_KEY` (voir
`.env.example`). Aucun jeton Apify.

---

## 3. Le déclenchement conditionnel

La classification n'a lieu que si `verdict_potentiel.declenche_plc` vaut vrai
dans la sortie F5 — c'est-à-dire, en pratique, si le verdict est `positif`.

Dans tous les autres cas, l'agent produit une **sortie courte valide** :

```json
"declenchement": {
  "declenche_plc_amont": false,
  "mode": "non_declenche",
  "motif": "verdict amont « indetermine » : classification non déclenchée
            conformément au CDC (PLC uniquement si le potentiel est positif).
            Ce n'est pas une erreur."
}
```

`classification` vaut `null`, `signaux` et `recommandations_phase` sont vides,
`conditions_reexamen` reprend celles de F5, et le code de sortie est **0**.
**Aucun appel LLM n'est passé.** Le non-déclenchement est documenté, jamais
silencieux (exigence F6.4).

`--forcer` permet de classer malgré un verdict non positif. Le mode passe à
`force`, et une **limite de traçage** est insérée en tête de `limites` :

> Exécution forcée à des fins d'étude : le verdict amont n'est pas positif,
> cette classification ne doit pas être utilisée en décision. Le drapeau
> `--forcer` est interdit à l'orchestrateur en production.

---

## 4. ⚠️ La grille de lecture est une hypothèse de travail

**La grille de classification de phase n'est définie ni dans le cahier des
charges, ni dans la spécification fonctionnelle générale.** Ce module implémente
la grille de lecture indicative de la note de structuration des agents d'analyse
(§6) comme **hypothèse de travail**, signalée dans chaque sortie par
`statut_regle="hypothese_de_travail_a_valider"`.

### Les quatre familles de signaux

| Id | Famille | Source effective | Poids |
|---|---|---|---|
| `demande` | Trajectoire de la demande | écho Tendances du dossier F5 | 0,35 |
| `dynamique_publicitaire` | Dynamique des campagnes Meta | F4 en priorité, écho F5 à défaut | 0,30 |
| `structure_offre` | Structure et saturation de l'offre | F4 en priorité, écho F5 à défaut | 0,20 |
| `corpus_avis` | Récence et densité du corpus d'avis | F3 | 0,15 |

### Le partage des rôles

1. **Le modèle oriente** chaque famille disponible vers une phase (ou
   `neutre`), avec une force et une justification citant des `ref` du dossier.
2. **Le code corrige** : famille absente de la réponse → non évaluable ; famille
   orientée alors que ses signaux sont indisponibles → **forcée** non évaluable ;
   force ou phase hors vocabulaire → ramenée à une valeur admise.
3. **Le code décide** : `agreger` est une fonction pure — aucun appel réseau,
   aucun état, aucun aléa.
4. **La post-validation recalcule** la classification depuis les orientations
   publiées : toute divergence est corrigée au profit du code et tracée en alerte.

### La règle d'agrégation

```
score(phase) = Σ  POIDS_FAMILLES[f] × VALEUR_FORCE[force]
               f orientée vers phase, f évaluée

VALEUR_FORCE = {faible: 1, moyenne: 2, forte: 3}
« neutre » n'alimente aucune phase.

phase_probable = argmax(score), et vaut null si :
    nb_familles_evaluees < 2, ou aucun score > 0, ou égalité stricte en tête

incertitude = elevee   si écart relatif 1re/2e < 0,15
                       OU si une famille de poids ≥ 0,30 est non évaluable
              moyenne  si écart relatif < 0,40
              faible   au-delà
```

Tous ces seuils vivent dans `config.py`. `regle_appliquee` publie l'énoncé
littéral **avec les seuils effectifs** : si vous les modifiez, la sortie le dit.

### Déterminisme de l'agrégation — vérifié

Sur des orientations fabriquées, `agreger` rejoué deux fois produit exactement
la même classification :

| Cas | Résultat |
|---|---|
| Cas du run réel : demande → croissance/moyenne, offre → maturité/forte, corpus → croissance/moyenne, publicité non évaluable | `croissance`, incertitude `elevee`, scores `{croissance: 1.0, maturite: 0.6}`, 3 familles |
| Une seule famille évaluable (demande → croissance/forte) | `null`, incertitude `elevee`, 1 famille — sous le minimum de 2 |
| Deux familles quasi à égalité (0,35 contre 0,30) | `croissance`, incertitude `elevee` — l'écart relatif de 0,14 est sous le seuil |
| Quatre familles renseignées et concordantes | `croissance`, incertitude `faible`, score 2,35 |

---

## 5. Les pièges de lecture, opposables à toute conclusion

Injectés dans les prompts et rappelés dans chaque famille du dossier :

- **`date_fin` d'une annonce active** vaut la date du jour de collecte, jamais
  une date d'arrêt. Seul le drapeau `active` fait foi.
- **La longévité publicitaire** mesure une persistance de diffusion, jamais une
  rentabilité : une campagne longue n'est pas une campagne qui gagne.
- **L'indice Google Trends est relatif** à la période interrogée : aucun volume
  absolu de recherche, donc aucune taille de marché.
- **Les volumes de corpus** mesurent une activité de collecte, jamais un marché.
- **Une absence dans le corpus** est une absence d'observation, pas une absence
  de marché.

---

## 6. ⚠️ Dépendance connue : `dynamique_publicitaire` absente (exigence D4)

Au 06/08/2026, la sortie de F4 **ne contient pas**
`intensite_concurrentielle.dynamique_publicitaire`. La famille de signaux
correspondante est donc systématiquement `non_evaluable`, avec :

- un avertissement explicite dans le dossier PLC ;
- une limite en tête de `limites` ;
- une condition de réexamen générée par gabarit ;
- **une incertitude forcée à `elevee`**, cette famille pesant 0,30.

Elle n'est **jamais reconstituée localement**. Les seules données disponibles
sont des durées de diffusion, et le piège `date_fin` rend tout calcul local
d'ancienneté ou d'arrêt invalide. Le correctif appartient à F4 ; le contourner
ici produirait un chiffre faux présenté comme mesuré.

---

## 7. Matrice de dégradation

| Situation | Comportement |
|---|---|
| Verdict amont non positif, sans `--forcer` | Sortie courte de non-déclenchement, code 0, **aucun appel LLM** |
| `--forcer` | Classification produite, `mode="force"` + limite de traçage |
| F4 absente | `structure_offre` lue dans l'écho F5 (sans typologie de concurrents) ; `dynamique_publicitaire` non évaluable |
| F3 absente | `corpus_avis` non évaluable |
| Moins de 2 familles évaluables | `phase_probable=null`, incertitude `elevee`, aucune recommandation de phase |
| Chaîne d'orientation en échec | `phase_probable=null`, statut en échec, sortie complète |
| Chaîne de recommandations en échec | Liste vide + statut ; la classification est conservée |
| Aucune famille évaluable | `donnees_suffisantes=false` |

Quand aucune phase n'est retenue, **aucune recommandation de phase n'est
produite** : ce sont les conditions de réexamen qui prennent leur place.
Recommander sur une phase inconnue reviendrait à recommander au hasard.

---

## 8. Sortie — `ResultatPLC`

| Champ | Contenu |
|---|---|
| `declenchement` | `declenche_plc_amont`, `mode` (`normal` / `force` / `non_declenche`), `motif` |
| `dossier_plc` | Écho intégral des quatre familles : `disponible`, `source_effective`, `indicateurs[]` (`ref`, `libelle`, `valeur`, `detail`), `avertissements[]`. `null` si non déclenché |
| `signaux` | Une `OrientationSignal` par famille : `non_evaluable`, `orientation_phase`, `force`, `justification`, `fondements[]` |
| `classification` | `phase_probable`, `incertitude`, `scores_par_phase`, `nb_familles_evaluees`, `regle_appliquee`, `statut_regle`, `confiance`. **Tout est calculé par le code** |
| `recommandations_phase` | 3 à 6 recommandations `domaine="plc"`, chacune nommant la phase |
| `conditions_reexamen` | Gabarits de code + complément du modèle, dédoublonnés |
| `faits_cles` | Valeurs **recopiées par le code** depuis le dossier PLC |
| `confiance_globale` | Jamais supérieure à la plus faible des confiances amont |

---

## 9. Coûts et durée observés

Mesuré le 06/08/2026 sur le run *ashwagandha-supplement-ES*, avec `--forcer`
(verdict amont `indetermine`) :

| Scénario | Appels LLM | Coût estimé | Durée |
|---|---|---|---|
| Non-déclenchement (verdict non positif) | **0** | 0,00 $ | < 1 s |
| `--forcer`, F5 + F3 + F4 | 2 | ≈ 0,14 $ | 126 s |
| `--forcer`, F5 seule | 2 | ≈ 0,13 $ | 116 s |

Modèle : `claude-sonnet-4-5-20250929`, température 0, deux chaînes (orientation
des signaux, recommandations de phase). Tarif saisi à la main dans `config.py`,
à vérifier avant tout usage budgétaire.

---

## 10. Ce que ce module ne fait pas

- Aucune **collecte**, aucune lecture de sortie brute de collecteur, aucun appel
  réseau hors API Anthropic.
- Aucun **re-scoring du potentiel** : le verdict F5 fait foi et n'est ni
  recalculé, ni commenté, ni contredit.
- Aucune **recommandation généraliste** produit / prix / positionnement /
  marketing : c'est le rôle de F5.
- Aucune **conversion de devises**, aucune affirmation de taille ou de part de
  marché.
- Aucune **rédaction de rapport** : c'est le rôle de F7.
- Aucun test automatisé, aucun serveur, aucune persistance hors `--sortie`.
