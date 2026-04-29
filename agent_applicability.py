import json
import logging
from pathlib import Path
from typing import Sequence, Optional, Union

from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, Field

from ontology.openai_client import BaseOpenAIClient, NoneResult
from ontology.entities import (
    BaseRequirement,
    GraphRequirement,
    Applicability,
    Statement,
    ExternalReference,
    Entity,
    Operator,
    StatementType,
)
from ontology.utilities import escape_braces, TEXT_USER_PROMPT

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)


class StructuredRequirement(BaseModel):
    object_entity: str = Field(
        description="Название целевой сущности, к которой относится требование."
    )
    subject_entity: str = Field(
        description=(
            "Название исходной сущности. Если требование относится к одной сущности, "
            "то subject_entity == object_entity."
        )
    )
    relation: str = Field(
        description=(
            "Глагольная формулировка связи. subject_entity - relation - object_entity. "
            "Если subject_entity == object_entity, то relation = \"является\"."
        )
    )
    applicability: Optional[list[Applicability]] = Field(
        default=None,
        description=(
            "Набор applicability, при которых накладывается требование statement. "
            "Описывает чёткие условия на свойства сущностей."
        ),
    )
    statement: Statement = Field(
        description="Требование, накладываемое на object_entity."
    )
    external_refs: Optional[list[ExternalReference]] = Field(
        default=None,
        description="Ссылки на требования из других нормативных документов.",
    )


class Result(BaseModel):
    structured_requirement: Optional[StructuredRequirement] = Field(
        description=(
            "Структурированное требование. "
            "null - если в тексте отсутствует требование, которое можно формализоввать."
        )
    )


class ContextualBaseRequirement:
    def __init__(
            self,
            req_text: str,
            source,
            local_context: Optional[str] = None,
            global_context: Optional[str] = None,
    ):
        self.req_text = req_text
        self.source = source
        self.local_context = local_context
        self.global_context = global_context


