# Before You Start, version LangGraph (branche `langgraph`)

Le même produit que `main` : même API FastAPI, même interface, même base Postgres et même serveur MCP. Seule l'**orchestration des agents** change. Sur `main`, elle est écrite à la main en `asyncio`. Ici, elle passe par **LangGraph**, et le chat utilise **LangChain** pour le modèle et les tools.

## Où lire le code (dans cet ordre)

| Fichier | Contenu |
|---|---|
| `backend/app/agents/state.py` | L'état partagé (`HuntState`, `ChatState`) et les **reducers** |
| `backend/app/agents/nodes.py` | Un nœud par agent : scout, researcher, geocoder, analyst, strategist, finish, planner |
| `backend/app/agents/hunt_graph.py` | Le graphe de recherche : nœuds, arêtes, fan-out `Send`, lancement et reprise |
| `backend/app/agents/chat_graph.py` | L'agent de chat ReAct : tools LangChain, boucle LangGraph, cache sémantique |
| `backend/app/agents/checkpointer.py` | Où l'état est sauvegardé : Postgres sur Aiven, ou en mémoire |
| `backend/app/pipeline/hunt.py` | Le **travail** de chaque agent (recherche, lecture, extraction, analyse), appelé par les nœuds |

Les nœuds sont courts : ils lisent le state, appellent une fonction de `hunt.py`, et renvoient seulement ce qu'ils ont changé.

## 1. Le graphe de recherche

```
START → scout ──Send×N──► researcher ×N ─┐
              └─Send────► geocoder ───────┴─► analyst → strategist → finish → planner → END
```

Exécution réelle (sortie de `scripts/graph_check.py`) :

```
step 0: scout
step 1: researcher, researcher, researcher, researcher, researcher, researcher, researcher, geocoder
step 2: analyst
step 3: strategist
step 4: finish
step 5: planner
```

