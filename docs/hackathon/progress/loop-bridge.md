# Pont MCP Apps vers permissions et agent loop

## Cartographie vérifiée

### Appel d’outil décidé par le modèle

Le chemin canonique observé est :

1. le backend produit un `LLMMessage` avec des `tool_calls` ;
2. `APIToolFormatHandler.parse_message` décode les arguments JSON en
   `ParsedToolCall` ;
3. `APIToolFormatHandler.resolve_tool_calls` résout la classe dans
   `ToolManager.available_tools`, valide les arguments avec le modèle Pydantic de
   l’outil et produit un `ResolvedToolCall`, ou un `FailedToolCall` ;
4. `AgentLoop._handle_tool_calls` publie le `ToolCallEvent` complet ;
5. `AgentLoop._run_tools_concurrently` crée une tâche par appel et relaie les
   événements par une `asyncio.Queue` ;
6. `AgentLoop._process_one_tool_call` ouvre le span, puis
   `AgentLoop._execute_tool_call` résout l’instance via `ToolManager.get` ;
7. `_run_pre_tool_pipeline` sérialise l’entrée, exécute les hooks `pre_tool`,
   revalide chaque réécriture et peut produire un refus typé ;
8. `_should_execute_tool` applique le bypass éventuel, puis sous le verrou du
   `PermissionStore` la permission granulaire de `BaseTool.resolve_permission` ou
   la permission configurée : `ALWAYS` exécute, `NEVER` refuse, `ASK` consulte les
   règles de session puis `approval_callback` ; sans callback, `ASK` refuse ;
9. `_invoke_tool` capture l’éventuel snapshot et construit l’`InvokeContext` avec
   identifiant d’appel, agent/session, callbacks d’approbation et d’entrée,
   sampling, plan, changement d’agent, clear context, skills, scratchpad,
   `PermissionStore`, hooks, session et pool MCP ;
10. `BaseTool.invoke` revalide les arguments, puis appelle `run` ; les
    `ToolStreamEvent` sont publiés au fil de l’eau et le dernier modèle Pydantic
    devient le résultat ;
11. les hooks `post_tool` reçoivent succès, échec ou annulation et peuvent
    remplacer le texte destiné au modèle ;
12. un `ToolResultEvent` terminal expose résultat, erreur, refus, durée ou
    annulation. Le chemin modèle ajoute ensuite le message `role=tool`, met à jour
    statistiques/télémétrie et permet au tour suivant de continuer ;
13. l’annulation du consommateur annule les tâches d’outil. Une annulation avant
    le corps ne lance pas `post_tool`; une annulation après démarrage finalise les
    hooks sous `asyncio.shield`, publie un résultat annulé et referme les tâches.

Les erreurs de résolution/validation deviennent des `ToolResultEvent` sans
classe/résultat. `ToolError` devient une erreur utilisateur. `ToolPermissionError`
est distinguée comme refus de permission. Les événements de hooks et de flux
gardent le même `tool_call_id` que l’appel et le résultat.

### Nouveau tour utilisateur et queue existante

Le chemin Textual observé est
`on_chat_input_container_submitted` → `_dispatch_idle_input` →
`_handle_user_message` → `_handle_agent_loop_turn` → `AgentLoop.act` →
`_conversation_loop` → `_open_user_turn`. `_open_user_turn` crée le message
`role=user`, l’ajoute à `MessageList`, conserve la métadonnée d’affichage, puis
publie `UserMessageEvent`. `_conversation_loop` sauvegarde la session après les
tours et dans son `finally`.

Pendant un tour actif, la CLI appelle `QueueController.enqueue_prompt`.
`MessageQueue` conserve les éléments FIFO. Le drain ne démarre que sans agent ni
bash actif et hors pause. Lorsqu’il regroupe plusieurs prompts, les éléments de
tête passent par `AgentLoop.inject_user_context(as_message=True)` et le dernier
démarre un vrai `AgentLoop.act`. Dans le `finally` du tour, la CLI libère son état
actif puis appelle `start_drain_if_needed`.

Une annulation du tour libère la tâche CLI dans le même `finally`; le drain peut
alors reprendre. La fermeture arrête le drain, annule/attend les tâches puis ferme
`AgentLoop`. Les reprises rechargent `MessageList` et changent l’identité de
session par les APIs de session, sans mutation externe de liste privée.

