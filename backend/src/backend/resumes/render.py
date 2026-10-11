"""Bounded RenderCV rendering in an isolated, secret-free child process.

RenderCV is a document authoring library, not an untrusted-input sandbox: it
supports local themes, photos, raw Typst and template overrides. This adapter
uses only bundled templates/fonts, treats CV Markdown as text plus safe inline
formatting, and compiles inside a disposable Typst filesystem root.
"""

import asyncio
import contextlib
import ctypes
import hashlib
import io
import json
import math
import os
import re
import signal
import sys
import tarfile
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element

from rendercv.schema.models.cv.cv import Cv
from rendercv.schema.models.design.built_in_design import built_in_design_adapter
from rendercv.schema.models.design.font_family import available_font_families
from ruamel.yaml import YAML

RENDER_VERSION = "1"
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PAGES = 10
MAX_INPUT_BYTES = 512 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_TOTAL_BYTES = 25 * 1024 * 1024


class RenderError(ValueError):
    """A document could not be safely rendered within the limits."""


@dataclass(frozen=True)
class RenderedResume:
    pdf: bytes
    pages: tuple[bytes, ...]
    page_count: int
    font_sizes: tuple[tuple[float, int], ...] = ()
    font_faces: tuple[tuple[str, int], ...] = ()


def _bounded_json(value: Any) -> None:
    """Reject recursive YAML structures, exotic values and excessive nesting."""
    count = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal count
        count += 1
        if count > 15000 or depth > 15:
            raise RenderError("The document is too complex to render.")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise RenderError("Document keys must be text.")
            for child in item.values():
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
        elif item is not None and not isinstance(item, str | bool | int | float):
            raise RenderError("Unsupported document value.")

    visit(value, 0)
    if len(json.dumps(value).encode()) > MAX_INPUT_BYTES:
        raise RenderError("The document is too large to render.")


def cv_dictionary(cv_yaml: str, *, strict: bool = False) -> dict[str, Any]:
    """Read only CV content; never evaluate RenderCV design/settings from YAML."""
    if len(cv_yaml.encode()) > MAX_INPUT_BYTES:
        raise RenderError("The document is too large to render.")
    reader = YAML(typ="safe", pure=True)
    # Preserve YYYY-MM-DD values as strings, as RenderCV does.
    reader.constructor.yaml_constructors["tag:yaml.org,2002:timestamp"] = (
        lambda loader, node: loader.construct_scalar(node)
    )
    try:
        data = reader.load(cv_yaml)
        _bounded_json(data)
        if not isinstance(data, dict) or not isinstance(data.get("cv"), dict):
            raise RenderError("The document has no CV content.")
        if strict and set(data) != {"cv"}:
            raise RenderError(
                "The parsed document must contain only the cv key, without design, settings or locale."
            )
        cv = data["cv"]
        if cv.get("photo"):
            raise RenderError("External photos are not supported.")
        validated = Cv.model_validate(cv)
        if not validated.name or not validated.sections:
            raise RenderError("The CV must contain a name and sections.")
        return validated.model_dump(mode="json", exclude_none=True, warnings=False)
    except RenderError:
        raise
    except Exception as exc:
        raise RenderError("The CV document is invalid.") from exc


