"""Assemble tailored CVs from immutable metadata and explicitly cited rewrites."""

import copy
import json
import re
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field
from rendercv.schema.sample_generator import dictionary_to_yaml

from .parse import load_yaml

Identifier = Annotated[str, Field(min_length=1, max_length=200)]
Index = Annotated[int, Field(strict=True, ge=0)]

MANDATORY_SECTION_TITLES = frozenset(
    {
        "education",
        "education and training",
        "educational background",
        "academic background",
        "academic qualifications",
        "experience",
        "work experience",
        "professional experience",
        "relevant experience",
        "employment",
        "employment history",
        "work history",
        "professional history",
        "career history",
        "professional background",
    }
)
PROFILE_SECTION_TITLES = frozenset(
    {"summary", "profile", "professional summary", "career summary", "about me"}
)


def normalized_title(title: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", title.casefold()))


def mandatory_section(title: str) -> bool:
    return normalized_title(title) in MANDATORY_SECTION_TITLES


class GroundedText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=200)


class EntrySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_id: Identifier
    # None retains the source highlights; a list selects/reorders them verbatim.
    highlights: list[Index] | None = Field(default=None, max_length=200)
    rewrite: GroundedText | None = None
    summary: GroundedText | None = None
    rewritten_highlights: list[GroundedText] | None = Field(
        default=None, min_length=1, max_length=100
    )
    detail_indices: list[Index] | None = Field(
        default=None, min_length=1, max_length=200
    )


class SectionSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section_id: Identifier
    entries: list[EntrySelection] = Field(min_length=1, max_length=200)
    title: GroundedText | None = None


class TailoringPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sections: list[SectionSelection] = Field(min_length=1, max_length=100)
    headline: GroundedText | None = None
    profile: GroundedText | None = None


def source_document(yaml: str) -> dict[str, Any]:
    return {"cv": json.loads(load_yaml(yaml).cv.model_dump_json(exclude_none=True))}


def source_sections(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "section_id": f"s{section_index}",
            "title": title,
            "entries": [
                {"entry_id": f"s{section_index}:e{entry_index}", "content": entry}
                for entry_index, entry in enumerate(entries)
            ],
        }
        for section_index, (title, entries) in enumerate(
            document["cv"].get("sections", {}).items()
        )
    ]


def source_facts(document: dict[str, Any]) -> dict[str, str]:
    """Stable references into the source only; never include job requirements."""
    facts: dict[str, str] = {}
    if headline := document["cv"].get("headline"):
        facts["headline"] = headline
    for section in source_sections(document):
        for entry in section["entries"]:
            entry_id, content = entry["entry_id"], entry["content"]
            facts[entry_id] = (
                content
                if isinstance(content, str)
                else json.dumps(content, ensure_ascii=False, sort_keys=True)
            )
            if isinstance(content, dict):
                for field in ("summary", "details", "bullet"):
                    if isinstance(content.get(field), str):
                        facts[f"{entry_id}:{field}"] = content[field]
                for index, highlight in enumerate(content.get("highlights", [])):
                    facts[f"{entry_id}:h{index}"] = highlight
    return facts


def split_skill_items(details: str) -> list[str]:
    """Split source list items while retaining commas inside bracketed text."""
    items: list[str] = []
    stack: list[str] = []
    closing = {"(": ")", "[": "]", "{": "}"}
    start = 0
    for index, char in enumerate(details):
        if char in closing:
            stack.append(closing[char])
        elif stack and char == stack[-1]:
            stack.pop()
        elif char in ",|;" and not stack:
            if item := details[start:index].strip():
                items.append(item)
            start = index + 1
    if item := details[start:].strip():
        items.append(item)
    return items


def _numbers(text: str) -> set[str]:
    # Accept formatting changes such as 1,000 -> 1000 and 12 percent -> 12%.
    return {
        number.replace(",", "")
        for number in re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", text)
    }


def _urls(text: str) -> set[str]:
    targets = re.findall(
        r"(?:[a-z][a-z0-9+.-]*://|www\.|mailto:)[^\s<>\]\)\"'\\]+",
        text,
        re.IGNORECASE,
    )
    # Markdown links can also contain relative URLs or schemes without //.
    targets.extend(re.findall(r"\[[^\]]*\]\(([^\s\)]+)\)", text))
    return {url.rstrip(".,;!?") for url in targets}


def _validate_indices(indices: list[int], original: list[Any], field: str) -> None:
    if len(set(indices)) != len(indices) or any(
        isinstance(index, bool)
        or not isinstance(index, int)
        or index < 0
        or index >= len(original)
        for index in indices
    ):
        raise ValueError(
            f"{field} indices must be unique, valid zero-based source indices."
        )