## APIs ajoutées

### `AgentLoop.execute_tool`

`execute_tool(tool_name, arguments)` est un générateur asynchrone public. Il
réutilise `APIToolFormatHandler` pour la résolution/validation et le pipeline
canonique pour hooks, permissions, `InvokeContext`, snapshots, télémétrie,
streaming, erreurs et annulation. Il publie `ToolCallEvent`, événements de hooks,
`ToolStreamEvent` et `ToolResultEvent`.

L’appel hors bande ne crée aucun message `role=tool` : le pipeline a désormais un
mode interne `record_response=False` qui désactive seulement l’écriture du
transcript, sans désactiver hooks, permissions, événements ou télémétrie.

### `AgentLoop.submit_user_message`

`submit_user_message(message, context=None, source="external")` produit un vrai
nouveau tour via `AgentLoop.act`. Le contexte JSON est :

- rendu explicitement dans le contenu utilisateur sous un bloc JSON intitulé
  `Context supplied by <source>` afin qu’il soit visible au modèle et à l’humain ;
- conservé dans `UserDisplayContentMetadata` pour le transcript et les surfaces.

Il n’y a aucune injection système, aucun accès à `MessageList` depuis
l’adaptateur et aucune mutation de `_pending_injected_messages`.

### Sérialisation et adaptateur

`vibe.core.mcp_apps` expose :

- `MCPAppAdapter` et `build_mcp_app_callbacks` ;
- les callbacks `call_tool(tool_name, arguments)` et
  `send_user_message(message, context)` compatibles avec le host ;
- `ToolCallResult` avec les statuts `success`, `error`, `permission_denied` et
  `cancelled`, un contenu JSON, le `structured_content` MCP lorsqu’il existe,
  l’erreur utilisateur et l’identifiant de corrélation ;
- `UserMessageResult` avec statut, identifiant de message et indication de mise
  en attente.

L’adaptateur ne dépend ni du CLI, ni du SDK/client MCP. Un `on_event` asynchrone
optionnel permet à la surface propriétaire d’afficher exactement les événements
de l’appel ou du nouveau tour.

## Concurrence, annulation et cycle de vie

`AgentLoop.act` et `AgentLoop.execute_tool` partagent un verrou d’orchestration.
Un tour, un appel App et les demandes App concurrentes sont donc sérialisés en
FIFO par `asyncio.Lock`; aucun second tour ou outil hors bande ne s’exécute en
parallèle. Une demande soumise pendant un tour attend sa terminaison complète.

Annuler une demande en attente la retire sans exécution. Annuler l’opération
active libère le verrou dans les `finally`; la demande suivante reprend. Un outil
qui signale sa propre annulation retourne le statut sérialisable `cancelled`.
Après `AgentLoop.aclose`, toute nouvelle opération publique est refusée avec
`AgentLoopStateError`; l’adaptateur traduit la soumission de message en statut
`closed` et l’appel d’outil en erreur sérialisable.

## Couverture ajoutée

Les tests de `tests/core/mcp_apps/test_adapter.py` couvrent : outil connu/inconnu,
arguments valides/invalides, `ALWAYS`, `ASK` accepté/refusé, `NEVER`, hooks et
événements de hooks, résultat progressif et structuré, `ToolError`,
`ToolPermissionError`, annulation, absence de message outil orphelin, visibilité
des événements, message au repos, message pendant un tour, FIFO, contexte rendu
et persisté, round-trip par `SessionLoader`, reprise après annulation, fermeture de
session, deux appels outils UI concurrents et absence de double exécution.

## Limites

- Ce patch construit les callbacks mais ne démarre pas `MCPAppHost` et ne modifie
  pas son cycle de vie Textual.
- La queue Textual reste propriétaire des entrées clavier et bash. Le pont
  externe utilise le verrou d’orchestration public du core, sans modifier le
  contrôleur CLI interdit ; l’ordre est garanti entre opérations déjà soumises au
  core, pas entre une demande externe et un prompt encore stocké uniquement dans
  la queue Textual.
- Une annulation imposée par le transport à la coroutine du callback se propage
  comme `CancelledError`; le statut JSON `cancelled` concerne un outil ayant
  produit un résultat d’annulation observable.
