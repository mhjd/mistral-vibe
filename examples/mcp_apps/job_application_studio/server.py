from __future__ import annotations

from pathlib import Path

from anyio import Path as AsyncPath
from mcp.server.fastmcp import FastMCP

from examples.mcp_apps.job_application_studio.models import (
    ApplicationConstraints,
    ApplicationDetail,
    ApplicationList,
    ApplicationStatus,
    ApplyDocumentSelectionResult,
    GenerationResult,
    OpenStudioResult,
    VerificationResult,
)
from examples.mcp_apps.job_application_studio.storage import StudioStorage

APP_DIR = Path(__file__).parent
UI_RESOURCE_URI = "ui://job-application-studio/main"
UI_META = {"ui": {"resourceUri": UI_RESOURCE_URI}}

mcp = FastMCP(
    "Application Studio",
    instructions=(
        "Manage fictitious education and job applications, generate deterministic "
        "documents with provenance, and verify their claims."
    ),
)
storage = StudioStorage(APP_DIR / "data")


@mcp.tool(
    title="Open Application Studio",
    description="Open the interactive Application Studio MCP App.",
    meta=UI_META,
)
async def open_application_studio() -> OpenStudioResult:
    return OpenStudioResult(
        resource_uri=UI_RESOURCE_URI,
        title="Application Studio",
        instructions="Render the UI resource and connect its host message adapter.",
    )


@mcp.tool(description="List all application dossiers and their current status.")
async def list_applications() -> ApplicationList:
    return await storage.list_applications()


@mcp.tool(description="Get one application, its target, sources, and generated files.")
async def get_application(application_id: str) -> ApplicationDetail:
    return await storage.get_application(application_id)


@mcp.tool(description="Save target-specific constraints for an application.")
async def save_application_constraints(
    application_id: str, constraints: ApplicationConstraints
) -> ApplicationDetail:
    return await storage.save_constraints(application_id, constraints)


@mcp.tool(description="Change the workflow status of an application.")
async def update_application_status(
    application_id: str, status: ApplicationStatus
) -> ApplicationDetail:
    return await storage.update_status(application_id, status)


@mcp.tool(
    description="Generate a deterministic resume and cover letter with provenance."
)
async def generate_application(application_id: str) -> GenerationResult:
    return await storage.generate_application(application_id)


@mcp.tool(
    description=(
        "Store Vibe's exact sourced paragraph selection and regenerate the documents."
    )
)
async def apply_document_selection(
    application_id: str,
    resume_paragraph_ids: list[str],
    cover_letter_paragraph_ids: list[str],
    rationale: str | None = None,
) -> ApplyDocumentSelectionResult:
    return await storage.apply_document_selection(
        application_id, resume_paragraph_ids, cover_letter_paragraph_ids, rationale
    )


@mcp.tool(
    description="Compare generated claims with their referenced source paragraphs."
)
async def verify_claims(application_id: str) -> VerificationResult:
    return await storage.verify_claims(application_id)


@mcp.resource(
    UI_RESOURCE_URI,
    name="Application Studio UI",
    description="Standalone HTML interface for the Application Studio demo.",
    mime_type="text/html;profile=mcp-app",
    meta=UI_META,
)
async def application_studio_ui() -> str:
    return await AsyncPath(APP_DIR / "ui" / "index.html").read_text()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
