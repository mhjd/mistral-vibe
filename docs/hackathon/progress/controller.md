# MCP App Controller progress

## Status

The CLI now owns a typed, injectable `MCPAppController` without depending on a
concrete `read_resource` implementation. It accepts an async resource loader,
tool-call and user-message callbacks, and either a host factory or the existing
browser host.

The controller:

- validates the returned URI, HTML media type, non-empty text, and a 2 MiB limit;
- builds `MCPAppInitialState` with server, published/remote tool names, resource
  URI, arguments, and a JSON-safe result;
- keeps one active host and replaces it only after the next host starts;
- keeps the previous session when loading or startup fails;
- cancels rapid superseded event-driven opens and owns all created tasks;
- closes explicitly, on controller shutdown, and idempotently;
- correlates full `ToolCallEvent` and successful `ToolResultEvent` instances by
  `tool_call_id`, while ignoring ordinary tools and failed/cancelled results.

## Textual integration

`VibeApp.set_mcp_app_controller()` and the optional `run_textual_ui(...,
mcp_app_controller=...)` boundary inject the feature without changing the agent
loop. The existing event stream is observed after normal rendering. Observation
only schedules controller work, so resource loading and browser-host startup do
not block event consumption. Controller errors use a Textual error notification.

The controller closes before session resume, `/clear`, plan context reset, and
`AgentLoop.aclose()` during shutdown.

The default interactive CLI does not construct the controller yet because the
parallel concrete `ui://` reader is intentionally not imported or guessed. Its
future adapter only needs this signature:

```python
async def load_resource(server_name: str, resource_uri: str) -> MCPAppResource:
    ...
```

The callbacks supplied to the controller remain adapters. No permission or
agent-loop tool execution path is implemented or bypassed here.

## Manual browser demo

From the repository root, run:

```console
uv run python -m examples.mcp_apps.controller_demo
```

The command reads the tracked Application Studio HTML, creates an initial state,
installs fake tool/message callbacks, opens the real loopback browser host, and
waits for Enter before closing it. It does not start the MCP stdio server or
modify Application Studio data.

## Validation commands

```console
uv run pytest tests/cli/mcp_apps -q
uv run pytest tests/cli/textual_ui/test_mcp_app_controller.py tests/cli/textual_ui/test_quit_confirmation.py -q
uv run pytest tests/tools/test_mcp.py examples/mcp_apps/job_application_studio/tests -q
uv run ruff check .
uv run ruff format --check .
uv run pyright vibe/cli/mcp_apps vibe/cli/textual_ui/app.py examples/mcp_apps/controller_demo.py
uv run pyright
git diff --check
```

## Remaining boundary

- Wire the parallel branch's concrete MCP resource reader to
  `MCPAppResourceLoader` after its API stabilizes.
- Supply permission-preserving `call_tool` and queue-preserving
  `send_user_message` adapters when their public APIs exist.
- The browser host currently protects loopback access with a random token and a
  sandboxed iframe. A future hardening pass can add explicit origin checks,
  request-size/rate limits, and a stricter CSP without changing the controller.
- The controller deliberately does not add permission logic, queue callbacks,
  MCP SDK access, or private agent-loop calls.
