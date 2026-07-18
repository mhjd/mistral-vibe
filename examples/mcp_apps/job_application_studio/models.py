from __future__ import annotations

from enum import StrEnum, auto

from pydantic import BaseModel, ConfigDict, Field


class StudioModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TargetType(StrEnum):
    PROGRAM = auto()
    JOB = auto()


class ApplicationStatus(StrEnum):
    DRAFT = auto()
    IN_PROGRESS = auto()
    GENERATED = auto()
    READY = auto()
    SUBMITTED = auto()


class DocumentType(StrEnum):
    RESUME = auto()
    COVER_LETTER = auto()


class TransformationType(StrEnum):
    SELECTED = auto()
    LIGHTLY_ADAPTED = auto()
    RULE_AUGMENTED = auto()


class ClaimStatus(StrEnum):
    VERIFIED = auto()
    UNSUPPORTED = auto()
    NEEDS_REVIEW = auto()


class ApplicationRequirement(StudioModel):
    id: str
    label: str
    details: str
    keywords: list[str] = Field(default_factory=list)


class JobRequirement(ApplicationRequirement):
    pass


class ApplicationTarget(StudioModel):
    id: str
    target_type: TargetType
    organization: str
    title: str
    summary: str
    requirements: list[ApplicationRequirement]


class JobOffer(ApplicationTarget):
    pass


class ApplicationConstraints(StudioModel):
    emphasis_requirement_ids: list[str] = Field(default_factory=list)
    excluded_topics: list[str] = Field(default_factory=list)
    tone: str = "direct and specific"
    notes: str = ""


class SourceParagraph(StudioModel):
    id: str
    text: str
    criteria_tags: list[str]
    claims: list[str] = Field(default_factory=list)


class SourceDocument(StudioModel):
    id: str
    document_type: DocumentType
    title: str
    file: str
    paragraphs: list[SourceParagraph]


class GeneratedParagraph(StudioModel):
    id: str
    text: str
    source_file: str
    source_paragraph_id: str
    criteria_covered: list[str]
    transformation: TransformationType
    claims: list[str] = Field(default_factory=list)


class GeneratedDocument(StudioModel):
    id: str
    document_type: DocumentType
    markdown_file: str
    markdown: str
    paragraphs: list[GeneratedParagraph]


class Application(StudioModel):
    id: str
    target_id: str
    status: ApplicationStatus
    constraints: ApplicationConstraints = Field(default_factory=ApplicationConstraints)
    generated_documents: list[GeneratedDocument] = Field(default_factory=list)


class ApplicationRule(StudioModel):
    id: str
    description: str
    target_keywords: list[str]
    source_paragraph_ids: list[str]
    transformation: TransformationType = TransformationType.SELECTED
    added_claim: str | None = None
    enabled: bool = True


class DocumentSelection(StudioModel):
    resume_paragraph_ids: list[str]
    cover_letter_paragraph_ids: list[str]
    rationale: str | None = None


class ApplyDocumentSelectionResult(DocumentSelection):
    application_id: str


class ClaimVerification(StudioModel):
    document_id: str
    paragraph_id: str
    claim: str
    status: ClaimStatus
    source_paragraph_id: str | None
    explanation: str


class ApplicationSummary(StudioModel):
    id: str
    target_type: TargetType
    organization: str
    title: str
    status: ApplicationStatus


class ApplicationList(StudioModel):
    applications: list[ApplicationSummary]


class ApplicationDetail(StudioModel):
    application: Application
    target: ApplicationTarget
    source_documents: list[SourceDocument]
    document_selection: DocumentSelection | None = None


class GenerationResult(StudioModel):
    application: Application
    target: ApplicationTarget
    documents: list[GeneratedDocument]


class VerificationResult(StudioModel):
    application_id: str
    verifications: list[ClaimVerification]


class OpenStudioResult(StudioModel):
    resource_uri: str
    title: str
    instructions: str
