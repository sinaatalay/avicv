"""Résumé PDF -> validated RenderCV model, with Gemini through OpenRouter.

Gemini reads the PDF (natively, so scans work too) and writes RenderCV YAML. RenderCV parses and validates it.
If that fails, RenderCV's error messages, which carry YAML line numbers, go back to the model so it can correct
the document. Writing YAML works better than asking Gemini to fill RenderCV's model as JSON: RenderCV's
`sections` mapping has dynamic keys, which Gemini's structured output can't express.
"""

import io
import json
import re
import time
from dataclasses import dataclass

from pydantic_ai import Agent, BinaryContent, ModelRetry, RunContext, TextOutput
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import Model
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pypdf import PdfReader
from rendercv.exception import RenderCVUserValidationError
from rendercv.schema.models.cv.cv import Cv
from rendercv.schema.models.rendercv_model import RenderCVModel
from rendercv.schema.rendercv_model_builder import build_rendercv_dictionary_and_model
from rendercv.schema.sample_generator import dictionary_to_yaml

from ..config import Settings

RENDERCV_GUIDE = """\
## RenderCV format (v2.8)
- Keys under `cv` (all optional): name, headline, location, email, phone, website, social_networks, sections. \
No other keys exist; in particular there is no `summary` key, so put a summary or profile paragraph in a section.
- phone: international format with the country code, e.g. "+1 403 555 0182". If the résumé omits the country \
code, infer it from the candidate's location.
- website: a full URL including https://. Use a list for several.
- social_networks: a list of {network, username}. network is one of: LinkedIn, GitHub, GitLab, IMDB, Instagram, \
ORCID, Mastodon, StackOverflow, ResearchGate, YouTube, Google Scholar, Telegram, WhatsApp, Leetcode, X, Bluesky, \
Reddit. username is the handle only, never a URL (linkedin.com/in/jane-doe -> jane-doe).
- sections: a mapping from the section title as written in the résumé to a list of entries, in the résumé's order. \
All entries of one section must have the same type:
  - ExperienceEntry: company, position, start_date, end_date, location, summary, highlights
  - EducationEntry: institution, area, degree, start_date, end_date, location, summary, highlights
  - NormalEntry (projects, certifications, awards, volunteering, ...): name, date or start_date/end_date, location, \
summary, highlights
  - PublicationEntry: title, authors (list), journal, date, doi (bare, e.g. 10.1234/abc), url
  - OneLineEntry (skill groups, languages, interests): label, details
  - BulletEntry (a plain bulleted list): bullet
  - NumberedEntry (a list numbered 1, 2, 3, ...): number
  - ReversedNumberedEntry (a list numbered downwards, e.g. 4, 3, 2, 1): reversed_number
  - TextEntry: a plain string. Use it for summary/profile paragraphs.
- start_date and end_date must be YYYY-MM-DD, YYYY-MM or YYYY ("Feb 2023" -> 2023-02). Use end_date: present for \
ongoing roles. For a single date or a season ("Fall 2023") use `date` instead. Keep the precision the résumé shows: \
a bare year stays a year (2021), never 2021-01.
- highlights: one string per bullet point.
- Strings may use Markdown: **bold**, *italic*, [text](url). Use a Markdown link when the résumé links a project \
or publication title.
"""

EXAMPLE_YAML = """\
cv:
  name: Maria Santos
  headline: Data Analyst
  location: Toronto, ON
  email: maria.santos@email.com
  phone: +1 416 555 0144
  website: https://mariasantos.ca/
  social_networks:
    - network: LinkedIn
      username: maria-santos
  sections:
    Summary:
      - 'Data analyst with 3 years of experience in retail pricing: forecasting, A/B tests and dashboards.'
    Experience:
      - company: Maple Grocers
        position: Pricing Analyst
        start_date: 2022-03
        end_date: present
        location: Toronto, ON
        highlights:
          - 'Built a demand forecasting model in Python that cut stockouts by 18%'
          - 'Ran 40+ price experiments; results: +2.1% gross margin'
    Education:
      - institution: University of Toronto
        area: Statistics
        degree: BSc
        start_date: 2018-09
        end_date: 2022-04
        highlights:
          - 'GPA: 3.8/4.0'
    Skills:
      - label: Tools
        details: 'Python, SQL, Tableau'
    Awards:
      - name: 'Dean''s List'
        date: 2021
    Talks:
      - number: 'Pricing at scale — PyData Toronto (2023)'
"""

INSTRUCTIONS = f"""\
You convert résumés into RenderCV YAML.

Transcribe; never invent. Keep the résumé's own wording for bullets and summaries, and fix only extraction \
artifacts (words split across lines, stray spaces, bullet symbols). If something isn't in the résumé, leave the \
key out. Keep every section and every bullet; don't summarize or drop content.

Reply with the YAML document only, starting with `cv:`. No code fences, no commentary. Wrap every free-text value \
(summaries, highlights, details, bullets, TextEntry strings, names and titles containing punctuation) in single \
quotes, writing a single quote inside as two (''), so colons and other YAML syntax inside the text stay text.

{RENDERCV_GUIDE}
## Example of the expected output
{EXAMPLE_YAML}"""


