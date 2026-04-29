import json
import logging
import re
from pathlib import Path
from typing import Optional, Sequence, Union
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel
from ontology.openai_client import BaseOpenAIClient, NoneResult
from ontology.utilities import TEXT_USER_PROMPT


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())


SELECTED_SECTION_IDS = [
    "4.1", "4.3", "4.4", "5.3", "5.4",
    "6.4", "6.4.4", "6.4.5", "7.1.3", "7.1.7",
    "4.6", "6.1", "6.2", "6.3", "6.4.2",
    "6.4.3", "6.4.7", "7.1.2", "7.1.9", "7.2.1.3", "7.2.2.4",
]


class Requirement(BaseModel):
    text: str


class ExtractedRequirements(BaseModel):
    paragraph: Optional[str] = None
    reqs: Optional[list[Requirement]] = None


REQ_SYS_PROMPT = """
Ты — эксперт по нормативным документам в строительной сфере.

Твоя задача — извлечь из входного текста элементарные требования.

Элементарное требование — это одно атомарное нормативное требование:
- одно условие,
- одна сущность,
- одно свойство.

Если требований в тексте нет, верни reqs: null.

Верни только JSON строго такого вида:

{
  "paragraph": "номер пункта или null",
  "reqs": [
    {"text": "текст элементарного требования"}
  ]
}

Правила:
- Не добавляй пояснений.
- Не добавляй markdown.
- Не добавляй текст вне JSON.
- Не выдумывай требования, которых нет во входном тексте.
- Если есть перечисление, разбей на отдельные требования.
- Если в тексте есть номер пункта, укажи его в paragraph.
- Если номера пункта нет, укажи paragraph: null.
- Не включай в reqs редакционные пометки вроде:
  "Измененная редакция",
  "Введен дополнительно",
  "Изм. № 2",
  "Таблица 7.4".
"""


def is_trash_requirement(text: str) -> bool:
    if not text:
        return True

    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return True

    low = cleaned.lower()

    if cleaned in {"-", "–", "—"}:
        return True
    if "измененная редакция" in low:
        return True
    if "введен дополнительно" in low:
        return True
    if low.startswith("таблица "):
        return True
    if re.fullmatch(r"изм\.\s*№\s*\d+", low):
        return True

    return False


def load_selected_sections_from_json(
    json_path: Union[str, Path],
    selected_section_ids: Sequence[str],
) -> list[str]:
    json_path = Path(json_path)

    with json_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    records = payload["records"]
    selected = set(selected_section_ids)

    fragments: list[str] = []
    seen: set[str] = set()

    for record in records:
        clause_id = record.get("clause_id")

        if clause_id not in selected:
            continue

        text = record.get("parent_section_text") or record.get("paragraph_text")

        if not text:
            continue

        text = re.sub(r"\s+", " ", text).strip()

        if not text or clause_id in seen:
            continue

        seen.add(clause_id)
        fragments.append(f"{clause_id} {text}")

    return fragments


class RequirementExtractor(BaseOpenAIClient):
    TEMPERATURE = 0.1
    FREQUENCY_PENALTY = 0.02
    TOP_P = 0.95
    MAX_TOKENS = 8000

    def __init__(
        self,
        openai_base_url: str,
        openai_api_token: str,
        model_name: str = "Qwen2.5-72b-64k",
        min_request_timeout: float = BaseOpenAIClient.DEFAULT_REQUEST_TIMEOUT,
        max_concurrent_requests: int = 8,
        **kwargs,
    ):
        super().__init__(
            openai_base_url=openai_base_url,
            openai_api_token=openai_api_token,
            model_name=model_name,
            result_model=ExtractedRequirements,
            min_request_timeout=min_request_timeout,
            max_concurrent_requests=max_concurrent_requests,
        )

    @property
    def pbar_description(self) -> str:
        return "Extracting requirements"

    @property
    def pbar_unit(self) -> str:
        return "fragment"

    async def _build_messages(self, fragment: str) -> list[ChatCompletionMessageParam]:
        user_prompt = TEXT_USER_PROMPT.format(text=fragment)
        return [
            {"role": "system", "content": REQ_SYS_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    def extract_requirements_from_fragments(
        self,
        fragments: Sequence[str],
    ) -> list[dict]:
        if not fragments:
            return []

        responses: list[Union[ExtractedRequirements, NoneResult]] = self.make_requests(
            fragments
        )
        results: list[dict] = []

        for fragment, response in zip(fragments, responses):
            if not response:
                logger.warning("[Warning] LLM failed on fragment:\n%s\n", fragment[:1000])
                results.append({"paragraph": fragment, "reqs": None})
                continue

            reqs_payload = []
            seen = set()

            if response.reqs:
                for req in response.reqs:
                    if not req or not req.text or is_trash_requirement(req.text):
                        continue

                    cleaned = re.sub(r"\s+", " ", req.text).strip()

                    if cleaned and cleaned not in seen:
                        seen.add(cleaned)
                        reqs_payload.append({"text": cleaned})

            results.append(
                {
                    "paragraph": response.paragraph,
                    "source_section": fragment,
                    "reqs": reqs_payload if reqs_payload else None,
                }
            )

        return results


def extract_requirements_from_selected_sections(
    json_path: Union[str, Path],
    selected_section_ids: Sequence[str],
    openai_base_url: str,
    openai_api_token: str,
    model_name: str = "Qwen2.5-72b-64k",
    min_request_timeout: float = BaseOpenAIClient.DEFAULT_REQUEST_TIMEOUT,
    max_concurrent_requests: int = 8,
) -> list[dict]:
    fragments = load_selected_sections_from_json(
        json_path=json_path,
        selected_section_ids=selected_section_ids,
    )

    logger.info("Loaded %s selected sections from %s", len(fragments), json_path)

    extractor = RequirementExtractor(
        openai_base_url=openai_base_url,
        openai_api_token=openai_api_token,
        model_name=model_name,
        min_request_timeout=min_request_timeout,
        max_concurrent_requests=max_concurrent_requests,
    )

    results = extractor.extract_requirements_from_fragments(fragments)

    logger.info("Extracted requirements from %s selected sections", len(results))
    return results


def save_grouped_requirements_to_json(
    results: Sequence[dict],
    output_path: Union[str, Path],
) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(list(results), f, ensure_ascii=False, indent=2)

    logger.info("Saved %s grouped fragments to %s", len(results), output_path)


if __name__ == "__main__":
    JSON_PATH = "itmo-practice-spring-2026/parsed_fragments_only_v27_СП_251.json"
    OPENAI_BASE_URL = "xxx"
    OPENAI_API_TOKEN = "xxx"
    MODEL_NAME = "xxx"


    results = extract_requirements_from_selected_sections(
        json_path=JSON_PATH,
        selected_section_ids=SELECTED_SECTION_IDS,
        openai_base_url=OPENAI_BASE_URL,
        openai_api_token=OPENAI_API_TOKEN,
        model_name=MODEL_NAME,
        min_request_timeout=600.0,
        max_concurrent_requests=8,
    )

    save_grouped_requirements_to_json(
        results,
        "requirements_selected_sections_qwen.json",
    )

    for item in results[:5]:
        print("=" * 80)
        print("paragraph:")
        print(item["paragraph"])
        print("source_section:")
        print(item["source_section"])
        print("reqs:")
        print(item["reqs"])