STRUCTURED_REQ_SYS_PROMPT = f"""
**Role**
You are a requirements formalizer for regulatory documents in the construction domain.

**Task**
Your task is to analyze the input text containing an **elementary regulatory requirement** from the construction field and transform it into a structured JSON representation, strictly following the specified schema. The goal is to formalize the semantic relationships and constraints described in the text between construction-related entities.

**Input**
You will receive three fragments:

1. `requirement_text` — the elementary regulatory requirement that must be formalized.
2. `local_context` — the local context of the requirement. It describes the condition, situation, object, user group, project scenario, or scope in which the requirement applies.
3. `global_context` — the global context of the document: terms, definitions, abbreviations, and scope limitations.

Use requirement_text as the primary source for subject_entity, object_entity, relation, and statement.

Use local_context only to уточнить conditions of applicability. If local_context contains conditions, scope limitations, object type, room type, building type, user group, number of floors, occupancy, design scenario, or other entity properties, consider them when filling applicability.

Use global_context only to understand terminology, abbreviations, and the document scope. Do not extract standalone requirements from global_context.

**Output Format**
Return the result in JSON format according to the following schema:
```json
{{output_json_schema}}
```

**Rules**
1. Identify entities and relation (subject_entity, object_entity, relation)
*   Goal: Extract two key entities from the requirement and determine the logical relation between them.
*   Steps:
    1. Identify the target entity — the entity to which the requirement is directly applied (restriction/constraint). This will be object_entity.
    2. Identify the source entity — the contextual or “parent” entity connected to the target entity. If the requirement concerns only one entity, then subject_entity and object_entity are the same.    
    3. Choose a verbal relation (relation) that formally describes how subject_entity is connected to object_entity.*   

    **Important:**
    *   In subject_entity and object_entity, specify only the entity name, without explanations or functions.
    *   If the requirement concerns only one entity, then: `subject_entity` == `object_entity`, то `relation` = "является".
    *   The relation must be semantically accurate and reflect the meaning of the sentence.

2. Determine applicability conditions (applicability)
*   Goal: Describe the explicit conditions under which the requirement (statement) is valid.
*   Include::
    *   Conditions on properties of subject_entity or object_entity
    *   Conditions explicitly stated in the text or logically implied
*   Do NOT include:
    *   Purpose/context explanations (“for evacuation”, “for safety purposes”)
    *   References to other regulations (“in accordance with...”)
    *  Modal verbs (“may”, “should”, “recommended”) — these affect statement.type, not applicability
*   Format:
    *   applicability is an array of condition objects
    *   Каждое условие – это объект с полями: `entity` (указывает, к какой сущности относится условие: `subject_entity` или `object_entity`), `property`, `operator`, `value`.
    *   A single entity may have multiple conditions.
*   When analyzing applicability, use both requirement_text and local_context.
*   If local_context defines applicability conditions, include them if they can be expressed as entity properties.*   Если `local_context` содержит только пояснение без условия применимости, не добавляй его в `applicability`.
*   If local_context only contains explanation and no condition, do not include it.
*   Do not copy local_context as raw text into one field — split it into formalized conditions whenever possible.
*   Do not use global_context as a source of applicability if it only contains definitions or abbreviations.

3. Formulate the requirement (statement)
*   Goal: Formalize the actual constraint applied to object_entity.
*   Fields:
    *   `property`: Свойство `object_entity`, к которому применяется требование.
    *   `operator`: Тип ограничения (`==`, `!=`, `>`, `>=`, `<`, `<=`, `∈`).
    *   `value`: Значение ограничения.
    *   type values:
        *   `requirement`: for mandatory wording (“must”, “shall”, “is required”, direct imperative wording)
        *   `assumption`: for softer wording (“allowed”, “recommended”, “may”, etc.)

4. General rules and limitations
*   Language: All field values (entity names, properties, relations, units, etc.) must be in Russian.
*   Avoid ambiguity: Do not use vague values such as "any", "other", "etc."
*   References (external_refs): If the requirement cites external regulatory documents, include them in external_refs. Specify the document and, if needed, domain.
*   Non-formalizable cases: If the text does not contain a formalizable requirement (e.g., only a definition, general recommendation, or impossible to isolate a requirement) `structured_requirement: null`.

**Task Examples**
Пример 1:
Input: "Лестничные клетки многоквартирных жилых домов должны иметь естественное освещение через окна, расположенные во внешних стенах, в соответствии с требованиями СП 54.13330.2016."
Output:
```json
{escape_braces(Result(
    structured_requirement=StructuredRequirement(
        object_entity="окна",
        subject_entity="лестничная клетка",
        relation="содержит",
        applicability=[
            Applicability(
                entity=Entity.SUBJECT,
                property="тип здания",
                operator=Operator.EQUAL,
                value="многоквартирный жилой дом"
            ),
            Applicability(
                entity=Entity.OBJECT,
                property="расположение",
                operator=Operator.EQUAL,
                value="внешние стены"
            )
        ],
        statement=Statement(
            property="наличие",
            operator=Operator.EQUAL,
            value="есть",
            type=StatementType.REQUIREMENT
        ),
        external_refs=[
            ExternalReference(
                document="СП 54.13330.2016",
                domain="жилые здания"
            )
        ]
    )
).model_dump_json())}
```

---

Пример 2:
Input: "На территории ОО допускается выделение учебно-опытной зоны."
Output:
```json
{escape_braces(Result(
    structured_requirement=StructuredRequirement(
        object_entity="учебно-опытная зона",
        subject_entity="территория ОО",
        relation="содержит",
        applicability=None,
        statement=Statement(
            property="наличие",
            operator=Operator.EQUAL,
            value="есть",
            type=StatementType.ASSUMPTION
        ),
        external_refs=None
    )
).model_dump_json())}
```

---

Пример 3:
Input: "Двери в учебные помещения, рассчитанные более чем на 20 учащихся, следует предусматривать из коридоров шириной не менее 4 ,0 м."
Output:
```json
{escape_braces(Result(
    structured_requirement=StructuredRequirement(
        object_entity="двери",
        subject_entity="учебные помещения",
        relation="имеют",
        applicability=[
            Applicability(
                entity=Entity.SUBJECT,
                property="вместимость",
                operator=Operator.GREATER_THAN,
                value="20 учащихся"
            )
        ],
        statement=Statement(
            property="место входа",
            operator=Operator.EQUAL,
            value="коридоры шириной не менее 4,0 м",
            type=StatementType.REQUIREMENT
        ),
        external_refs=None
    )
).model_dump_json())}
```

---

Пример 4:
Input: "Для отдыха на участке рекомендуется предусматривать площадки для подвижных игр для обучающихся 1-х классов – не менее 180 м2"
Output:
```json
{escape_braces(Result(
    structured_requirement=StructuredRequirement(
        object_entity="площадка для подвижных игр",
        subject_entity="участок",
        relation="содержит",
        applicability=[
            Applicability(
                entity=Entity.SUBJECT,
                property="назначение",
                operator=Operator.EQUAL,
                value="отдых"
            ),
            Applicability(
                entity=Entity.OBJECT,
                property="пользователи",
                operator=Operator.EQUAL,
                value="обучающиеся 1-х классов"
            )
        ],
        statement=Statement(
            property="площадь",
            operator=Operator.GREATER_EQUAL,
            value="180 м2",
            type=StatementType.ASSUMPTION
        ),
        external_refs=None
    )
).model_dump_json())}
```

---

Пример 5:
Input: "Площадь мастерской по обработке тканей и технологии в основной школе должна составлять не менее 6 м2 на одного обучающегося (13 мест)"
Output:
```json
{escape_braces(Result(
    structured_requirement=StructuredRequirement(
        object_entity="мастерская по обработке тканей и технологии",
        subject_entity="основная школа",
        relation="содержит",
        applicability=None,
        statement=Statement(
            property="площадь на одного обучающегося",
            operator=Operator.GREATER_EQUAL,
            value="6 м2",
            type=StatementType.REQUIREMENT
        ),
        external_refs=None
    )
).model_dump_json())}
```

---

Пример 6:
Input: "Площадь инструментальной в основной школе должна составлять не менее 15 м2 одного помещения"
Output:
```json
{escape_braces(Result(
    structured_requirement=StructuredRequirement(
        object_entity="инструментальная",
        subject_entity="основная школа",
        relation="содержит",
        applicability=None,
        statement=Statement(
            property="площадь",
            operator=Operator.GREATER_EQUAL,
            value="15 м2",
            type=StatementType.REQUIREMENT
        ),
        external_refs=None
    )
).model_dump_json())}
```

---

Пример 7:
Input: "Площадь кабинета ручного труда в начальной школе должна составлять не менее 2,5 м2 на одного обучающегося (13 мест)"
Output:
```json
{escape_braces(Result(
    structured_requirement=StructuredRequirement(
        object_entity="кабинет ручного труда",
        subject_entity="начальная школа",
        relation="содержит",
        applicability=None,
        statement=Statement(
            property="площадь на одного обучающегося",
            operator=Operator.GREATER_EQUAL,
            value="2.5 м2",
            type=StatementType.REQUIREMENT
        ),
        external_refs=None
    )
).model_dump_json())}
```

---

Пример 8:
Input: "Из группы мастерских на первом этаже рекомендуется предусматривать дополнительный обособленный выход непосредственно наружу через коридор, в который отсутствует выход из классов, кабинетов и лабораторий."
Output:
```json
{escape_braces(Result(
    structured_requirement=StructuredRequirement(
        object_entity="выход",
        subject_entity="группа мастерских",
        relation="имеет",
        applicability=[
            Applicability(
                entity=Entity.SUBJECT,
                property="этаж",
                operator=Operator.EQUAL,
                value="1"
            )
        ],
        statement=Statement(
            property="тип",
            operator=Operator.EQUAL,
            value="обособленный (через коридор, в который отсутствует выход из классов, кабинетов и лабораторий)",
            type=StatementType.ASSUMPTION
        ),
        external_refs=None
    )
).model_dump_json())}
```

---

Пример 9:
Input: "Размещение земельных участков ОО в застройке определяется в соответствии с таблицей Д.1 СП 42.13330.2016."
Output:
```json
{escape_braces(Result(
    structured_requirement=StructuredRequirement(
        object_entity="земельный участок",
        subject_entity="ОО",
        relation="имеет",
        applicability=[
            Applicability(
                entity=Entity.OBJECT,
                property="расположение",
                operator=Operator.EQUAL,
                value="в застройке"
            )
        ],
        statement=Statement(
            property="размещение",
            operator=Operator.EQUAL,
            value="по таблице Д.1 СП 42.13330.2016",
            type=StatementType.REQUIREMENT
        ),
        external_refs=[
            ExternalReference(
                document="СП 42.13330.2016",
                domain=None
            )
        ]
    )
).model_dump_json())}
```

---

Пример 10:
Input: "Оптимальная форма площади – с соотношением сторон не более 1:2."
Output:
```json
{escape_braces(Result(structured_requirement=None).model_dump_json())}
```
"""

