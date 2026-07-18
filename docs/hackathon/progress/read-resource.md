# Lecture des ressources MCP Apps

État au 18 juillet 2026 : implémentation et validations terminées.

## Cartographie du runtime

- `AgentLoop` possède un `MCPRegistry` public et un `MCPConnectionPool` privé. Le
  pool est créé avec la boucle, injecté dans chaque `InvokeContext`, puis fermé
  par `AgentLoop.aclose()`.
- Le registre associe chaque alias configuré à son `MCPServer`. Sa découverte
  stdio démarre une session temporaire, appelle `list_tools()`, construit les
  classes dynamiques `MCPTool`, puis ferme cette session. Cette session de
  découverte ne traverse pas les boucles d’événements.
- Un proxy stdio reçoit l’alias, la commande, l’environnement et le répertoire de
  travail issus du serveur découvert. Lors du premier appel d’outil, il utilise
  le pool de l’`InvokeContext`. Le pool crée alors un worker et un
  `ClientSession` persistants, identifiés par commande, environnement et
  répertoire de travail.
- Le worker est le propriétaire unique des contextes anyio du transport et de la
  session. Il sérialise toutes les opérations d’une connexion, reconnecte une
  fois sur une erreur de transport et ferme le processus à la fin de la session.
- L’appel d’outil transmet maintenant aussi l’alias au pool. Le pool conserve
  l’association alias → clé de connexion : la lecture n’a pas à reconstruire la
  commande et ne peut pas démarrer un second serveur stdio.

La version installée de `mcp` est `1.28.1`. Son API publique est
`ClientSession.read_resource(uri: pydantic.AnyUrl) -> ReadResourceResult`.
`ReadResourceResult.contents` est une liste de `TextResourceContents |
BlobResourceContents`. Les deux variantes portent `uri: AnyUrl`, `mimeType: str
| None` et `_meta: dict[str, Any] | None`; le texte porte `text: str` et le
binaire `blob: str`. Le résultat lui-même peut aussi porter `_meta`.

## API publique

```python
resource = await pool.read_resource(
    server_alias="application_studio",
    resource_uri="ui://job-application-studio/main",
)
assert isinstance(resource, MCPAppResource)
```

Le contrat exige qu’un outil stdio de cet alias ait déjà été appelé avec ce
pool. C’est le chemin normal d’une MCP App : le résultat de l’outil déclenche
l’ouverture, puis le contrôleur demande sa ressource. Un alias jamais associé
est refusé au lieu de lancer un serveur implicite.

`MCPAppResource` expose l’alias serveur, l’URI demandée, le MIME commun réel ou
`None`, le texte concaténé et les métadonnées de réponse. `contents` conserve en
plus chaque bloc textuel avec son URI, son MIME et ses métadonnées exacts. Un MIME
absent ou divergent entre plusieurs blocs reste `None`; aucun MIME n’est déduit.

Le chemin de données complet est :

`MCPTool.run` → `MCPConnectionPool.call_tool(server_alias=...)` → association de
l’alias à la connexion persistante → `MCPConnectionPool.read_resource` → queue
du même `_StdioConnection` → même `ClientSession.read_resource` → validation
Pydantic de la réponse SDK → `MCPAppResource`.

## Validation et erreurs

La hiérarchie locale dérive de `MCPResourceError` :

- `MCPServerNotFoundError` pour un alias inconnu ;
- `MCPResourceURIError` pour une URI absente, invalide ou hors du schéma `ui` ;
- `MCPResourceNotFoundError` pour une ressource inconnue du serveur ;
- `MCPResourceEmptyError` pour zéro contenu ou du texte vide ;
- `MCPResourceContentError` pour un bloc binaire, non pris en charge ou invalide ;
- `MCPResourceReadError` pour les autres erreurs MCP/SDK ;
- `MCPResourceSessionError` pour un pool fermé ou une session indisponible.

Les erreurs du SDK sont chaînées. `CancelledError` n’est pas interceptée : le
demandeur reçoit l’annulation, le worker termine proprement l’opération déjà
envoyée, puis la même session reste utilisable.

## Contrat du futur contrôleur CLI

Le contrôleur doit obtenir l’alias et l’URI depuis le `MCPTool` découvert,
attendre le `ToolResultEvent` réussi, puis appeler l’API ci-dessus sur le pool de
la session courante. Il ne doit ni lire les attributs privés du proxy, ni créer
un `ClientSession`, ni relancer la commande stdio. Une erreur doit empêcher
l’ouverture ou le remplacement du host et être rendue à l’utilisateur. La
lecture HTTP reste explicitement hors du MVP.

## Tests

`tests/tools/test_mcp_resources.py` couvre la conservation des URI/MIME/textes et
métadonnées, les MIME absents, les réponses multi-contenus, toutes les erreurs
ci-dessus, deux lectures concurrentes, l’annulation et la fermeture.

Le test Application Studio découvre réellement le proxy depuis la configuration
stdio, appelle l’outil découvert via le pool, vérifie une ressource inconnue,
lit `ui://job-application-studio/main`, conserve
`text/html;profile=mcp-app`, vérifie le HTML et attend la terminaison du processus.
Il prend environ 0,6 seconde sur la machine de développement.

Validations exécutées :

- tests ciblés de lecture : 18 réussis ;
- suite combinée MCP, OAuth, écran MCP, Browser host et Studio : 184 réussis ;
- suite globale : 7 978 tests collectés, aucune défaillance ;
- Pyright ciblé et global : aucune erreur ;
- Ruff ciblé et vérification du diff : réussis.

## Limites

- Le MVP prend uniquement en charge stdio. HTTP/OAuth nécessitera une stratégie
  de session persistante distincte.
- La découverte du registre reste volontairement une session temporaire ; la
  lecture réutilise la session persistante de l’appel d’outil découvert, qui est
  la session pertinente pour l’état runtime de l’App.
- Le contrôleur CLI et son branchement au host ne font pas partie de cette
  tranche.
