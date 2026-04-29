import json
import logging
from pathlib import Path
from typing import Any, Literal, Optional, TypedDict
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, Send

from prompts import (
    CRITIC_SYS_PROMPT,
    CRITIC_USER_PROMPT,
    GLOBAL_CONTEXT_SYS_PROMPT,
    GLOBAL_CONTEXT_USER_PROMPT,
    LOCAL_CONTEXT_SYS_PROMPT,
    LOCAL_CONTEXT_USER_PROMPT,
    REPAIR_SYS_PROMPT,
    REPAIR_USER_PROMPT,
    REQ_SYS_PROMPT,
    REQ_USER_PROMPT,
)
from schema import (
    GlobalContextResult,
    LLMAtomicRequirement,
    LLMExtractedRequirements,
    LLMRequirementText,
    LocalContextResult,
    Requirement,
    RequirementCritique,
    RequirementDependence,
    RequirementRepairResult,
)

logger = logging.getLogger(__name__)


class GraphRuntimeContext(TypedDict):
    extractor_chain: Any
    local_context_chain: Any
    global_context_chain: Any
    critic_chain: Any
    repair_chain: Any
    global_context: str
    per_requirement_checkpoint_path: str | None


class ParagraphGraphState(TypedDict, total=False):
    paragraph_text: str
    extracted_paragraph: str | None

    # Только тексты требований после extractor
    raw_requirements: list[LLMRequirementText]

    # Результаты двух параллельных батчевых веток
    local_contexts: list[Optional[str]]
    global_contexts: list[Optional[str]]

    # Собранные кандидаты req_text + local + global
    candidate_requirements: list[LLMAtomicRequirement]

    # Critic и repair
    critiques: list[RequirementCritique]
    repair_round: int
    max_repair_attempts: int

    # Финал
    final_requirements: list[Requirement]


def _save_per_requirement_checkpoint(
    checkpoint_path: str | None,
    paragraph_text: str,
    current_requirement_index: int,
    accepted_requirements: list[Requirement],
) -> None:
    if not checkpoint_path:
        return

    payload = {
        "paragraph_text": paragraph_text,
        "current_requirement_index": current_requirement_index,
        "accepted_requirements": [
            req.model_dump(by_alias=True)
            for req in accepted_requirements
        ],
    }

    path = Path(checkpoint_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Idx=%s сохранен в файл %s (принято требований: %s)",
        current_requirement_index,
        path,
        len(accepted_requirements),
    )
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _clean_optional(value: str | None) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def build_extractor_chain(llm):
    return (
        ChatPromptTemplate.from_messages([
            ("system", REQ_SYS_PROMPT),
            ("user", REQ_USER_PROMPT),
        ])
        | llm.with_structured_output(LLMExtractedRequirements)
    )


def build_local_context_chain(llm):
    return (
        ChatPromptTemplate.from_messages([
            ("system", LOCAL_CONTEXT_SYS_PROMPT),
            ("user", LOCAL_CONTEXT_USER_PROMPT),
        ])
        | llm.with_structured_output(LocalContextResult)
    )


def build_global_context_chain(llm):
    return (
        ChatPromptTemplate.from_messages([
            ("system", GLOBAL_CONTEXT_SYS_PROMPT),
            ("user", GLOBAL_CONTEXT_USER_PROMPT),
        ])
        | llm.with_structured_output(GlobalContextResult)
    )


def build_critic_chain(llm):
    return (
        ChatPromptTemplate.from_messages([
            ("system", CRITIC_SYS_PROMPT),
            ("user", CRITIC_USER_PROMPT),
        ])
        | llm.with_structured_output(RequirementCritique)
    )


def build_repair_chain(llm):
    return (
        ChatPromptTemplate.from_messages([
            ("system", REPAIR_SYS_PROMPT),
            ("user", REPAIR_USER_PROMPT),
        ])
        | llm.with_structured_output(RequirementRepairResult)
    )