STRUCTURED_REQ_USER_PROMPT = """
Требование:
{requirement_text}

Локальный контекст:
{local_context}

Глобальный контекст:
{global_context}

Верни только JSON.
""".strip()


class RequirementsStructurator(BaseOpenAIClient):
    DEFAULT_REQUEST_TIMEOUT = 3600.0

    TEMPERATURE = 0.1
    FREQUENCY_PENALTY = 0.02
    TOP_P = 0.95
    # MAX_TOKENS = 15000 # GPT

    MAX_TOKENS = 8000  # Qwen

    def __init__(
            self,
            openai_base_url: str,
            openai_api_token: str,
            model_name: str = "Qwen2.5-72b-64k",
            min_request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
            max_concurrent_requests: int = BaseOpenAIClient.DEFAULT_CONCURRENCY,
            **kwargs,
    ):
        super().__init__(
            openai_base_url=openai_base_url,
            openai_api_token=openai_api_token,
            model_name=model_name,
            result_model=Result,
            min_request_timeout=min_request_timeout,
            max_concurrent_requests=max_concurrent_requests,
        )

    @property
    def pbar_description(self) -> str:
        return "Structuring requirements"

    @property
    def pbar_unit(self) -> str:
        return "requirement"

    def formalize_requirements(
            self,
            requirements: Sequence[ContextualBaseRequirement],
    ) -> list[GraphRequirement]:
        if not requirements:
            return []

        responses: list[Union[Result, NoneResult]] = self.make_requests(requirements)

        graph_requirements: list[GraphRequirement] = []

        for req, response in zip(requirements, responses):
            if not response:
                logger.warning(
                    "[Warning] LLM failed on requirement formalization for text:\n%s.\n",
                    req.req_text,
                )
                continue

            if not response.structured_requirement:
                logger.warning(
                    "[Warning] LLM decided that the following text cannot be formalized:\n%s.\n",
                    req.req_text,
                )
                continue

            structured_req = response.structured_requirement

            graph_requirement = GraphRequirement(
                req_text=req.req_text,
                object_entity=structured_req.object_entity,
                subject_entity=structured_req.subject_entity,
                relation=structured_req.relation,
                applicability=structured_req.applicability
                if structured_req.applicability is not None
                else [],
                statement=structured_req.statement,
                external_refs=structured_req.external_refs
                if structured_req.external_refs is not None
                else [],
                source=req.source,
            )

            graph_requirements.append(graph_requirement)

        return graph_requirements

    async def _build_messages(
            self,
            requirement: ContextualBaseRequirement,
    ) -> list[ChatCompletionMessageParam]:
        sys_prompt = STRUCTURED_REQ_SYS_PROMPT.format(
            output_json_schema=self.output_json_schema
        )

        user_prompt = STRUCTURED_REQ_USER_PROMPT.format(
            requirement_text=requirement.req_text,
            local_context=getattr(requirement, "local_context", None) or "Локальный контекст отсутствует.",
            global_context=getattr(requirement, "global_context", None) or "Глобальный контекст отсутствует.",
        )

        return [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ]