- **Le state** est la mémoire commune du graphe. Chaque nœud renvoie un morceau de state, que LangGraph fusionne.
- **Les reducers** fixent la règle de fusion : `facts: Annotated[list, operator.add]` concatène au lieu de remplacer. C'est ce qui permet aux 7 Researchers d'écrire **en même temps** sans s'écraser. `locations` utilise un reducer qui fusionne les dictionnaires.
- **`Send`, le fan-out** : après le Scout, `fan_out()` renvoie un `Send("researcher", {...})` par concurrent, plus un pour le Geocoder. Chaque `Send` lance une copie du nœud avec **sa propre petite entrée**, et pas tout le state. Toutes ces copies sont dans le **même step** (le *superstep*), donc elles tournent en parallèle.
- **Le fan-in** : `analyst` est relié à `researcher` et à `geocoder`. LangGraph attend la **fin du step**, donc que tous aient fini, puis lance l'Analyst **une seule fois**.
- **Le parallélisme à deux niveaux** :
  - entre agents, c'est LangGraph (`Send`) ;
  - dans un même Researcher (ses 4 pages, sa recherche d'avis), c'est `asyncio.gather` ;
  - c'est de l'**asynchrone** sur un seul thread : les agents attendent le réseau, ils ne calculent pas.
- **Un nœud ne lève jamais d'exception pour un seul concurrent.** Un Researcher qui échoue renvoie ses faits partiels, parce qu'une exception dans un nœud ferait échouer tout le step.
- **Le Planner vient après `finish`.** Le rapport s'affiche d'abord, puis le business plan s'écrit pendant que l'utilisateur lit.
- **Le direct passe toujours par nos événements.** Chaque agent appelle `bus.emit()`, qui alimente le SSE et l'animation de l'interface. LangGraph orchestre, il ne remplace pas notre flux d'événements.

## 2. Le checkpointer et la reprise

- `compile(checkpointer=...)` sauvegarde le state **après chaque step**, sous un `thread_id`, qui est l'identifiant de la recherche.
- Avec `DATABASE_URL`, c'est un `AsyncPostgresSaver` **dans la même base Aiven**, dans ses propres tables créées par `setup()`. Sans `DATABASE_URL`, c'est un `InMemorySaver`.
- **Reprise après crash** : `POST /api/research/{id}/resume` appelle `graph.ainvoke(None, config)`. Une entrée `None` veut dire « continue depuis le checkpoint ». Testé sur Aiven : crash pendant l'Analyst, « redémarrage », reprise à l'Analyst avec les 42 faits. **Ni le Scout ni les Researchers ne sont relancés.**
- **Les pending writes** : si 5 Researchers sur 7 ont fini au moment du crash, leurs résultats sont déjà sauvegardés. Seuls les 2 autres sont relancés.
- Le state ne contient que du JSON (des dicts). L'Analyst stocke `analysis.model_dump()`, et le Strategist le relit avec `Analysis.model_validate(...)`.

## 3. Le chat : ReAct avec LangGraph et LangChain

```
START → agent ──tool_calls ?── oui ──► tools (ToolNode) ──► agent   (boucle)
                     └──────── non ──► END (réponse finale)
```

- **LangChain** fournit les briques dans les nœuds :
  - `ChatAnthropic(model, effort="low")` est le modèle ;
  - `@tool` transforme nos fonctions en tools : la docstring et les arguments sont ce que voit le modèle ;
  - `.bind_tools(TOOLS)` dit au modèle quels tools existent.
- **LangGraph** fournit la boucle :
  - le nœud `agent` appelle le modèle ;
  - `route()` envoie vers `tools` s'il y a des `tool_calls`, sinon vers `END` ;
  - `ToolNode` exécute **en parallèle** tous les appels d'un même tour, puis renvoie vers `agent`.
- **Les 4 tools** :
  - `search_facts` : recherche dans **tous** les faits stockés, lecture seule. Le prompt n'en montre que 40 par concurrent ;
  - `web_research`, `dig_into_company`, `add_company` : vont sur le web et **sauvegardent** les nouveaux faits.
- **`InjectedState("research_id")`** : le tool reçoit l'identifiant de la recherche depuis le state, mais **le modèle ne le voit pas**, il n'apparaît pas dans le schéma du tool. Le modèle ne peut donc pas viser une autre recherche.
- **`response_format="content_and_artifact"`** : le modèle reçoit le contenu (les faits). Notre code reçoit l'**artifact** : les étapes à afficher, les sources et les entreprises à surligner.
- **La mémoire courte** : le checkpointer garde la conversation, avec un thread `chat-{id}` par recherche. Au 2e message, le modèle voit le tour précédent, y compris les appels de tools. `recent_turns()` limite la fenêtre aux 4 derniers échanges, et coupe toujours sur un message utilisateur pour ne jamais séparer un résultat de tool de son appel.
- **Les garde-fous** :
  - `recursion_limit` plafonne à 6 tours de tools ;
  - le contenu des pages est traité comme des données, jamais comme des instructions ;
  - les agents qui lisent les pages n'ont aucun tool d'action.

## 4. Le cache : quoi, avec quelle clé, combien de temps

| Niveau | Clé | Durée | Évite |
|---|---|---|---|
| Recherche web | la requête | 6 h | de repayer Keenable |
| Pages | l'URL | 6 h | de retélécharger |
| Géocodage | l'adresse | 30 jours | de rappeler Nominatim |
| **Réponses LLM** (nouveau, `llm.py`) | hash de (modèle, effort, schéma, prompt) | 24 h (`LLM_CACHE_HOURS`) | un appel Claude identique |
| **Chat sémantique** (nouveau) | embedding de la question + **nombre de faits** | 24 h | de répondre deux fois à la même question |
| Hash de contenu (Postgres) | hash du texte de la page | tant que la page ne change pas | de réextraire une page inchangée |
| Mémoire des entreprises (Postgres) | identifiant de l'entreprise | permanent | de refaire la recherche sur un concurrent connu |
| Prompt caching Anthropic | préfixe du prompt système | ~5 min | de repayer les longs prompts |

- Tout passe par `providers/cache.py`. Il utilise **Valkey** (Redis sur Aiven) si `VALKEY_URL` est défini, sinon un dictionnaire en mémoire limité à 5 000 entrées. L'interface `get` / `set` est la même dans les deux cas. Valkey devient nécessaire avec plusieurs process, car il est partagé et survit aux redémarrages.
- **L'invalidation du cache de chat** : la clé contient le nombre de faits du projet. Dès qu'un fait est ajouté, les anciennes réponses ne sont plus servies. Une réponse qui a elle-même ajouté des faits n'est jamais mise en cache. Le seuil de similarité est de 0,95 pour éviter de confondre deux questions proches.
- **Pourquoi pas le cache par nœud de LangGraph (`CachePolicy`) ?** Nos nœuds ont des **effets de bord** : ils écrivent en base et émettent les événements de l'interface. Un nœud servi depuis le cache sauterait ces effets, et l'interface n'afficherait rien. Le cache est donc placé **sous** les nœuds, au niveau des appels LLM et web, qui sont purs.
- **Le compromis fraîcheur contre coût** : on refetch souvent, ce qui est peu cher, mais on ne repaye le LLM que si la page a vraiment changé, grâce au hash de contenu.

## 5. Observabilité

- La table `llm_calls` loggue chaque appel : agent, modèle, tokens, latence et coût.
- **LangSmith (optionnel)** : avec `LANGSMITH_TRACING=true` et `LANGSMITH_API_KEY`, chaque exécution d'un graphe est tracée : chaque nœud avec son entrée et sa sortie, chaque tool et chaque appel du modèle de chat. Les runs sont nommés `hunt` et `chat` et portent le `research_id` dans leurs métadonnées.

## 6. Ce qui n'a pas changé

- **Continue, Enrich, Watcher et Upgrader** utilisent toujours `asyncio` directement, avec les mêmes fonctions d'agent. On pourrait les porter en graphes plus tard.
- **L'API FastAPI, l'interface, le SSE, le MCP et la voix** sont identiques. Il y a une seule nouvelle route, `POST /api/research/{id}/resume`.

## Tester

```bash
cd backend
uv pip install -p .venv/bin/python -r requirements.txt
# hors ligne (sans clés) : tout le parcours de l'API
MOCK_DELAY=0.05 ANTHROPIC_API_KEY= OPENAI_API_KEY= DATABASE_URL= .venv/bin/python -m scripts.smoke
# spécifique LangGraph : dessin des graphes, steps parallèles, crash puis reprise
MOCK_DELAY=0.3 SEED_DEMO=0 ANTHROPIC_API_KEY= OPENAI_API_KEY= DATABASE_URL= .venv/bin/python -m scripts.graph_check
# l'app
.venv/bin/uvicorn app.main:app --port 8000
```

## Ce que LangGraph a apporté, et ce qu'il n'a pas apporté

- **Apporté** : la reprise après crash sans rien refaire, le parallélisme déclaratif (`Send` et reducers au lieu de `gather` et de dicts mutés), une conversation de chat persistante, la traçabilité, et le dessin du graphe.
- **Pas apporté** : de la vitesse. Le goulot reste le LLM et le web. Une recherche prend le même temps.
- **Le prix** : une dépendance de plus, et des règles à respecter : un state sérialisable, des nœuds qui ne lèvent pas d'exception, attention aux effets de bord avec le cache.
