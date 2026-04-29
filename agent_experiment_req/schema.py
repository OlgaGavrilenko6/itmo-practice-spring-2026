from typing import Literal, Optional
from pydantic import BaseModel, Field


class LLMRequirementText(BaseModel):
    req_text: str = Field(description="Текст элементарного требования")


class LLMExtractedRequirements(BaseModel):
    reqs: Optional[list[LLMRequirementText]] = Field(
        default=None,
        description="Список элементарных требований без контекстов",
    )


class LocalContextResult(BaseModel):
    local_relevant_context: Optional[str] = Field(
        default=None,
        description="Локальный контекст для требования",
    )


class GlobalContextResult(BaseModel):
    global_relevant_context: Optional[str] = Field(
        default=None,
        description="Глобальный контекст для требования",
    )


class LLMAtomicRequirement(BaseModel):
    req_text: str = Field(description="Текст элементарного требования")
    local_relevant_context: Optional[str] = Field(
        default=None,
        description="Локальный контекст требования",
    )
    global_relevant_context: Optional[str] = Field(
        default=None,
        description="Глобальный контекст требования",
    )


class RequirementDependence(BaseModel):
    global_: Optional[str] = Field(
        default=None,
        alias="global",
        description="Релевантный глобальный контекст требования",
    )
    local: Optional[str] = Field(
        default=None,
        description="Релевантный локальный контекст требования",
    )

    model_config = {
        "populate_by_name": True,
    }


class Requirement(BaseModel):
    text: str = Field(description="Текст элементарного требования")
    dependence: RequirementDependence = Field(
        description="Релевантный глобальный и локальный контекст требования"
    )


class FinalExtractedRequirements(BaseModel):
    paragraph: str = Field(description="Полный выбранный параграф")
    reqs: Optional[list[Requirement]] = Field(default=None)


class RequirementExtractionInput(BaseModel):
    paragraph_text: str = Field(description="Полный текст выбранного параграфа")


class RequirementCritiqueIssue(BaseModel):
    code: str = Field(description="Код нарушения")
    target: Literal["local_relevant_context", "global_relevant_context"] = Field(
        description="Поле, к которому относится замечание"
    )
    severity: Literal["major", "minor"] = Field(
        description="Критичность замечания"
    )
    comment: str = Field(description="Подробный комментарий критика")


class RequirementCritique(BaseModel):
    approved: bool = Field(
        description="Одобрена ли пара контекстов для данного требования"
    )
    issues: list[RequirementCritiqueIssue] = Field(
        default_factory=list,
        description="Список замечаний критика"
    )
    summary: Optional[str] = Field(
        default=None,
        description="Краткая сводка по результату проверки"
    )


class RequirementRepairResult(BaseModel):
    local_relevant_context: Optional[str] = Field(
        default=None,
        description="Исправленный локальный контекст",
    )
    global_relevant_context: Optional[str] = Field(
        default=None,
        description="Исправленный глобальный контекст",
    )
    repair_notes: Optional[str] = Field(
        default=None,
        description="Краткое описание внесенных исправлений",
    )