# Statut — Cartographie MCP Apps

## Périmètre et état

- Mission d'analyse uniquement ; aucun fichier de production ou de test ne sera modifié.
- Lectures obligatoires terminées : `AGENTS.md` et ADR 0001, 0002, 0003, 0004, 0007.
- Modifications préexistantes préservées : `uv.lock` modifié et `TASK.md` non suivi.
- Cartographie du core MCP, des permissions, des événements et de la queue Textual vérifiée.
- Inventaire détaillé des tests terminé et livrable principal rédigé.

## Faits vérifiés

### Découverte et identité MCP

- `AgentLoop.__init__` crée ou reçoit `MCPRegistry`, puis le transmet à `ToolManager` (`vibe/core/agent_loop/_loop.py`).
- `ToolManager._integrate_mcp_async` demande les outils configurés à `MCPRegistry.get_tools_async`, puis enregistre les classes retournées dans `_all_tools` (`vibe/core/tools/manager.py`).
- `MCPRegistry._discover_http` et `MCPRegistry._discover_stdio` appellent respectivement `list_tools_http` et `list_tools_stdio`, puis les factories de proxy (`vibe/core/tools/mcp/registry.py`).
- `list_tools_http` et `list_tools_stdio` appellent `ClientSession.list_tools`; chaque outil SDK est validé en `RemoteTool` (`vibe/core/tools/mcp/tools.py`).
- Les factories créent une sous-classe dynamique de `MCPTool`. Elle conserve l'alias dans `_server_name`, le nom distant dans `_remote_name`, le schéma dans `_input_schema`, et publie `alias_nomDistant` (`vibe/core/tools/mcp/tools.py`, `vibe/core/tools/remote.py`).
- `RemoteTool` ne conserve aujourd'hui que `name`, `description` et `input_schema`. La métadonnée `_meta.ui.resourceUri` est donc perdue lors de `RemoteTool.model_validate`.
- `MCPToolResult.server` contient actuellement l'URL ou `stdio:<commande>`, pas nécessairement l'alias configuré. L'alias fiable au runtime vient de `MCPTool.get_server_name()` via `ToolCallEvent.tool_class`/`ToolResultEvent.tool_class`.

### Exécution, permissions et événements

- Le chemin modèle est : réponse LLM → `APIToolFormatHandler.parse_message` → `resolve_tool_calls` → `AgentLoop._handle_tool_calls` → `_run_tools_concurrently` → `_process_one_tool_call` → `_execute_tool_call` (`vibe/core/llm/format.py`, `vibe/core/agent_loop/_loop.py`).
- Les arguments validés sont dans `ResolvedToolCall.validated_args`/`args_dict`, puis dans `ToolCallEvent.args`; les arguments bruts existent auparavant dans `ParsedToolCall.raw_args`.
- `_execute_tool_call` exécute hooks pré-outil, permission, puis `_invoke_tool`. `_invoke_tool` construit `InvokeContext`, appelle `BaseTool.invoke`, émet les flux et le `ToolResultEvent`, puis exécute la finalisation post-outil.
- `AgentLoop._should_execute_tool` est l'unique décision commune observée pour `ALWAYS`, `NEVER`, `ASK`, règles de session et callback d'approbation. Appeler directement `BaseTool.invoke`, `call_tool_stdio/http` ou le client MCP contournerait cette décision.
- Aucune API publique n'expose aujourd'hui ce pipeline pour un appel arbitraire initié par une MCP App. Les seules entrées sont des méthodes privées de `AgentLoop`; une future API publique surface-neutre est nécessaire.
- Événements observés : `ToolCallEvent` (parfois d'abord partiel en streaming, puis enrichi avec `args`), `ToolStreamEvent`, `ToolResultEvent` (résultat, erreur, refus, annulation), plus les événements de hooks.

### Conversation et Textual

- Un tour utilisateur normal entre par `VibeApp._handle_user_message`, puis `VibeApp._handle_agent_loop_turn`, puis `AgentLoop.act`/`_open_user_turn`; ce dernier ajoute `LLMMessage(role=user)` et émet `UserMessageEvent`.
- Pendant un travail actif, `VibeApp._handle_queue_submit` envoie les prompts à `QueueController.enqueue_prompt`. Le drain démarre seulement quand le tour actif est terminé (`VibeApp._handle_agent_loop_turn` appelle `start_drain_if_needed` dans son `finally`).
- `AgentLoop.inject_user_context(as_message=True)` sert aux éléments groupés en tête d'une queue. Il ajoute directement un message et ne doit pas être appelé concurremment depuis le host sans passer par l'orchestrateur de queue.
- `_pending_injected_messages` est privé et représente du contexte injecté au sein du même tour ; ce n'est pas le contrat adapté à `send_user_message`.
- `UserDisplayContentMetadata` permet déjà de persister une provenance structurée sur `LLMMessage`, mais cette métadonnée est exclue du payload LLM. La sélection candidature/paragraphe doit donc aussi être rendue dans le contenu utile au modèle.
- `VibeApp` est créée par `run_textual_ui`; `on_mount` installe les callbacks et workers. `_run_app_with_cleanup` garantit `shutdown_cleanup`, qui ferme tâches, managers et `AgentLoop`.
- La reprise locale remplace le `session_id` dans `VibeApp._resume_local_session`; clear/compaction changent aussi l'identité via `AgentLoop._reset_session`. Le host devra être fermé à ces frontières.
- `webbrowser.open` est déjà utilisé par les écrans OAuth/connector, mais l'ouverture du futur navigateur doit être hors du thread UI (par exemple `asyncio.to_thread`) pour respecter les règles de réactivité.

## Décisions recommandées à documenter

- Core MCP : préserver `resourceUri` sur `RemoteTool`, le propager sur la classe `MCPTool`, et fournir une lecture typée de ressource par alias sans exposer `ClientSession` au navigateur.
- Core agent : fournir une API publique d'exécution d'outil qui réutilise validation, hooks, permission, `InvokeContext`, événements, erreurs et annulation.
- CLI : ajouter un orchestrateur dédié consommant les événements, corrélant appel/résultat par `tool_call_id`, lisant `ui://`, pilotant un unique `MCPAppHost` et injectant les messages via la queue.
- Host : rester un adaptateur navigateur, sans accès direct au client MCP ni à l'historique privé.

## Livrables finalisés

- `docs/hackathon/mcp-apps-integration-map.md` répond aux questions A à F avec
  symboles précis, deux diagrammes, tableau des points d’intégration et contrats
  minimaux des branches parallèles.
- Le document décompose le glue en six commits et définit un test end-to-end basé
  sur le faux serveur FastMCP stdio existant et un fake host.
- La matrice de risques distingue le MVP et le fallback, notamment pour la
  concurrence, les permissions, l’identité serveur, Textual et la fermeture.

## Vérifications finales

- `uv run git status --short` confirme que les seules créations de cette mission
  sont `STATUS.md` et `docs/hackathon/mcp-apps-integration-map.md`.
- `uv run git diff --check` ne signale aucune erreur d’espace.
- Aucun fichier de production ou de test n’a été modifié ; `uv.lock` modifié et
  `TASK.md` non suivi restent hors du périmètre et seront exclus du commit.
- Le commit sera limité explicitement aux deux documents avec `uv run git`.