def extract_requirements_from_paragraph(
    state: ParagraphGraphState,
    runtime: Runtime[GraphRuntimeContext],
):
    response: LLMExtractedRequirements = runtime.context["extractor_chain"].invoke({
        "paragraph_text": state["paragraph_text"],
    })
    reqs = response.reqs or []
    logger.info("Extractor returned %s raw requirements", len(reqs))

    return {
        "extracted_paragraph": state["paragraph_text"],
        "raw_requirements": reqs,
        "local_contexts": [],
        "global_contexts": [],
        "candidate_requirements": [],
        "critiques": [],
        "repair_round": 0,
        "final_requirements": [],
    }


def route_after_extraction(
    state: ParagraphGraphState,
    runtime: Runtime[GraphRuntimeContext],
) -> Command[Literal["dispatch_context_batches", END]]:
    raw_requirements = state.get("raw_requirements", [])
    if not raw_requirements:
        return Command(goto=END)
    return Command(goto="dispatch_context_batches")


def dispatch_context_batches(
    state: ParagraphGraphState,
    runtime: Runtime[GraphRuntimeContext],
):
    # Пустая нода: реальная маршрутизация через add_conditional_edges + Send
    return {}


def send_context_batch_jobs(state: ParagraphGraphState):
    raw_requirements = state.get("raw_requirements", [])
    if not raw_requirements:
        return []

    return [
        Send(
            "build_local_contexts_batch",
            {
                "paragraph_text": state["paragraph_text"],
                "raw_requirements": raw_requirements,
            },
        ),
        Send(
            "build_global_contexts_batch",
            {
                "raw_requirements": raw_requirements,
            },
        ),
    ]


def build_local_contexts_batch(
    state: ParagraphGraphState,
    runtime: Runtime[GraphRuntimeContext],
):
    raw_requirements = state.get("raw_requirements", [])
    paragraph_text = state["paragraph_text"]

    payloads = [
        {
            "req_text": req.req_text,
            "paragraph_text": paragraph_text,
        }
        for req in raw_requirements
    ]

    logger.info("Local-context batch started for %s requirements", len(payloads))
    responses: list[LocalContextResult] = runtime.context["local_context_chain"].batch(payloads)
    logger.info("Local-context batch completed")

    return {
        "local_contexts": [
            _clean_optional(resp.local_relevant_context)
            for resp in responses
        ]
    }


def build_global_contexts_batch(
    state: ParagraphGraphState,
    runtime: Runtime[GraphRuntimeContext],
):
    raw_requirements = state.get("raw_requirements", [])
    global_context = runtime.context["global_context"] or "Глобальный контекст отсутствует."

    payloads = [
        {
            "req_text": req.req_text,
            "global_context": global_context,
        }
        for req in raw_requirements
    ]

    logger.info("Global-context batch started for %s requirements", len(payloads))
    responses: list[GlobalContextResult] = runtime.context["global_context_chain"].batch(payloads)
    logger.info("Global-context batch completed")

    return {
        "global_contexts": [
            _clean_optional(resp.global_relevant_context)
            for resp in responses
        ]
    }


def merge_contexts_into_requirements(
    state: ParagraphGraphState,
    runtime: Runtime[GraphRuntimeContext],
):
    raw_requirements = state.get("raw_requirements", [])
    local_contexts = state.get("local_contexts", [])
    global_contexts = state.get("global_contexts", [])

    if len(local_contexts) != len(raw_requirements):
        raise ValueError(
            f"local_contexts count mismatch: {len(local_contexts)} != {len(raw_requirements)}"
        )
    if len(global_contexts) != len(raw_requirements):
        raise ValueError(
            f"global_contexts count mismatch: {len(global_contexts)} != {len(raw_requirements)}"
        )

    candidate_requirements: list[LLMAtomicRequirement] = []
    for idx, req in enumerate(raw_requirements):
        candidate_requirements.append(
            LLMAtomicRequirement(
                req_text=_clean_optional(req.req_text) or "",
                local_relevant_context=local_contexts[idx],
                global_relevant_context=global_contexts[idx],
            )
        )

    return {
        "candidate_requirements": candidate_requirements,
    }


