import asyncio
import io
import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from backend.resumes import render
from backend.resumes.design import default_design

CV = """cv:
  name: Test Candidate
  email: test@example.com
  website: https://example.com
  social_networks:
    - network: GitHub
      username: test
  sections:
    Summary:
      - 'Built **reliable** software using *Python* and C++.'
    Experience:
      - company: Example Corp
        position: Software Engineer
        start_date: 2020
        end_date: present
        location: Remote
        highlights:
          - 'Developed [public tools](https://example.com/tools).'
    Education:
      - institution: Example University
        area: Computing
        degree: BS
        date: 2020
    Projects:
      - name: Project One
        summary: A practical project
        highlights:
          - It works
    Skills:
      - label: Languages
        details: Python and C++
    Awards:
      - bullet: Award winner
    Talks:
      - number: A conference talk
    Patents:
      - reversed_number: Patent 1234
    Publications:
      - title: A scientific paper
        authors: [Test Candidate]
        journal: Journal of Tests
        date: 2023
        url: https://example.com/paper
"""


def test_render_all_entry_types_preserves_content_and_links() -> None:
    result = asyncio.run(render.render_resume(CV, default_design()))
    reader = PdfReader(io.BytesIO(result.pdf))
    text = " ".join(page.extract_text() for page in reader.pages)
    for expected in (
        "Test Candidate",
        "Example Corp",
        "Project One",
        "Award winner",
        "conference talk",
        "Patent",
        "scientific paper",
    ):
        assert expected in text
    assert "!!!" not in text
    assert "A practical project" in text
    assert result.page_count == len(reader.pages) == len(result.pages)
    assert result.pages[0].startswith(b"\x89PNG")
    urls = [
        str(annotation.get_object().get("/A", {}).get("/URI", ""))
        for page in reader.pages
        for annotation in page.get("/Annots", [])
    ]
    assert "https://example.com/tools" in urls
    preview = asyncio.run(render.preview_pdf(result.pdf))
    assert preview.page_count == result.page_count
    assert any(size == 10 for size, _ in preview.font_sizes)
    assert any("SourceSans" in name.replace(" ", "") for name, _ in preview.font_faces)


def test_untrusted_markdown_and_metadata_are_literal_not_typst(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("THISMUSTNEVERAPPEAR")
    exploit = f'#read("{secret}")'
    cv = {
        "cv": {
            "name": 'Test "quoted" Candidate',
            "sections": {
                "Summary": [
                    exploit,
                    "`" + exploit + "`",
                    "$$" + exploit + "$$",
                    "[bad](javascript:alert)",
                    "[safe](https://example.com/quote%22)",
                ]
            },
        }
    }
    result = asyncio.run(render.render_resume(json.dumps(cv), default_design()))
    reader = PdfReader(io.BytesIO(result.pdf))
    text = " ".join(page.extract_text() for page in reader.pages)
    assert "THISMUSTNEVERAPPEAR" not in text
    assert "#read" in text
    assert "quoted" in text
    assert reader.metadata is not None
    assert "quoted" in (reader.metadata.title or "")
    urls = [
        str(annotation.get_object().get("/A", {}).get("/URI", ""))
        for page in reader.pages
        for annotation in page.get("/Annots", [])
    ]
    assert all(not url.startswith("javascript:") for url in urls)


@pytest.mark.parametrize(
    "design",
    [
        {"theme": "localmalicious"},
        {"theme": "classic", "typography": {"font_family": "../../secret"}},
        {"theme": "classic", "templates": {"footer": '#read("/etc/passwd")'}},
        {
            "theme": "classic",
            "templates": {"experience_entry": {"main_column": "Invented company"}},
        },
        {"theme": "classic", "page": {"left_margin": "900cm"}},
        {"theme": "classic", "typography": {"font_size": {"body": "1pt"}}},
    ],
)
def test_reject_unsafe_or_unreadable_design(design: dict) -> None:
    with pytest.raises(render.RenderError):
        render.validate_design(design)


def test_design_cannot_hide_cv_content_by_removing_placeholders() -> None:
    with pytest.raises(render.RenderError, match="preserve all content"):
        render.validate_design(
            {
                "theme": "classic",
                "templates": {
                    "experience_entry": {"main_column": "**COMPANY**, POSITION"}
                },
            }
        )


def test_reject_photo_yaml_code_and_recursive_data() -> None:
    for value in (
        "cv: {name: Test, photo: /etc/passwd}",
        "cv: !!python/object/apply:os.system [whoami]",
        "cv: &a {sections: *a}",
    ):
        with pytest.raises(render.RenderError):
            render.cv_dictionary(value)


def test_render_does_not_inherit_keys_and_cancellation_kills_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict = {}
    started = asyncio.Event()
    killed: list[int] = []

    class Process:
        pid = 12345
        returncode = None

        async def wait(self):
            started.set()
            if killed:
                self.returncode = -9
                return -9
            await asyncio.Event().wait()

    process = Process()

    async def spawn(*args, **kwargs):
        observed.update(kwargs)
        return process

    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(render.os, "killpg", lambda pid, sig: killed.append(pid))

    async def run():
        task = asyncio.create_task(render.render_resume(CV, default_design()))
        await started.wait()
        directory = observed["cwd"]
        assert directory.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not directory.exists()

    asyncio.run(run())
    assert killed == [12345]
    assert set(observed["env"]) == {"LANG", "TMPDIR"}


def test_preview_rejects_more_than_ten_pages(make_pdf) -> None:
    with pytest.raises(render.RenderError):
        asyncio.run(render.preview_pdf(make_pdf(pages=11)))


def test_parser_never_loads_custom_theme_or_external_photo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai import ModelRetry

    from backend.resumes import parse

    theme = tmp_path / "customtheme"
    theme.mkdir()
    (theme / "Header.j2.typ").write_text("anything")
    marker = tmp_path / "executed"
    (theme / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    monkeypatch.chdir(tmp_path)
    for extra in (
        "\ndesign:\n  theme: customtheme\n",
        "\nsettings:\n  render_command:\n    output_folder: /tmp/unsafe\n",
    ):
        with pytest.raises(ModelRetry, match="only the cv key"):
            parse.yaml_to_model(None, CV + extra)  # ty: ignore[invalid-argument-type]
    with pytest.raises(ModelRetry, match="External photos"):
        parse.yaml_to_model(None, "cv:\n  name: Candidate\n  photo: /etc/passwd\n")  # ty: ignore[invalid-argument-type]
    assert not marker.exists()