def validate_design(design: dict[str, Any]) -> dict[str, Any]:
    """Built-in schema only: RenderCV's general validator can import local code."""
    _bounded_json(design)
    try:
        validated = built_in_design_adapter.validate_python(design)
        result = validated.model_dump(mode="json", warnings=False)
    except Exception as exc:
        raise RenderError("The RenderCV design is invalid.") from exc
    if any(
        font not in available_font_families
        for font in result["typography"]["font_family"].values()
    ):
        raise RenderError("Use a bundled RenderCV font.")

    # Prevent pathological layouts while retaining useful RenderCV flexibility.
    def dimensions(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for name, item in value.items():
                dimensions(item, name)
        elif isinstance(value, str) and (
            match := re.fullmatch(r"(-?\d+(?:\.\d+)?)(pt|cm|mm|in|em)", value)
        ):
            amount = (
                float(match[1])
                * {"pt": 1, "cm": 28.35, "mm": 2.835, "in": 72, "em": 10}[match[2]]
            )
            if not 0 <= amount <= 200:
                raise RenderError(
                    f"Design dimension {key} is outside the supported range."
                )

    dimensions(result)
    for key, value in result["typography"]["font_size"].items():
        match = re.fullmatch(r"(\d+(?:\.\d+)?)(pt|cm|mm|in|em)", value)
        if match:
            points = (
                float(match[1])
                * {"pt": 1, "cm": 28.35, "mm": 2.835, "in": 72, "em": 10}[match[2]]
            )
            if not 7 <= points <= 48:
                raise RenderError(f"The {key} font size must be between 7 and 48pt.")
    allowed_tokens = {
        "NAME",
        "PAGE_NUMBER",
        "TOTAL_PAGES",
        "CURRENT_DATE",
        "LAST_UPDATED",
        "MONTH_NAME",
        "MONTH_ABBREVIATION",
        "MONTH",
        "MONTH_IN_TWO_DIGITS",
        "DAY",
        "DAY_IN_TWO_DIGITS",
        "YEAR",
        "YEAR_IN_TWO_DIGITS",
        "START_DATE",
        "END_DATE",
        "HOW_MANY_YEARS",
        "YEARS",
        "HOW_MANY_MONTHS",
        "MONTHS",
        "LABEL",
        "DETAILS",
        "INSTITUTION",
        "AREA",
        "SUMMARY",
        "HIGHLIGHTS",
        "DEGREE",
        "DEGREE_WITH_AREA",
        "in",
        "of",
        "Page",
        "LOCATION",
        "DATE",
        "COMPANY",
        "POSITION",
        "TITLE",
        "AUTHORS",
        "URL",
        "DOI",
        "JOURNAL",
    }

    def templates(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                templates(item)
        elif isinstance(value, str):
            tokens = re.findall(r"[A-Za-z_]+", value)
            if len(value) > 1200 or any(
                token not in allowed_tokens for token in tokens
            ):
                raise RenderError(
                    "Design templates may contain only RenderCV placeholders and punctuation."
                )
            if any(
                character in value for character in ("#", "{", "}", "`", "<", ">", "\\")
            ):
                raise RenderError("Executable template markup is not supported.")

    templates(result["templates"])
    required = {
        "one_line_entry": {"LABEL", "DETAILS"},
        "education_entry": {
            "INSTITUTION",
            "AREA",
            "DEGREE",
            "SUMMARY",
            "HIGHLIGHTS",
            "LOCATION",
            "DATE",
        },
        "normal_entry": {"NAME", "SUMMARY", "HIGHLIGHTS", "LOCATION", "DATE"},
        "experience_entry": {
            "COMPANY",
            "POSITION",
            "SUMMARY",
            "HIGHLIGHTS",
            "LOCATION",
            "DATE",
        },
        "publication_entry": {"TITLE", "SUMMARY", "AUTHORS", "URL", "JOURNAL", "DATE"},
    }
    for kind, fields in required.items():
        text = " ".join(value or "" for value in result["templates"][kind].values())
        present = set(re.findall(r"[A-Z_]+", text))
        if "DEGREE_WITH_AREA" in present:
            present.update({"DEGREE", "AREA"})
        if not fields <= present:
            raise RenderError(
                f"The {kind} design must preserve all content placeholders."
            )
    return result


async def _run_worker(
    operation: str, payload: bytes, *, timeout_seconds: float, previews: bool = True
) -> RenderedResume:
    if timeout_seconds <= 0:
        raise ValueError("Render timeout must be positive.")
    with tempfile.TemporaryDirectory(prefix="avicv-render-") as temporary:
        folder = Path(temporary)
        folder.chmod(0o700)
        input_path = folder / "input"
        input_path.write_bytes(payload)
        input_path.chmod(0o600)
        # No API credentials, inherited application configuration, user font or
        # template directories are visible to the renderer through its environment.
        env = {"LANG": "en_US.UTF-8", "TMPDIR": temporary}
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(Path(__file__).resolve()),
            "--worker",
            operation,
            str(folder),
            "1" if previews else "0",
            cwd=folder,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            async with asyncio.timeout(timeout_seconds):
                await process.wait()
        except BaseException:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
            raise
        if process.returncode != 0:
            raise RenderError("The CV could not be rendered. Please try again.")
        metadata = json.loads((folder / "result.json").read_text())
        page_count = metadata["page_count"]
        if not isinstance(page_count, int) or not 1 <= page_count <= MAX_PAGES:
            raise RenderError("The CV has too many pages.")
        pdf_path = folder / "output.pdf"
        pdf = pdf_path.read_bytes() if operation == "render" else payload
        if len(pdf) > MAX_PDF_BYTES or not pdf.startswith(b"%PDF-"):
            raise RenderError("Invalid rendered PDF.")
        pages = []
        if previews:
            for index in range(page_count):
                page_path = folder / f"page-{index}.png"
                if page_path.stat().st_size > MAX_IMAGE_BYTES:
                    raise RenderError("The preview is too large.")
                pages.append(page_path.read_bytes())
        if sum(map(len, pages)) > MAX_IMAGE_TOTAL_BYTES:
            raise RenderError("The previews are too large.")
        return RenderedResume(
            pdf=pdf,
            pages=tuple(pages),
            page_count=page_count,
            font_sizes=tuple(tuple(item) for item in metadata.get("font_sizes", [])),
            font_faces=tuple(tuple(item) for item in metadata.get("font_faces", [])),
        )


async def render_resume(
    cv_yaml: str,
    design: dict[str, Any],
    *,
    timeout_seconds: float = 60,
    previews: bool = True,
) -> RenderedResume:
    payload = json.dumps(
        {"cv": cv_dictionary(cv_yaml), "design": validate_design(design)}
    ).encode()
    return await _run_worker(
        "render", payload, timeout_seconds=timeout_seconds, previews=previews
    )


async def preview_pdf(pdf: bytes, *, timeout_seconds: float = 60) -> RenderedResume:
    if len(pdf) > MAX_PDF_BYTES or not pdf.startswith(b"%PDF-"):
        raise RenderError("Invalid source PDF.")
    return await _run_worker("preview", pdf, timeout_seconds=timeout_seconds)


def _escape_typst(text: str) -> str:
    # Unlike RenderCV's stock parser, deliberately preserve no executable Typst.
    return text.translate(
        str.maketrans({char: "\\" + char for char in '\\#$[]{}*_<@`~"'})
    )


def _safe_url(url: str) -> str | None:
    try:
        return (
            url
            if urlsplit(url).scheme.lower() in {"https", "http", "mailto", "tel"}
            else None
        )
    except ValueError:
        return None


def _markdown_serializer(element: Element) -> str:
    parts = [_escape_typst(element.text or "")]
    for child in element:
        if child.get("class") == "admonition-title":
            continue
        inner = _markdown_serializer(child)
        if child.tag == "div" and "admonition" in child.get("class", ""):
            content = "#summary[" + inner.strip("\n").replace("\n", " \\ ") + "]"
        elif child.tag == "strong":
            content = f"#strong[{inner}]"
        elif child.tag == "em":
            content = f"#emph[{inner}]"
        elif child.tag == "a" and (url := _safe_url(child.get("href", ""))):
            content = f"#link({json.dumps(url)})[{inner}]"
        else:
            content = inner
        parts.extend((content, _escape_typst(child.tail or "")))
    return "".join(parts)


def _install_safe_templates() -> None:
    """Adapt only this disposable process; never patch the serving process."""
    import jinja2
    import markdown
    from rendercv.renderer.templater import connections, model_processor, templater
    from rendercv.renderer.templater.string_processor import substitute_placeholders

    parser = markdown.Markdown(extensions=["admonition"])
    for name in ("html_block",):
        parser.preprocessors.deregister(name)
    parser.inlinePatterns.deregister("html")
    for name in ("hashheader", "setextheader", "olist", "ulist", "quote"):
        parser.parser.blockprocessors.deregister(name)
    parser.output_formats["safe_typst"] = _markdown_serializer
    parser.set_output_format("safe_typst")
    parser.stripTopLevelTags = False

    def markdown_to_typst(text: str) -> str:
        # RenderCV wraps standalone entry summaries in an admonition block.
        # Keep that generated block together so its marker never becomes text.
        lines = text.split("\n")
        blocks = []
        index = 0
        while index < len(lines):
            block = [lines[index]]
            index += 1
            if block[0].startswith("!!!"):
                while index < len(lines) and lines[index].startswith("    "):
                    block.append(lines[index])
                    index += 1
            blocks.append(parser.reset().convert("\n".join(block)))
        return "\n".join(blocks)

    model_processor.markdown_to_typst = markdown_to_typst  # ty: ignore[invalid-assignment]
    connections.markdown_to_typst = markdown_to_typst  # ty: ignore[invalid-assignment]

    def safe_connections(model: Any) -> list[str]:
        result = []
        for connection in connections.parse_connections(model):
            body = markdown_to_typst(connection.body)
            if model.design.header.connections.show_icons:
                # Icon names are arguments, never executable markup.
                body = f"#connection-with-icon({json.dumps(connection.fontawesome_icon)})[{body}]"
            if model.design.header.connections.hyperlink and (
                url := _safe_url(connection.url or "")
            ):
                body = f"#link({json.dumps(url)}, icon: false, if-underline: false, if-color: false)[{body}]"
            result.append(body)
        return result

    connections.compute_connections_for_typst = safe_connections  # ty: ignore[invalid-assignment]

    original_footer = model_processor.render_footer_template

    def safe_footer(template: str, **kwargs: Any) -> str:
        # Expand only RenderCV's two known page counters after text has been escaped.
        template = substitute_placeholders(
            template,
            {"PAGE_NUMBER": "AVICVPAGENUMBER", "TOTAL_PAGES": "AVICVTOTALPAGES"},
        )
        output = original_footer(template, **kwargs)
        return output.replace("AVICVPAGENUMBER", "#str(here().page())").replace(
            "AVICVTOTALPAGES", "#str(counter(page).final().first())"
        )

    model_processor.render_footer_template = safe_footer  # ty: ignore[invalid-assignment]

    # Use package-owned templates exclusively. Quote every scalar interpolated
    # into a Typst string (metadata, font names and contact separators).
    templates = {}
    for path in templater.templates_directory.rglob("*.j2.typ"):
        source = path.read_text()
        source = re.sub(r'"{{\s*([^{}]+?)\s*}}"', r"{{ \1|typst_json }}", source)
        templates[str(path.relative_to(templater.templates_directory))] = source
    environment = jinja2.Environment(
        loader=jinja2.DictLoader(templates), trim_blocks=True, lstrip_blocks=True
    )
    environment.filters["typst_json"] = lambda value: json.dumps(
        str(value), ensure_ascii=False
    )
    environment.filters["clean_url"] = templater.clean_url
    environment.filters["strip"] = lambda value: value.strip()
    templater.get_jinja2_environment = lambda *args: environment  # ty: ignore[invalid-assignment]


def _offline_package_path() -> Path:
    from rendercv.renderer.pdf_png import get_package_path

    root = get_package_path()
    archive_path = Path(__file__).parent / "vendor" / "fontawesome-0.6.0.tar.gz"
    data = archive_path.read_bytes()
    if (
        hashlib.sha256(data).hexdigest()
        != "31374c788d6850c7fd3da742a67e8a753e5644fa0c677b845f75be8aa488380c"
    ):
        raise RenderError("The bundled icon package is invalid.")
    destination = root / "preview" / "fontawesome" / "0.6.0"
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for name in (
            "lib.typ",
            "lib-impl.typ",
            "lib-gen-map.typ",
            "lib-gen-func.typ",
            "typst.toml",
        ):
            member = archive.getmember(name)
            if not member.isfile() or member.size > 300000:
                raise RenderError("Invalid bundled package member.")
            stream = archive.extractfile(member)
            if stream is None:
                raise RenderError("Missing bundled package member.")
            (destination / name).write_bytes(stream.read())
    return root


def _worker(operation: str, folder: Path, previews: bool) -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_PDF_BYTES, MAX_PDF_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    if sys.platform != "darwin":
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    if operation == "render":
        import rendercv_fonts
        import typst
        from rendercv.renderer.templater.templater import render_full_template
        from rendercv.schema.models.rendercv_model import RenderCVModel

        payload = json.loads((folder / "input").read_text())
        payload["design"] = validate_design(payload["design"])
        model = RenderCVModel.model_validate(payload)
        _install_safe_templates()
        source = render_full_template(model, "typst")
        (folder / "cv.typ").write_text(source)
        compiler = typst.Compiler(
            root=folder,
            font_paths=rendercv_fonts.paths_to_font_folders,
            ignore_system_fonts=True,
            package_path=_offline_package_path(),
            package_cache_path=folder / "packages",
        )
        compiler.compile(
            input=folder / "cv.typ", output=folder / "output.pdf", format="pdf"
        )
        pdf = (folder / "output.pdf").read_bytes()
    else:
        pdf = (folder / "input").read_bytes()
    if len(pdf) > MAX_PDF_BYTES:
        raise RenderError("PDF size exceeded.")
    import pypdfium2

    with pypdfium2.PdfDocument(pdf) as document:
        count = len(document)
        if not 1 <= count <= MAX_PAGES:
            raise RenderError("PDF page count exceeded.")
        fonts: Counter[float] = Counter()
        font_faces: Counter[str] = Counter()
        remaining_characters = 100000
        if previews:
            for index in range(count):
                page = document[index]
                try:
                    # Aggregate point sizes help the vision model compare type
                    # scale independently of screenshot resolution. Scans have
                    # no text and simply produce no metrics.
                    text_page = page.get_textpage()
                    try:
                        sample_size = min(text_page.count_chars(), remaining_characters)
                        remaining_characters -= sample_size
                        for character in range(sample_size):
                            size = pypdfium2.raw.FPDFText_GetFontSize(
                                text_page, character
                            )
                            if math.isfinite(size) and 3 <= size <= 100:
                                fonts[round(size, 1)] += 1
                            if character % 8 == 0:
                                flags = ctypes.c_int()
                                length = pypdfium2.raw.FPDFText_GetFontInfo(
                                    text_page, character, None, 0, ctypes.byref(flags)
                                )
                                if 1 < length <= 256:
                                    buffer = ctypes.create_string_buffer(length)
                                    pypdfium2.raw.FPDFText_GetFontInfo(
                                        text_page,
                                        character,
                                        buffer,
                                        length,
                                        ctypes.byref(flags),
                                    )
                                    name = buffer.value.decode(
                                        "utf-8", errors="replace"
                                    )
                                    font_faces[re.sub(r"^[A-Z]{6}\+", "", name)] += 1
                    finally:
                        text_page.close()
                    width, height = page.get_size()
                    if width <= 0 or height <= 0 or max(width, height) > 20000:
                        raise RenderError("Invalid PDF page dimensions.")
                    bitmap = page.render(scale=min(1.5, 1200 / max(width, height)))
                    try:
                        image = bitmap.to_pil()
                        stream = io.BytesIO()
                        image.save(stream, format="PNG")
                        data = stream.getvalue()
                        if len(data) > MAX_IMAGE_BYTES:
                            raise RenderError("Preview size exceeded.")
                        (folder / f"page-{index}.png").write_bytes(data)
                    finally:
                        bitmap.close()
                finally:
                    page.close()
        (folder / "result.json").write_text(
            json.dumps(
                {
                    "page_count": count,
                    "font_sizes": fonts.most_common(12),
                    "font_faces": font_faces.most_common(12),
                }
            )
        )


if __name__ == "__main__":
    if (
        len(sys.argv) != 5
        or sys.argv[1] != "--worker"
        or sys.argv[2] not in {"render", "preview"}
    ):
        raise SystemExit(2)
    _worker(sys.argv[2], Path(sys.argv[3]), sys.argv[4] == "1")