def critique_requirements_batch(
    state: ParagraphGraphState,
    runtime: Runtime[GraphRuntimeContext],
):
    candidate_requirements = list(state.get("candidate_requirements", []))
    if not candidate_requirements:
        return {"critiques": []}

    paragraph_text = state["paragraph_text"]
    global_context = runtime.context["global_context"] or "Глобальный контекст отсутствует."

    payloads = [
        {
            "req_text": req.req_text,
            "local_relevant_context": req.local_relevant_context or "null",
            "global_relevant_context": req.global_relevant_context or "null",
            "paragraph_text": paragraph_text,
            "global_context": global_context,
        }
        for req in candidate_requirements
    ]

    logger.info("Critic batch started for %s requirements", len(payloads))
    critiques: list[RequirementCritique] = runtime.context["critic_chain"].batch(payloads)
    logger.info("Critic batch completed")

    return {
        "critiques": critiques,
    }


def _maybe_apply_deterministic_repair(
    req: LLMAtomicRequirement,
    critique: RequirementCritique,
) -> LLMAtomicRequirement | None:
    issue_codes = [issue.code for issue in critique.issues]
    issue_comments_text = " ".join(issue.comment for issue in critique.issues).lower()

    if not issue_codes:
        return None

    allowed_codes = {
        "local_context_redundant",
        "local_context_not_source_faithful",
        "global_context_redundant",
        "global_context_missing",
    }
    if not all(code in allowed_codes for code in issue_codes):
        return None

    new_local = req.local_relevant_context
    new_global = req.global_relevant_context
    changed = False

    if "local_context_redundant" in issue_codes:
        new_local = None
        changed = True

    if (
        "local_context_not_source_faithful" in issue_codes
        and "local_context_redundant" in issue_codes
    ):
        new_local = None
        changed = True

    if "global_context_redundant" in issue_codes:
        new_global = None
        changed = True

    if "global_context_missing" in issue_codes and "null допустимо" in issue_comments_text:
        new_global = None
        changed = True

    if not changed:
        return None

    return LLMAtomicRequirement(
        req_text=_clean_optional(req.req_text) or "",
        local_relevant_context=_clean_optional(new_local),
        global_relevant_context=_clean_optional(new_global),
    )


def route_after_critique_batch(
    state: ParagraphGraphState,
    runtime: Runtime[GraphRuntimeContext],
) -> Command[Literal["repair_requirements_batch", "finalize_requirements"]]:
    critiques = state.get("critiques", [])
    if not critiques:
        return Command(goto="finalize_requirements")

    has_rejected = any(not critique.approved for critique in critiques)
    if not has_rejected:
        return Command(goto="finalize_requirements")

    repair_round = state.get("repair_round", 0)
    max_repair_attempts = state.get("max_repair_attempts", 1)
    if repair_round >= max_repair_attempts:
        return Command(goto="finalize_requirements")

    return Command(goto="repair_requirements_batch")


