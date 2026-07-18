from __future__ import annotations

from pathlib import Path
import shutil
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

from examples.mcp_apps.job_application_studio.models import (
    ApplicationConstraints,
    ApplicationStatus,
    ClaimStatus,
    DocumentType,
)
from examples.mcp_apps.job_application_studio.server import UI_RESOURCE_URI, mcp
from examples.mcp_apps.job_application_studio.storage import (
    StudioNotFoundError,
    StudioStorage,
)

EXAMPLE_DIR = Path(__file__).parents[1]
PROJECT_DIR = Path(__file__).parents[4]


@pytest.fixture
def storage(tmp_path: Path) -> StudioStorage:
    data_dir = tmp_path / "data"
    shutil.copytree(EXAMPLE_DIR / "data", data_dir)
    return StudioStorage(data_dir)


@pytest.mark.asyncio
async def test_loads_all_fictitious_data(storage: StudioStorage) -> None:
    targets = await storage.load_targets()
    applications = await storage.load_applications()
    sources = await storage.load_source_documents()
    rules = await storage.load_rules()

    assert len(targets) == 3
    assert len(applications) == 3
    assert len(sources) == 4
    assert len(rules) == 3
    assert sum(len(source.paragraphs) for source in sources) >= 9


@pytest.mark.asyncio
async def test_lists_applications_with_target_details(storage: StudioStorage) -> None:
    result = await storage.list_applications()

    assert [item.id for item in result.applications] == [
        "application-northstar",
        "application-meridian",
        "application-lattice",
    ]
    assert result.applications[2].organization == "Lattice Cloud"


@pytest.mark.asyncio
async def test_gets_application_and_existing_generated_documents(
    storage: StudioStorage,
) -> None:
    detail = await storage.get_application("application-northstar")

    assert detail.target.title == "MSc Applied Machine Intelligence"
    assert len(detail.application.generated_documents) == 2
    assert detail.application.generated_documents[0].paragraphs[0].source_file


@pytest.mark.asyncio
async def test_unknown_application_id_has_clear_error(storage: StudioStorage) -> None:
    with pytest.raises(StudioNotFoundError, match="Unknown application id: missing"):
        await storage.get_application("missing")


@pytest.mark.asyncio
async def test_constraints_persist_in_temporary_data(storage: StudioStorage) -> None:
    constraints = ApplicationConstraints(
        emphasis_requirement_ids=["distributed-systems"],
        excluded_topics=["mobile"],
        tone="technical and concise",
        notes="Lead with event-processing reliability.",
    )

    await storage.save_constraints("application-lattice", constraints)
    reloaded = StudioStorage(storage.data_dir)
    detail = await reloaded.get_application("application-lattice")

    assert detail.application.constraints == constraints


@pytest.mark.asyncio
async def test_status_change_persists(storage: StudioStorage) -> None:
    await storage.update_status("application-meridian", ApplicationStatus.READY)

    reloaded = StudioStorage(storage.data_dir)
    detail = await reloaded.get_application("application-meridian")
    assert detail.application.status is ApplicationStatus.READY


@pytest.mark.asyncio
async def test_generation_is_deterministic_and_preserves_provenance(
    storage: StudioStorage,
) -> None:
    first = await storage.generate_application("application-lattice")
    second = await storage.generate_application("application-lattice")

    assert first.documents == second.documents
    assert {document.document_type for document in first.documents} == {
        DocumentType.RESUME,
        DocumentType.COVER_LETTER,
    }
    paragraphs = [
        paragraph for document in first.documents for paragraph in document.paragraphs
    ]
    assert all(paragraph.source_file for paragraph in paragraphs)
    assert all(paragraph.source_paragraph_id for paragraph in paragraphs)
    assert all(paragraph.transformation for paragraph in paragraphs)
    assert (storage.data_dir / first.documents[0].markdown_file).exists()


@pytest.mark.asyncio
async def test_imperfect_rule_selects_mobile_project_for_backend_demo(
    storage: StudioStorage,
) -> None:
    generated = await storage.generate_application("application-lattice")
    resume = next(
        document
        for document in generated.documents
        if document.document_type is DocumentType.RESUME
    )

    assert resume.paragraphs[0].source_paragraph_id == "cv-mobile-01"
    assert "mobile companion" in resume.paragraphs[0].text


@pytest.mark.asyncio
async def test_claim_verification_flags_rule_added_claim(
    storage: StudioStorage,
) -> None:
    await storage.generate_application("application-lattice")

    result = await storage.verify_claims("application-lattice")
    statuses = {verification.status for verification in result.verifications}

    assert ClaimStatus.VERIFIED in statuses
    assert ClaimStatus.NEEDS_REVIEW in statuses
    review = next(
        item for item in result.verifications if item.status is ClaimStatus.NEEDS_REVIEW
    )
    assert review.source_paragraph_id == "cv-mobile-01"


@pytest.mark.asyncio
async def test_mcp_exposes_tools_ui_resource_and_metadata() -> None:
    tools = await mcp.list_tools()
    tool_by_name = {tool.name: tool for tool in tools}

    assert set(tool_by_name) >= {
        "open_application_studio",
        "list_applications",
        "get_application",
        "save_application_constraints",
        "update_application_status",
        "generate_application",
        "verify_claims",
    }
    assert tool_by_name["open_application_studio"].meta == {
        "ui": {"resourceUri": UI_RESOURCE_URI}
    }

    resources = await mcp.list_resources()
    resource = next(item for item in resources if str(item.uri) == UI_RESOURCE_URI)
    assert resource.mimeType == "text/html;profile=mcp-app"
    contents = list(await mcp.read_resource(UI_RESOURCE_URI))
    html = str(contents[0].content)
    assert "Application Studio" in html
    assert "Northstar Institute" in html
    assert "Meridian Polytechnic" in html
    assert "Lattice Cloud" in html


@pytest.mark.asyncio
async def test_ui_emits_structured_revision_message() -> None:
    contents = list(await mcp.read_resource(UI_RESOURCE_URI))
    html = str(contents[0].content)

    assert 'type: "send_user_message"' in html
    assert "application_id: current.application.id" in html
    assert "document_type: documentType" in html
    assert "paragraph_id: paragraphId" in html
    assert "Request revision in Vibe" in html


@pytest.mark.asyncio
async def test_server_starts_over_stdio() -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "examples.mcp_apps.job_application_studio.server"],
        cwd=PROJECT_DIR,
    )

    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            result = await session.list_tools()

    assert any(tool.name == "open_application_studio" for tool in result.tools)