def _assemble(
    source_yaml: str, plan: TailoringPlan
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    # Plans are mutable, so validate again after any caller-side edits.
    plan = TailoringPlan.model_validate(plan.model_dump())
    document = source_document(source_yaml)
    sections = {section["section_id"]: section for section in source_sections(document)}
    facts = source_facts(document)
    claims: list[dict[str, Any]] = []

    def grounded(
        value: GroundedText,
        claim_id: str,
        *,
        entry_id: str | None = None,
        section_id: str | None = None,
        context: dict[str, Any] | None = None,
        original: str | None = None,
    ) -> str:
        if not value.text.strip():
            raise ValueError("Rewritten text cannot be blank.")
        if len(set(value.evidence_ids)) != len(value.evidence_ids):
            raise ValueError("Evidence references must not repeat.")
        evidence = []
        for evidence_id in value.evidence_ids:
            if evidence_id not in facts:
                raise ValueError(f"Unknown source evidence reference: {evidence_id}.")
            if entry_id and not (
                evidence_id == entry_id or evidence_id.startswith(entry_id + ":")
            ):
                raise ValueError(
                    "Entry rewrites must cite only that same source entry."
                )
            if section_id and not evidence_id.startswith(section_id + ":e"):
                raise ValueError(
                    "Section titles must cite entries from that source section."
                )
            evidence.append({"id": evidence_id, "text": facts[evidence_id]})
        if value.text == original:
            return value.text
        supporting_text = "\n".join(item["text"] for item in evidence)
        if _numbers(value.text) - _numbers(supporting_text):
            raise ValueError(
                "Rewritten numbers must occur in the cited source evidence."
            )
        if _urls(value.text) - _urls(supporting_text):
            raise ValueError("Rewritten URLs must occur in the cited source evidence.")
        claim: dict[str, Any] = {
            "id": claim_id,
            "text": value.text,
            "evidence": evidence,
        }
        if context:
            claim["context"] = context
        claims.append(claim)
        return value.text

    mandatory = {
        entry["entry_id"]
        for section in sections.values()
        for entry in section["entries"]
        if mandatory_section(section["title"])
        or (
            isinstance(entry["content"], dict)
            and (
                "institution" in entry["content"]
                or {"company", "position"} <= entry["content"].keys()
            )
        )
    }
    if plan.headline is not None:
        if len(plan.headline.text) > 200:
            raise ValueError("The tailored headline must be at most 200 characters.")
        document["cv"]["headline"] = grounded(
            plan.headline, "headline", original=document["cv"].get("headline")
        )
    selected: dict[str, list[Any]] = {}
    selected_titles: set[str] = set()
    if plan.profile is not None:
        selected["Summary"] = [grounded(plan.profile, "profile")]
        selected_titles.add("summary")
    seen_sections: set[str] = set()
    seen_entries: set[str] = set()
    for section_index, selection in enumerate(plan.sections):
        if (
            selection.section_id not in sections
            or selection.section_id in seen_sections
        ):
            raise ValueError(
                "Select each source section at most once using its exact section_id."
            )
        seen_sections.add(selection.section_id)
        section = sections[selection.section_id]
        title = section["title"]
        if (
            plan.profile is not None
            and normalized_title(title) in PROFILE_SECTION_TITLES
        ):
            raise ValueError(
                "A new profile replaces original summary/profile sections; do not select both."
            )
        section_path = f"sections.{section_index}"
        if selection.title is not None:
            if len(selection.title.text) > 100:
                raise ValueError("Section titles must be at most 100 characters.")
            title = grounded(
                selection.title,
                f"{section_path}.title",
                section_id=selection.section_id,
                context={"original_title": section["title"]},
                original=section["title"],
            )
        normalized = normalized_title(title)
        if not normalized or normalized in selected_titles:
            raise ValueError("Every output section title must be nonempty and unique.")
        if plan.profile is not None and normalized in PROFILE_SECTION_TITLES:
            raise ValueError(
                "Do not add another summary/profile section beside the new profile."
            )
        selected_titles.add(normalized)
        entries = {entry["entry_id"]: entry["content"] for entry in section["entries"]}
        original_mandatory = [entry_id for entry_id in entries if entry_id in mandatory]
        selected_mandatory = [
            choice.entry_id
            for choice in selection.entries
            if choice.entry_id in mandatory
        ]
        if selected_mandatory != original_mandatory:
            raise ValueError(
                "Keep every employment and education entry in its original source order."
            )
        output: list[Any] = []
        for entry_index, choice in enumerate(selection.entries):
            if choice.entry_id not in entries or choice.entry_id in seen_entries:
                raise ValueError(
                    "Entry IDs must belong to their selected section and cannot repeat."
                )
            seen_entries.add(choice.entry_id)
            entry = copy.deepcopy(entries[choice.entry_id])
            entry_path = f"{section_path}.entries.{entry_index}"
            context = {
                "section": section["title"],
                "entry_id": choice.entry_id,
            }
            if isinstance(entry, dict):
                context["identity"] = {
                    key: value
                    for key, value in entry.items()
                    if key not in {"highlights", "summary", "details", "bullet"}
                }
            if choice.rewrite is not None:
                if choice.entry_id in mandatory:
                    raise ValueError(
                        "Keep plain-text employment and education entries unchanged."
                    )
                if not isinstance(entry, str) and not (
                    isinstance(entry, dict) and "bullet" in entry
                ):
                    raise ValueError(
                        "Entry rewrite is allowed only for ordinary text or bullet entries."
                    )
                rewritten = grounded(
                    choice.rewrite,
                    f"{entry_path}.rewrite",
                    entry_id=choice.entry_id,
                    context=context,
                    original=entry if isinstance(entry, str) else entry["bullet"],
                )
                if isinstance(entry, str):
                    entry = rewritten
                else:
                    entry["bullet"] = rewritten
            if choice.summary is not None:
                if not isinstance(entry, dict) or not (
                    {"company", "position"} <= entry.keys()
                    or "institution" in entry
                    or "name" in entry
                ):
                    raise ValueError(
                        "Summary rewrites require a structured experience, education, or named entry."
                    )
                entry["summary"] = grounded(
                    choice.summary,
                    f"{entry_path}.summary",
                    entry_id=choice.entry_id,
                    context=context,
                    original=entry.get("summary"),
                )
            if (
                choice.highlights is not None
                and choice.rewritten_highlights is not None
            ):
                raise ValueError(
                    "Choose source highlight indices or rewritten highlights, not both."
                )
            if choice.highlights is not None:
                if not isinstance(entry, dict) or not isinstance(
                    entry.get("highlights"), list
                ):
                    raise ValueError("Highlight selection requires source highlights.")
                original = entry["highlights"]
                _validate_indices(choice.highlights, original, "Highlight")
                entry["highlights"] = [original[index] for index in choice.highlights]
            elif choice.rewritten_highlights is not None:
                if not isinstance(entry, dict) or not (
                    {"company", "position"} <= entry.keys()
                    or "institution" in entry
                    or "name" in entry
                ):
                    raise ValueError(
                        "Rewritten highlights require a structured experience, education, or named entry."
                    )
                original_highlights = entry.get("highlights", [])
                entry["highlights"] = [
                    grounded(
                        rewrite,
                        f"{entry_path}.rewritten_highlights.{index}",
                        entry_id=choice.entry_id,
                        context=context,
                        original=(
                            rewrite.text
                            if rewrite.text in original_highlights
                            else None
                        ),
                    )
                    for index, rewrite in enumerate(choice.rewritten_highlights)
                ]
            if choice.detail_indices is not None:
                if (
                    not isinstance(entry, dict)
                    or not {"label", "details"} <= entry.keys()
                ):
                    raise ValueError(
                        "Skill item selection requires a source label/details entry."
                    )
                skill_items = split_skill_items(entry["details"])
                _validate_indices(choice.detail_indices, skill_items, "Skill item")
                details = ", ".join(
                    skill_items[index] for index in choice.detail_indices
                )
                # Exact fragments can still lose a shared qualifier when selected
                # independently (e.g. "beginner exposure to Python, SQL"). Audit
                # the reconstructed statement against the full original details.
                entry["details"] = grounded(
                    GroundedText(
                        text=details,
                        evidence_ids=[f"{choice.entry_id}:details"],
                    ),
                    f"{entry_path}.details",
                    entry_id=choice.entry_id,
                    context=context,
                    original=entry["details"],
                )
            output.append(entry)
        selected[title] = output
    if not mandatory <= seen_entries:
        raise ValueError(
            "Keep every source employment and education entry; tailor their highlights instead."
        )
    document["cv"]["sections"] = selected
    return document, claims


def apply_plan(source_yaml: str, plan: TailoringPlan) -> str:
    """Copy immutable facts, apply cited narrative edits, then validate RenderCV."""
    document, _ = _assemble(source_yaml, plan)
    result = dictionary_to_yaml(document)
    load_yaml(result)
    return result


def rewrite_claims(source_yaml: str, plan: TailoringPlan) -> list[dict[str, Any]]:
    """Resolve changed prose and reconstructed skills for a semantic evidence audit."""
    _, claims = _assemble(source_yaml, plan)
    return claims