def load_requirements_from_extracted_json(
        input_json_path: str | Path,
) -> list[ContextualBaseRequirement]:
    input_json_path = Path(input_json_path)

    with input_json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    requirements: list[ContextualBaseRequirement] = []

    for item_index, item in enumerate(data):
        paragraph = item.get("paragraph")
        reqs = item.get("reqs")

        if not reqs:
            continue

        for req_index, req in enumerate(reqs):
            req_text = req.get("text")

            if not req_text:
                continue

            req_text = req_text.strip()

            if not req_text:
                continue

            dependence = req.get("dependence") or {}
            requirements.append(
                ContextualBaseRequirement(
                    req_text=req_text,
                    local_context=dependence.get("local"),
                    global_context=dependence.get("global"),
                    source={
                        "document": "СП 251.1325800.2016",
                        "chapter": paragraph or "",
                        "paragraph": paragraph or "",
                    },
                )
            )

    return requirements


def save_graph_requirements_to_json(
        graph_requirements: Sequence[GraphRequirement],
        output_json_path: str | Path,
) -> None:
    output_json_path = Path(output_json_path)
    output_json_path.parent.mkdir(parents=True, exist_ok=True)

    with output_json_path.open("w", encoding="utf-8") as f:
        json.dump(
            [
                req.model_dump(mode="json")
                for req in graph_requirements
            ],
            f,
            ensure_ascii=False,
            indent=2,
        )

    logger.info(
        "Saved %s graph requirements to %s",
        len(graph_requirements),
        output_json_path,
    )


def main():
    OPENAI_BASE_URL = "xxx"
    OPENAI_API_TOKEN = "xxx"
    MODEL_NAME = "xxx"


    input_json_path = Path("itmo-practice-spring-2026/agent_experiment_req/requirements_selected_agent_qwen_v2.json")
    output_json_path = Path("applicability_agent_qwen.json")

    requirements = load_requirements_from_extracted_json(
        input_json_path=input_json_path,
    )

    logger.info(
        "Loaded requirements for formalization: %s",
        len(requirements),
    )

    structurator = RequirementsStructurator(
        openai_base_url=OPENAI_BASE_URL,
        openai_api_token=OPENAI_API_TOKEN,
        model_name=MODEL_NAME,
        min_request_timeout=3600.0,
        max_concurrent_requests=8,
    )

    graph_requirements = structurator.formalize_requirements(
        requirements=requirements,
    )

    logger.info(
        "Formalized requirements: %s",
        len(graph_requirements),
    )

    save_graph_requirements_to_json(
        graph_requirements=graph_requirements,
        output_json_path=output_json_path,
    )


if __name__ == "__main__":
    main()