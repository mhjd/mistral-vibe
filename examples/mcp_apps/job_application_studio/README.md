# Application Studio MCP App

Application Studio is a local MCP App demo for repeatable application
workflows. It handles education programs and jobs with the same small domain
model: target, requirements, constraints, source paragraphs, generated
documents, provenance, claim checks, and status.

The app deliberately separates two kinds of work:

- the UI executes frequent, structured operations;
- Vibe receives exceptional revision requests and can diagnose or improve the
  general workflow rule that caused a poor result.

No LLM, network service, database, authentication, or real personal data is
used.

## Run locally

From the repository root:

```console
uv run python -m examples.mcp_apps.job_application_studio.server
```

The process speaks MCP over stdio, so it waits for an MCP client rather than
opening a web port. A stdio host can configure it with the equivalent of:

```json
{
  "transport": "stdio",
  "command": "uv",
  "args": [
    "run",
    "python",
    "-m",
    "examples.mcp_apps.job_application_studio.server"
  ]
}
```

The working directory must be the repository root. The server exposes the HTML
resource `ui://job-application-studio/main` with media type
`text/html;profile=mcp-app`.

## Tools

| Tool | Typical arguments | Result |
| --- | --- | --- |
| `open_application_studio` | `{}` | UI resource URI and instructions |
| `list_applications` | `{}` | Compact application cards |
| `get_application` | `{"application_id":"application-lattice"}` | Target, constraints, sources, generated files |
| `save_application_constraints` | application ID and typed constraints | Persisted application detail |
| `update_application_status` | application ID and status | Persisted application detail |
| `generate_application` | application ID | Deterministic CV and letter with provenance |
| `verify_claims` | application ID | Per-claim verification records |

An MCP client should initialize the session, list tools, call
`open_application_studio`, read the announced resource, and then route the
UI's `call_tool` messages back to these tools. The small browser adapter also
supports `window.mcpHost.callTool` and `window.openai.callTool` when supplied by
the host.

Revision requests are host messages, not tool calls and never provider calls:

```json
{
  "type": "send_user_message",
  "request_id": "generated-in-the-browser",
  "message": "Revise the selected paragraph and diagnose whether the general selection rule should change.",
  "context": {
    "application_id": "application-lattice",
    "document_type": "resume",
    "paragraph_id": "application-lattice-resume-1"
  }
}
```

## Fictitious demo data

The tracked fixture contains two master applications and one job application,
two archived CVs, two archived letters, nine identified source paragraphs,
three selection rules, and one previously generated dossier. Every person,
organization, target, project, and claim is invented.

The rule `imperfect-backend-project-rule` intentionally selects
`cv-mobile-01` for the backend role and adds an unsupported latency number. It
provides two visible defects: irrelevant provenance and a `needs_review` claim.
Changing its source paragraph to `cv-backend-01` and removing `added_claim`
simulates the improvement Vibe would make before regeneration.

Generated files and application mutations are written below `data/`. To reset
the demo after a run:

```console
uv run git restore -- examples/mcp_apps/job_application_studio/data
```

The tests always copy `data/` to a temporary directory and never mutate the
tracked fixture.

## Five-minute demo

1. Connect the stdio server, call `open_application_studio`, and render its UI
   resource. Point out the three dossiers and the already generated master
   application.
2. Select the fictional backend role at Lattice Cloud and review its extracted
   Python, distributed-systems, and operational-quality criteria.
3. Add “Lead with event-processing reliability” as an application constraint
   and save it.
4. Choose **Generate CV and cover letter**. Inspect each paragraph's source,
   stable paragraph ID, covered criteria, and transformation.
5. Notice that the first CV paragraph is a mobile project even though the
   target is backend. Choose **Request revision in Vibe** on that paragraph.
6. Show that Vibe receives the application, document, paragraph, and revision
   request as structured context. Ask Vibe to fix the general selection rule,
   not just rewrite this one dossier.
7. Replace the faulty rule's source with `cv-backend-01`, clear its
   `added_claim`, and choose **Regenerate**. The backend event-processing
   paragraph now appears in the repeated workflow.
8. Choose **Verify claims**. Before the rule fix the fabricated latency number
   is `needs_review`; after the fix, the remaining sourced factual claims are
   verified.

The central point is: **the application executes the repeated workflow; Vibe
diagnoses and improves the workflow.**

## Limits

Generation is tag- and rule-based, not stylistically sophisticated. The demo
does not scrape targets, manage deadlines or attachments, export PDFs, edit
rich documents, authenticate users, submit applications, or update rules
through a dedicated MCP tool. The host bridge contract is intentionally small
so it can be connected to the future Vibe browser host without coupling this
example to runtime code.
