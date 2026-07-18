# MCP Apps host — hackathon MVP

## Scope

- Local MCP server over stdio only.
- One MCP App open at a time.
- Browser-based sidecar UI.
- Tool advertises `_meta.ui.resourceUri`.
- Vibe preserves tool and result metadata.
- Vibe reads the corresponding `ui://` resource.
- The UI receives the initial tool arguments and result.
- The UI can request a tool call through Vibe.
- The UI can submit a user message to the active Vibe conversation.
- Tool calls must reuse Vibe's permission path.

## Out of scope

- OAuth
- Remote HTTP MCP servers
- Multiple simultaneous apps
- VS Code and ACP rendering
- Full MCP Apps conformance
- Production-grade sandboxing
- App marketplace

## Proposed Python interfaces

MCPAppDescriptor:
- server_alias: str
- tool_name: str
- resource_uri: str

MCPAppResource:
- uri: str
- mime_type: str
- html: str

MCPAppHost.open:
- resource
- initial_arguments
- initial_result
- call_tool callback
- send_user_message callback

## Demo

A Job Application Studio MCP App:

- application list
- job details
- per-application constraints
- generate CV and cover letter action
- generated document preview
- source/provenance information
- send revision request to Vibe
