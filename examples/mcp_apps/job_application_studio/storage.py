from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Protocol

from anyio import Path as AsyncPath
from pydantic import TypeAdapter

from examples.mcp_apps.job_application_studio.models import (
    Application,
    ApplicationConstraints,
    ApplicationDetail,
    ApplicationList,
    ApplicationRule,
    ApplicationStatus,
    ApplicationSummary,
    ApplicationTarget,
    ApplyDocumentSelectionResult,
    ClaimStatus,
    ClaimVerification,
    DocumentSelection,
    DocumentType,
    GeneratedDocument,
    GeneratedParagraph,
    GenerationResult,
    SourceDocument,
    SourceParagraph,
    TransformationType,
    VerificationResult,
)


class StudioNotFoundError(ValueError):
    pass


class HasId(Protocol):
    id: str


class StudioStorage:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._write_lock = asyncio.Lock()
        self._document_selections: dict[str, DocumentSelection] = {}

    async def load_targets(self) -> list[ApplicationTarget]:
        return TypeAdapter(list[ApplicationTarget]).validate_json(
            await AsyncPath(self.data_dir / "jobs.json").read_text()
        )

    async def load_applications(self) -> list[Application]:
        return TypeAdapter(list[Application]).validate_json(
            await AsyncPath(self.data_dir / "applications.json").read_text()
        )

    async def load_source_documents(self) -> list[SourceDocument]:
        return TypeAdapter(list[SourceDocument]).validate_json(
            await AsyncPath(self.data_dir / "source_documents.json").read_text()
        )

    async def load_rules(self) -> list[ApplicationRule]:
        return TypeAdapter(list[ApplicationRule]).validate_json(
            await AsyncPath(self.data_dir / "rules.json").read_text()
        )

    async def list_applications(self) -> ApplicationList:
        applications = await self.load_applications()
        targets = {target.id: target for target in await self.load_targets()}
        summaries = [
            ApplicationSummary(
                id=application.id,
                target_type=targets[application.target_id].target_type,
                organization=targets[application.target_id].organization,
                title=targets[application.target_id].title,
                status=application.status,
            )
            for application in applications
        ]
        return ApplicationList(applications=summaries)

    async def get_application(self, application_id: str) -> ApplicationDetail:
        application = await self._find_application(application_id)
        target = await self._find_target(application.target_id)
        sources = await self.load_source_documents()
        return ApplicationDetail(
            application=application,
            target=target,
            source_documents=sources,
            document_selection=self._document_selections.get(application_id),
        )

    async def apply_document_selection(
        self,
        application_id: str,
        resume_paragraph_ids: list[str],
        cover_letter_paragraph_ids: list[str],
        rationale: str | None = None,
    ) -> ApplyDocumentSelectionResult:
        await self._find_application(application_id)
        sources = await self.load_source_documents()
        source_by_id = {
            paragraph.id: source
            for source in sources
            for paragraph in source.paragraphs
        }
        paragraph_ids = resume_paragraph_ids + cover_letter_paragraph_ids
        unknown_ids = [item for item in paragraph_ids if item not in source_by_id]
        if unknown_ids:
            raise StudioNotFoundError(f"Unknown source paragraph id: {unknown_ids[0]}")
        for paragraph_id in resume_paragraph_ids:
            if source_by_id[paragraph_id].document_type is not DocumentType.RESUME:
                raise ValueError(f"Not a resume paragraph id: {paragraph_id}")
        for paragraph_id in cover_letter_paragraph_ids:
            if (
                source_by_id[paragraph_id].document_type
                is not DocumentType.COVER_LETTER
            ):
                raise ValueError(f"Not a cover letter paragraph id: {paragraph_id}")
        selection = DocumentSelection(
            resume_paragraph_ids=resume_paragraph_ids,
            cover_letter_paragraph_ids=cover_letter_paragraph_ids,
            rationale=rationale,
        )
        self._document_selections[application_id] = selection
        return ApplyDocumentSelectionResult(
            application_id=application_id, **selection.model_dump()
        )

    async def save_constraints(
        self, application_id: str, constraints: ApplicationConstraints
    ) -> ApplicationDetail:
        async with self._write_lock:
            applications = await self.load_applications()
            application = self._get_by_id(applications, application_id, "application")
            application.constraints = constraints
            await self._write_applications(applications)
        return await self.get_application(application_id)

    async def update_status(
        self, application_id: str, status: ApplicationStatus
    ) -> ApplicationDetail:
        async with self._write_lock:
            applications = await self.load_applications()
            application = self._get_by_id(applications, application_id, "application")
            application.status = status
            await self._write_applications(applications)
        return await self.get_application(application_id)

    async def generate_application(self, application_id: str) -> GenerationResult:
        async with self._write_lock:
            applications = await self.load_applications()
            application = self._get_by_id(applications, application_id, "application")
            target = await self._find_target(application.target_id)
            sources = await self.load_source_documents()
            rules = await self.load_rules()
            documents = self._generate_documents(
                application,
                target,
                sources,
                rules,
                self._document_selections.get(application_id),
            )
            application.generated_documents = documents
            application.status = ApplicationStatus.GENERATED
            await self._write_generated_files(documents)
            await self._write_applications(applications)
        return GenerationResult(
            application=application, target=target, documents=documents
        )

    async def verify_claims(self, application_id: str) -> VerificationResult:
        application = await self._find_application(application_id)
        sources = await self.load_source_documents()
        source_paragraphs = {
            paragraph.id: paragraph
            for source in sources
            for paragraph in source.paragraphs
        }
        verifications = [
            self._verify_claim(document, paragraph, claim, source_paragraphs)
            for document in application.generated_documents
            for paragraph in document.paragraphs
            for claim in paragraph.claims
        ]
        return VerificationResult(
            application_id=application_id, verifications=verifications
        )

    async def _write_applications(self, applications: list[Application]) -> None:
        payload = [application.model_dump(mode="json") for application in applications]
        await AsyncPath(self.data_dir / "applications.json").write_text(
            json.dumps(payload, indent=2) + "\n"
        )

    async def _write_generated_files(self, documents: list[GeneratedDocument]) -> None:
        generated_dir = AsyncPath(self.data_dir / "generated")
        await generated_dir.mkdir(parents=True, exist_ok=True)
        for document in documents:
            await AsyncPath(self.data_dir / document.markdown_file).write_text(
                document.markdown
            )

    async def _find_application(self, application_id: str) -> Application:
        return self._get_by_id(
            await self.load_applications(), application_id, "application"
        )

    async def _find_target(self, target_id: str) -> ApplicationTarget:
        return self._get_by_id(await self.load_targets(), target_id, "target")

    @staticmethod
    def _get_by_id[ModelT: HasId](
        models: list[ModelT], expected_id: str, kind: str
    ) -> ModelT:
        for model in models:
            if model.id == expected_id:
                return model
        raise StudioNotFoundError(f"Unknown {kind} id: {expected_id}")

    def _generate_documents(
        self,
        application: Application,
        target: ApplicationTarget,
        sources: list[SourceDocument],
        rules: list[ApplicationRule],
        selection: DocumentSelection | None = None,
    ) -> list[GeneratedDocument]:
        source_by_id = {
            paragraph.id: (source, paragraph)
            for source in sources
            for paragraph in source.paragraphs
        }
        active_rules = [
            rule for rule in rules if rule.enabled and self._rule_matches(rule, target)
        ]
        if selection is None:
            resume_sources = self._select_resume_sources(
                application, target, sources, active_rules
            )
            letter_sources = self._select_letter_sources(application, target, sources)
        else:
            resume_sources = [
                source_by_id[paragraph_id]
                for paragraph_id in selection.resume_paragraph_ids
            ]
            letter_sources = [
                source_by_id[paragraph_id]
                for paragraph_id in selection.cover_letter_paragraph_ids
            ]
        resume_paragraphs = [
            self._generated_paragraph(
                application, target, source, paragraph, active_rules, index
            )
            for index, (source, paragraph) in enumerate(resume_sources, start=1)
        ]
        letter_paragraphs = [
            self._generated_paragraph(application, target, source, paragraph, [], index)
            for index, (source, paragraph) in enumerate(letter_sources, start=1)
        ]
        resume = self._document(
            application.id, target, DocumentType.RESUME, resume_paragraphs
        )
        letter = self._document(
            application.id, target, DocumentType.COVER_LETTER, letter_paragraphs
        )
        for paragraph in resume.paragraphs + letter.paragraphs:
            if paragraph.source_paragraph_id not in source_by_id:
                raise StudioNotFoundError(
                    f"Unknown source paragraph id: {paragraph.source_paragraph_id}"
                )
        return [resume, letter]

    def _select_resume_sources(
        self,
        application: Application,
        target: ApplicationTarget,
        sources: list[SourceDocument],
        rules: list[ApplicationRule],
    ) -> list[tuple[SourceDocument, SourceParagraph]]:
        candidates = [
            (source, paragraph)
            for source in sources
            if source.document_type is DocumentType.RESUME
            for paragraph in source.paragraphs
            if not self._is_excluded(paragraph, application.constraints)
        ]
        by_id = {paragraph.id: (source, paragraph) for source, paragraph in candidates}
        selected: list[tuple[SourceDocument, SourceParagraph]] = []
        for rule in rules:
            selected.extend(
                by_id[paragraph_id]
                for paragraph_id in rule.source_paragraph_ids
                if paragraph_id in by_id
            )
        ranked = sorted(
            candidates,
            key=lambda item: self._paragraph_score(
                item[1], target, application.constraints
            ),
            reverse=True,
        )
        selected.extend(item for item in ranked if item not in selected)
        return selected[:3]

    def _select_letter_sources(
        self,
        application: Application,
        target: ApplicationTarget,
        sources: list[SourceDocument],
    ) -> list[tuple[SourceDocument, SourceParagraph]]:
        candidates = [
            (source, paragraph)
            for source in sources
            if source.document_type is DocumentType.COVER_LETTER
            for paragraph in source.paragraphs
            if not self._is_excluded(paragraph, application.constraints)
        ]
        return sorted(
            candidates,
            key=lambda item: self._paragraph_score(
                item[1], target, application.constraints
            ),
            reverse=True,
        )[:2]

    @staticmethod
    def _is_excluded(
        paragraph: SourceParagraph, constraints: ApplicationConstraints
    ) -> bool:
        excluded = {topic.casefold() for topic in constraints.excluded_topics}
        return bool(
            excluded.intersection(tag.casefold() for tag in paragraph.criteria_tags)
        )

    @staticmethod
    def _paragraph_score(
        paragraph: SourceParagraph,
        target: ApplicationTarget,
        constraints: ApplicationConstraints,
    ) -> int:
        tags = {tag.casefold() for tag in paragraph.criteria_tags}
        score = 0
        for requirement in target.requirements:
            keywords = {keyword.casefold() for keyword in requirement.keywords}
            overlap = len(tags.intersection(keywords))
            score += overlap * 2
            if requirement.id in constraints.emphasis_requirement_ids:
                score += overlap * 3
        return score

    @staticmethod
    def _rule_matches(rule: ApplicationRule, target: ApplicationTarget) -> bool:
        target_text = " ".join([
            target.title,
            target.summary,
            *(requirement.label for requirement in target.requirements),
            *(
                keyword
                for requirement in target.requirements
                for keyword in requirement.keywords
            ),
        ]).casefold()
        return any(
            keyword.casefold() in target_text for keyword in rule.target_keywords
        )

    def _generated_paragraph(
        self,
        application: Application,
        target: ApplicationTarget,
        source: SourceDocument,
        paragraph: SourceParagraph,
        rules: list[ApplicationRule],
        index: int,
    ) -> GeneratedParagraph:
        matching_rule = next(
            (rule for rule in rules if paragraph.id in rule.source_paragraph_ids), None
        )
        text = paragraph.text
        claims = list(paragraph.claims)
        transformation = TransformationType.SELECTED
        if matching_rule and matching_rule.added_claim:
            text = f"{text} {matching_rule.added_claim}"
            claims.append(matching_rule.added_claim)
            transformation = TransformationType.RULE_AUGMENTED
        criteria = self._covered_requirements(paragraph, target)
        return GeneratedParagraph(
            id=f"{application.id}-{source.document_type.value}-{index}",
            text=text,
            source_file=source.file,
            source_paragraph_id=paragraph.id,
            criteria_covered=criteria,
            transformation=transformation,
            claims=claims,
        )

    @staticmethod
    def _covered_requirements(
        paragraph: SourceParagraph, target: ApplicationTarget
    ) -> list[str]:
        tags = {tag.casefold() for tag in paragraph.criteria_tags}
        return [
            requirement.id
            for requirement in target.requirements
            if tags.intersection(keyword.casefold() for keyword in requirement.keywords)
        ]

    @staticmethod
    def _document(
        application_id: str,
        target: ApplicationTarget,
        document_type: DocumentType,
        paragraphs: list[GeneratedParagraph],
    ) -> GeneratedDocument:
        heading = "Resume" if document_type is DocumentType.RESUME else "Cover letter"
        body = "\n\n".join(paragraph.text for paragraph in paragraphs)
        markdown = f"# {heading} — {target.title}\n\n{body}\n"
        filename = f"generated/{application_id}_{document_type.value}.md"
        return GeneratedDocument(
            id=f"{application_id}-{document_type.value}",
            document_type=document_type,
            markdown_file=filename,
            markdown=markdown,
            paragraphs=paragraphs,
        )

    @staticmethod
    def _verify_claim(
        document: GeneratedDocument,
        paragraph: GeneratedParagraph,
        claim: str,
        sources: dict[str, SourceParagraph],
    ) -> ClaimVerification:
        source = sources.get(paragraph.source_paragraph_id)
        if source is None:
            return ClaimVerification(
                document_id=document.id,
                paragraph_id=paragraph.id,
                claim=claim,
                status=ClaimStatus.UNSUPPORTED,
                source_paragraph_id=None,
                explanation="The referenced source paragraph does not exist.",
            )
        if claim in source.claims:
            return ClaimVerification(
                document_id=document.id,
                paragraph_id=paragraph.id,
                claim=claim,
                status=ClaimStatus.VERIFIED,
                source_paragraph_id=source.id,
                explanation="The claim is stated in the referenced source paragraph.",
            )
        return ClaimVerification(
            document_id=document.id,
            paragraph_id=paragraph.id,
            claim=claim,
            status=ClaimStatus.NEEDS_REVIEW,
            source_paragraph_id=source.id,
            explanation="The source is related, but it does not support the exact claim.",
        )
