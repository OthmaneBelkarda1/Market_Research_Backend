# Fixtures de rejeu de F7

Deux runs réels, exportés de la base, pour rejouer l'agent de restitution sans
relancer une collecte — vingt minutes de collecteurs et quatre analyses LLM.

| Dossier | Run | Ce qu'il montre |
|---|---|---|
| `8609db9e/` | *Wireless Lavalier Microphone Type-C*, ES, 03/09/2026 | Le cas du correctif : quatre chaînes de rédaction en échec sur `KeyError: max_mots`, rapport livré sans une phrase d'analyse, étude marquée `completed`. AliExpress y a rapporté **zéro offre** en étant enregistré `succeeded` |
| `ceinture-lombaire-FR/` | *Ceinture lombaire double traction*, FR, 06/08/2026 | Le run de référence, nominal : six sources renseignées, verdict positif |

Chaque dossier porte les quatre sorties d'analyse que F7 consomme
(`insights.json`, `concurrence.json`, `recommandations.json`, `plc.json`) et
l'état des collecteurs (`sources_etat.json`), qui porte la **raison** d'une
collecte vide — la seule information que les JSON d'analyse ne conservent pas.

## Rejouer

```bash
cd src/agents/market_study/agent_restitution
F=../../../../tests/fixtures/8609db9e
python main.py \
    --recommandations $F/recommandations.json \
    --insights $F/insights.json \
    --concurrence $F/concurrence.json \
    --plc $F/plc.json \
    --sources-etat $F/sources_etat.json \
    --rapport rapport.md --resume resume.md --sortie sortie.json
```

Un rejeu appelle le modèle : compter environ 0,08 à 0,11 $ et une minute.
`ANTHROPIC_API_KEY` est requise.

Les tests de `tests/studies/test_restitution_v2.py` ne les utilisent pas : ils
tiennent sans réseau ni base. Ces fixtures servent la vérification de bout en
bout, celle qui produit un rapport à relire.