def strip_fences(text: str) -> str:
    """Keep the YAML document even if the model wrapped it in a fence or added a sentence around it."""
    text = text.strip()
    if match := re.search(r"```(?:ya?ml)?\s*\n(.*?)\n?```", text, flags=re.DOTALL):
        text = match.group(1)
    if match := re.search(r"^cv:", text, flags=re.MULTILINE):
        text = text[match.start() :]
    return text


def extra_checks(cv: Cv) -> list[str]:
    """Mistakes RenderCV accepts but that render wrong."""
    problems = []
    for i, network in enumerate(cv.social_networks or []):
        if "/" in network.username and network.network not in (
            "StackOverflow",
            "Mastodon",
            "ORCID",
        ):
            problems.append(
                f"cv.social_networks.{i}.username: use the handle only, not a URL"
            )
    if not cv.name:
        problems.append("cv.name is missing")
    if not cv.sections:
        problems.append("cv.sections is empty")
    return problems


def yaml_to_model(ctx: RunContext[None], text: str) -> RenderCVModel:
    """Output function: RenderCV validates the YAML, and its errors go back to the model."""
    try:
        _, model = build_rendercv_dictionary_and_model(strip_fences(text))
    except RenderCVUserValidationError as e:
        lines = [
            f"- line {err.yaml_location[0][0] if err.yaml_location else '?'}"
            f" ({'.'.join(map(str, err.schema_location or ())) or 'document'}): {err.message}"
            for err in e.validation_errors
        ]
        raise ModelRetry(
            "RenderCV rejected the YAML:\n"
            + "\n".join(lines)
            + "\nReply with the corrected full YAML."
        )
    except Exception as e:  # noqa: BLE001 - ruamel and RenderCV raise KeyError, AttributeError, ... on odd YAML
        raise ModelRetry(
            f"This YAML could not be read ({type(e).__name__}: {str(e)[:200]}). Use plain block-style YAML without "
            "anchors, aliases, tags, flow collections or complex keys. Reply with the corrected full YAML."
        )
    if problems := extra_checks(model.cv):
        raise ModelRetry(
            "Fix these problems and reply with the corrected full YAML:\n- "
            + "\n- ".join(problems)
        )
    return model


agent = Agent(
    output_type=TextOutput(yaml_to_model),
    instructions=INSTRUCTIONS,
    retries={"output": 3},
    name="resume_parser",
)


def pdf_link_targets(data: bytes) -> list[str]:
    """URLs of the PDF's link annotations, which the rendered pages don't show (LinkedIn handles, project links)."""
    uris: list[str] = []
    for page in PdfReader(io.BytesIO(data)).pages:
        for annotation in page.get("/Annots") or []:
            action = annotation.get_object().get("/A")
            if (
                action is not None
                and (uri := action.get("/URI"))
                and str(uri) not in uris
            ):
                uris.append(str(uri))
    return uris


def to_yaml(model: RenderCVModel) -> str:
    """The `cv` part as RenderCV YAML, without empty fields."""
    return dictionary_to_yaml(
        {"cv": json.loads(model.cv.model_dump_json(exclude_none=True))}
    )


@dataclass
class ParsedResume:
    model: RenderCVModel
    yaml: str
    seconds: float
    cost_usd: float
    requests: int
    input_tokens: int
    output_tokens: int


def openrouter_model(settings: Settings) -> Model:
    if settings.openrouter_api_key is None:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    provider = OpenRouterProvider(
        api_key=settings.openrouter_api_key.get_secret_value(), app_title="speedhack"
    )
    return OpenRouterModel(settings.parse_model, provider=provider)


async def parse_resume(pdf: bytes, settings: Settings) -> ParsedResume:
    links = pdf_link_targets(pdf)
    note = (
        "Hyperlink targets embedded in the PDF, which the page images don't show:\n"
        + "\n".join(f"- {u}" for u in links)
        if links
        else "The PDF has no hyperlinks."
    )
    started = time.perf_counter()
    result = await agent.run(
        [
            "Convert this résumé.",
            BinaryContent(data=pdf, media_type="application/pdf"),
            note,
        ],
        model=openrouter_model(settings),
        model_settings=OpenRouterModelSettings(
            temperature=0,
            # Also the worst-case cost OpenRouter holds against the balance while a request runs
            max_tokens=8_000,
            openrouter_reasoning={"effort": settings.parse_reasoning},
        ),
    )
    responses = [m for m in result.all_messages() if isinstance(m, ModelResponse)]
    return ParsedResume(
        model=result.output,
        yaml=to_yaml(result.output),
        seconds=time.perf_counter() - started,
        cost_usd=sum((m.provider_details or {}).get("cost", 0) or 0 for m in responses),
        requests=result.usage.requests,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
    )


def load_yaml(text: str) -> RenderCVModel:
    """Rebuild the model from YAML stored earlier."""
    return build_rendercv_dictionary_and_model(text)[1]
