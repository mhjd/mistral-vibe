# MCP Apps end-to-end

État au 18 juillet 2026 : tranche verticale fonctionnelle et testée avec le
serveur stdio réel Application Studio et un host injecté. Le Browser host local
est couvert séparément sans ouvrir de navigateur dans la suite automatique.

## Flux obtenu

`run_textual_ui` construit par défaut un `MCPAppController` avec l'unique
`AgentLoop` interactif. Le contrôleur reçoit :

- `AgentLoop.read_mcp_app_resource` comme lecteur `ui://` ;
- les callbacks de `build_mcp_app_callbacks`, qui passent par
  `AgentLoop.execute_tool` et `AgentLoop.submit_user_message` ;
- le `MCPAppHost` navigateur paresseux, sauf lorsqu'une fabrique est injectée en
  test.

Le flux Textual corrèle le `ToolCallEvent` complet et le `ToolResultEvent` réussi
par `tool_call_id`. Un outil sans App, un refus, une erreur, une annulation ou une
URI hors du schéma `ui` est ignoré. Pour une App valide, la ressource est lue sur
la session stdio persistante qui vient d'exécuter l'outil. L'état initial conserve
l'alias serveur, le nom publié, le nom distant, les arguments et le résultat.

Le host reçoit les callbacks du core. Un nom distant émis par l'App est qualifié
avec l'alias de son serveur avant le pipeline Vibe ; par exemple
`generate_application` devient
`application_studio_generate_application`. Les hooks et les permissions
`ALWAYS`, `ASK` et `NEVER` restent donc ceux de Vibe. Les événements produits par
ces callbacks repassent dans le rendu Textual. Un `send_user_message` crée un vrai
tour utilisateur, rend son contexte JSON visible au modèle et le conserve dans
`UserDisplayContentMetadata`.

## Test end-to-end automatique

Commande ciblée :

```console
uv run pytest tests/cli/mcp_apps/test_e2e.py -q
```

Le test démarre le vrai serveur
`examples.mcp_apps.job_application_studio.server` sur stdio, découvre
`open_application_studio`, l'exécute avec `AgentLoop.execute_tool`, lit
`ui://job-application-studio/main` sur la même session, et ouvre un
`FakeMCPAppHost`. Il vérifie :

- HTML non vide et MIME `text/html;profile=mcp-app` ;
- alias, noms d'outil, arguments et résultat initiaux ;
- appel UI distant `get_application` qualifié et exécuté une seule fois ;
- callback `ASK` réellement consulté pour l'ouverture et pour l'appel UI ;
- résultat structuré retourné au fake host ;
- message de révision et contexte soumis une seule fois dans un nouveau tour ;
- contexte persistant dans le message utilisateur ;
- host fermé une fois et processus stdio terminé.

Le navigateur système n'est jamais ouvert par ce test. Les tests de
`MCPAppHost` mockent `webbrowser.open` et exercent le serveur loopback HTTP, le
token, les deux callbacks et la fermeture du socket.

## Démonstration réelle

La configuration MCP de démonstration est suivie dans
`examples/mcp_apps/job_application_studio/.vibe/config.toml`. Elle réutilise les
mécanismes normaux de configuration projet et le runtime stdio réel.

Depuis la racine du dépôt :

```console
cd examples/mcp_apps/job_application_studio
uv run --project ../../.. vibe
```

Le fournisseur et sa clé restent ceux de la configuration Vibe habituelle. Au
premier lancement dans ce répertoire, accepter la confiance du projet si Vibe la
demande. Dans Vibe, demander exactement :

```text
Call application_studio_open_application_studio with no arguments.
```

### Scénario de cinq minutes

1. Autoriser `application_studio_open_application_studio`. Le navigateur ouvre
   Application Studio avec les trois dossiers fictifs.
2. Choisir **Lattice Cloud**, puis **Generate CV and cover letter**. Autoriser
   `application_studio_generate_application` dans Vibe.
3. Constater que le premier paragraphe du CV parle d'un compagnon mobile et
   cliquer **Request revision in Vibe**.
4. Vibe affiche un nouveau tour avec `application_id`, `document_type` et
   `paragraph_id`. Lui demander de corriger la règle générale fictive dans
   `data/rules.json` : remplacer la source `cv-mobile-01` par `cv-backend-01` et
   retirer `added_claim`.
5. Dans l'interface, cliquer **Regenerate** et autoriser le même outil. Le premier
   paragraphe décrit désormais le traitement événementiel backend. **Verify
   claims** ne présente plus le nombre de latence inventé par l'ancienne règle.

Pour remettre les données suivies à leur état initial après la démonstration :

```console
uv run git restore -- examples/mcp_apps/job_application_studio/data
```

## Cycle de vie

Le contrôleur ferme l'App active lors d'un remplacement, de `/clear`, d'une
reprise de session, d'un reset de contexte de plan et de l'arrêt de Vibe. Une
lecture ou un démarrage annulé nettoie le host partiel. Les fermetures répétées
sont idempotentes ; `AgentLoop.aclose` ferme ensuite le pool et son processus
stdio.

## Limites et repli

- Les ressources MCP Apps utilisent seulement le transport stdio dans ce MVP ;
  HTTP/OAuth nécessite une stratégie de session persistante équivalente.
- Le host est local à `127.0.0.1`, protégé par un token et une iframe sandboxée.
  Le durcissement origine, débit et CSP reste une amélioration du prototype.
- Le test automatique simule le navigateur avec un fake host ; le serveur
  loopback concret est testé séparément.
- Application Studio génère des documents déterministes et fictifs. Ce n'est ni
  un éditeur riche ni un moteur de candidature réel.

Si le navigateur bloque les ouvertures automatiques, copier l'URL loopback
affichée dans les logs, ou valider le host seul avec :

```console
uv run python -m examples.mcp_apps.controller_demo
```

Ce fallback exerce le Browser host et son protocole, mais pas le serveur stdio.
Le test end-to-end ci-dessus reste la preuve déterministe du lecteur `ui://`, des
permissions et des messages vers Vibe.