def repair_requirements_batch(
    state: ParagraphGraphState,
    runtime: Runtime[GraphRuntimeContext],
):
    candidate_requirements = list(state.get("candidate_requirements", []))
    critiques = list(state.get("critiques", []))
    paragraph_text = state["paragraph_text"]
    global_context = runtime.context["global_context"] or "Глобальный контекст отсутствует."

    repaired_requirements = list(candidate_requirements)
    repair_indices: list[int] = []
    repair_payloads: list[dict[str, str]] = []

    for idx, (req, critique) in enumerate(zip(candidate_requirements, critiques)):
        if critique.approved:
            continue

        deterministic = _maybe_apply_deterministic_repair(req, critique)
        if deterministic is not None:
            repaired_requirements[idx] = deterministic
            continue

        repair_comments = "\n".join(
            f"- {issue.code}: {issue.comment}"
            for issue in critique.issues
        )

        repair_indices.append(idx)
        repair_payloads.append(
            {
                "req_text": req.req_text,
                "local_relevant_context": req.local_relevant_context or "null",
                "global_relevant_context": req.global_relevant_context or "null",
                "paragraph_text": paragraph_text[:2000],
                "global_context": global_context[:2000],
                "critique_json": repair_comments[:2000],
            }
        )

    if repair_payloads:
        logger.info("Repair batch started for %s requirements", len(repair_payloads))
        responses: list[RequirementRepairResult] = runtime.context["repair_chain"].batch(repair_payloads)
        logger.info("Repair batch completed")

        for idx, response in zip(repair_indices, responses):
            repaired_requirements[idx] = LLMAtomicRequirement(
                req_text=candidate_requirements[idx].req_text,
                local_relevant_context=_clean_optional(response.local_relevant_context),
                global_relevant_context=_clean_optional(response.global_relevant_context),
            )

    return {
        "candidate_requirements": repaired_requirements,
        "repair_round": state.get("repair_round", 0) + 1,
    }


def _to_final_requirement(req: LLMAtomicRequirement) -> Requirement | None:
    req_text = _clean_optional(req.req_text)
    if not req_text:
        return None

    return Requirement(
        text=req_text,
        dependence=RequirementDependence(
            global_=_clean_optional(req.global_relevant_context),
            local=_clean_optional(req.local_relevant_context),
        ),
    )


def finalize_requirements(
    state: ParagraphGraphState,
    runtime: Runtime[GraphRuntimeContext],
):
    candidate_requirements = list(state.get("candidate_requirements", []))
    final_requirements: list[Requirement] = []

    for req in candidate_requirements:
        final_req = _to_final_requirement(req)
        if final_req is not None:
            final_requirements.append(final_req)

    _save_per_requirement_checkpoint(
        checkpoint_path=runtime.context.get("per_requirement_checkpoint_path"),
        paragraph_text=state["paragraph_text"],
        current_requirement_index=len(candidate_requirements),
        accepted_requirements=final_requirements,
    )

    return {
        "final_requirements": final_requirements,
    }


def build_paragraph_graph():
    graph_builder = StateGraph(
        ParagraphGraphState,
        input_schema=None,
        output_schema=None,
        context_schema=GraphRuntimeContext,
    )

    graph_builder.add_node("extract_requirements_from_paragraph", extract_requirements_from_paragraph)
    graph_builder.add_node("route_after_extraction", route_after_extraction)
    graph_builder.add_node("dispatch_context_batches", dispatch_context_batches)

    graph_builder.add_node("build_local_contexts_batch", build_local_contexts_batch)
    graph_builder.add_node("build_global_contexts_batch", build_global_contexts_batch)
    graph_builder.add_node("merge_contexts_into_requirements", merge_contexts_into_requirements)

    graph_builder.add_node("critique_requirements_batch", critique_requirements_batch)
    graph_builder.add_node("route_after_critique_batch", route_after_critique_batch)
    graph_builder.add_node("repair_requirements_batch", repair_requirements_batch)
    graph_builder.add_node("finalize_requirements", finalize_requirements)

    graph_builder.add_edge(START, "extract_requirements_from_paragraph")
    graph_builder.add_edge("extract_requirements_from_paragraph", "route_after_extraction")

    graph_builder.add_conditional_edges(
        "dispatch_context_batches",
        send_context_batch_jobs,
        ["build_local_contexts_batch", "build_global_contexts_batch"],
    )

    graph_builder.add_edge("build_local_contexts_batch", "merge_contexts_into_requirements")
    graph_builder.add_edge("build_global_contexts_batch", "merge_contexts_into_requirements")

    graph_builder.add_edge("merge_contexts_into_requirements", "critique_requirements_batch")
    graph_builder.add_edge("critique_requirements_batch", "route_after_critique_batch")
    graph_builder.add_edge("repair_requirements_batch", "critique_requirements_batch")
    graph_builder.add_edge("finalize_requirements", END)

    return graph_builder.compile()