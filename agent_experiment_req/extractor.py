import json
import logging
from pathlib import Path
from typing import Optional, Sequence, Union, Any

from langchain_openai import ChatOpenAI
from tqdm import tqdm

from docx_utils import _clean_optional_text, combine_global_context
from graph import (
    GraphRuntimeContext,
    build_critic_chain,
    build_extractor_chain,
    build_global_context_chain,
    build_local_context_chain,
    build_paragraph_graph,
    build_repair_chain,
)
from schema import FinalExtractedRequirements, RequirementExtractionInput, Requirement

logger = logging.getLogger(__name__)


class RequirementExtractor:
    TEMPERATURE = 0.7
    MAX_TOKENS = 80000
    TOP_P = 0.95

    def __init__(
        self,
        openai_base_url: str,
        openai_api_token: str,
        model_name: str,
        scope_text: Optional[str] = None,
        terms_text: Optional[str] = None,
        max_repair_attempts: int = 2,
    ) -> None:
        self.extractor_llm = ChatOpenAI(
            openai_api_base=openai_base_url,
            openai_api_key=openai_api_token,
            model_name=model_name,
            temperature=self.TEMPERATURE,
            max_tokens=self.MAX_TOKENS,
            top_p=self.TOP_P,
        )

        self.local_context_llm = ChatOpenAI(
            openai_api_base=openai_base_url,
            openai_api_key=openai_api_token,
            model_name=model_name,
            temperature=self.TEMPERATURE,
            max_tokens=self.MAX_TOKENS,
            top_p=self.TOP_P,
        )

        self.global_context_llm = ChatOpenAI(
            openai_api_base=openai_base_url,
            openai_api_key=openai_api_token,
            model_name=model_name,
            temperature=self.TEMPERATURE,
            max_tokens=self.MAX_TOKENS,
            top_p=self.TOP_P,
        )

        self.critic_llm = ChatOpenAI(
            openai_api_base=openai_base_url,
            openai_api_key=openai_api_token,
            model_name=model_name,
            temperature=self.TEMPERATURE,
            max_tokens=self.MAX_TOKENS,
            top_p=self.TOP_P,
        )

        self.repair_llm = ChatOpenAI(
            openai_api_base=openai_base_url,
            openai_api_key=openai_api_token,
            model_name=model_name,
            temperature=self.TEMPERATURE,
            max_tokens=self.MAX_TOKENS,
            top_p=self.TOP_P,
        )

        self.scope_text = scope_text
        self.terms_text = terms_text
        self.full_global_context = combine_global_context(scope_text, terms_text)

        logger.info("Global context length: %s", len(self.full_global_context or ""))
        logger.info(
            "Global context preview: %s",
            (self.full_global_context or "")[:500].replace("\n", " "),
        )

        self.max_repair_attempts = max_repair_attempts

        self.extractor_chain = build_extractor_chain(self.extractor_llm)
        self.local_context_chain = build_local_context_chain(self.local_context_llm)
        self.global_context_chain = build_global_context_chain(self.global_context_llm)
        self.critic_chain = build_critic_chain(self.critic_llm)
        self.repair_chain = build_repair_chain(self.repair_llm)
        self.graph = build_paragraph_graph()

    def build_inputs_from_fragments(
        self,
        fragments: Sequence[str],
    ) -> list[RequirementExtractionInput]:
        items: list[RequirementExtractionInput] = []

        for fragment in tqdm(fragments, desc="Подготовка выбранных фрагментов", unit="fragment"):
            items.append(
                RequirementExtractionInput(
                    paragraph_text=fragment,
                )
            )

        return items

    def extract_one(
        self,
        item: RequirementExtractionInput,
        per_requirement_checkpoint_path: Union[str, Path],
    ) -> Optional[FinalExtractedRequirements]:
        try:
            runtime_context: GraphRuntimeContext = {
                "extractor_chain": self.extractor_chain,
                "local_context_chain": self.local_context_chain,
                "global_context_chain": self.global_context_chain,
                "critic_chain": self.critic_chain,
                "repair_chain": self.repair_chain,
                "global_context": self.full_global_context,
                "per_requirement_checkpoint_path": str(per_requirement_checkpoint_path),
            }

            initial_state: dict[str, Any] = {
                "paragraph_text": item.paragraph_text,
                "max_repair_attempts": self.max_repair_attempts,
            }

            checkpoint_data = self._load_per_requirement_checkpoint(per_requirement_checkpoint_path)
            if checkpoint_data and checkpoint_data.get("paragraph_text") == item.paragraph_text:
                logger.info(
                    "Resuming current fragment from saved checkpoint"
                )

                if "raw_requirements" in checkpoint_data:
                    initial_state["raw_requirements"] = checkpoint_data.get("raw_requirements") or []

                if "accepted_requirements" in checkpoint_data:
                    initial_state["final_requirements"] = [
                        Requirement.model_validate(x)
                        for x in checkpoint_data.get("accepted_requirements", [])
                    ]

                if "current_requirement_index" in checkpoint_data:
                    initial_state["current_requirement_index"] = checkpoint_data.get(
                        "current_requirement_index",
                        0,
                    )

            result = self.graph.invoke(
                initial_state,
                context=runtime_context,
                config={"recursion_limit": 200},
            )

            if result is None:
                logger.warning("Graph returned None for current fragment")
                return None

            logger.info("Graph raw result type: %s", type(result).__name__)
            logger.info(
                "Graph raw result keys: %s",
                list(result.keys()) if isinstance(result, dict) else "not a dict",
            )

            if isinstance(result, FinalExtractedRequirements):
                return result

            if isinstance(result, dict) and "reqs" in result and "paragraph" in result:
                parsed = FinalExtractedRequirements.model_validate(result)
                return parsed

            final_requirements = result.get("final_requirements") or result.get("reqs") or []

            parsed = FinalExtractedRequirements(
                paragraph=item.paragraph_text,
                reqs=final_requirements or None,
            )
            return parsed

        except Exception as e:
            logger.warning(f"[Warning] Graph pipeline failed on fragment: {e}")
            return None

    @staticmethod
    def _fragment_key(fragment: str) -> str:
        return fragment.strip()

    @staticmethod
    def _load_checkpoint(checkpoint_path: Union[str, Path]) -> dict[str, Any]:
        checkpoint_path = Path(checkpoint_path)
        if not checkpoint_path.exists():
            return {
                "completed_fragment_keys": [],
                "records": [],
            }

        try:
            with checkpoint_path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                logger.warning("Checkpoint file has invalid format. Starting from scratch.")
                return {
                    "completed_fragment_keys": [],
                    "records": [],
                }

            completed = data.get("completed_fragment_keys", [])
            records = data.get("records", [])

            if not isinstance(completed, list):
                completed = []
            if not isinstance(records, list):
                records = []

            return {
                "completed_fragment_keys": completed,
                "records": records,
            }

        except Exception as e:
            logger.warning(f"Failed to load checkpoint: {e}. Starting from scratch.")
            return {
                "completed_fragment_keys": [],
                "records": [],
            }

    @staticmethod
    def _load_per_requirement_checkpoint(
        checkpoint_path: Union[str, Path],
    ) -> dict[str, Any] | None:
        checkpoint_path = Path(checkpoint_path)
        if not checkpoint_path.exists():
            return None

        try:
            with checkpoint_path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                return None

            return data
        except Exception as e:
            logger.warning(f"Failed to load per-requirement checkpoint: {e}")
            return None

    @staticmethod
    def _clear_per_requirement_checkpoint(
        checkpoint_path: Union[str, Path],
    ) -> None:
        checkpoint_path = Path(checkpoint_path)
        if checkpoint_path.exists():
            checkpoint_path.unlink()

    @staticmethod
    def _save_checkpoint(
        checkpoint_path: Union[str, Path],
        completed_fragment_keys: list[str],
        records: list[dict],
    ) -> None:
        checkpoint_path = Path(checkpoint_path)
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "completed_fragment_keys": completed_fragment_keys,
            "records": records,
        }

        with checkpoint_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def extract_requirement_records_from_fragments(
        self,
        fragments: Sequence[str],
        checkpoint_path: Union[str, Path],
        per_requirement_checkpoint_path: Union[str, Path],
        output_path: Union[str, Path],
    ) -> list[dict]:
        if not fragments:
            return []

        checkpoint = self._load_checkpoint(checkpoint_path)
        completed_fragment_keys: list[str] = checkpoint["completed_fragment_keys"]
        full_records: list[dict] = checkpoint["records"]

        completed_set = set(completed_fragment_keys)
        inputs = self.build_inputs_from_fragments(fragments)

        with tqdm(total=len(inputs), desc="Извлечение требований (Graph + LLM)", unit="fragment") as pbar:
            for idx, item in enumerate(inputs):
                fragment_key = self._fragment_key(item.paragraph_text)

                if fragment_key in completed_set:
                    logger.info(
                        "Пропускаю уже обработанный фрагмент %s/%s",
                        idx + 1,
                        len(inputs),
                    )
                    pbar.update(1)
                    continue

                logger.info(
                    "Начинаю обработку фрагмента %s/%s: %s",
                    idx + 1,
                    len(inputs),
                    item.paragraph_text[:120].replace("\n", " "),
                )

                response = self.extract_one(
                    item=item,
                    per_requirement_checkpoint_path=per_requirement_checkpoint_path,
                )

                checkpoint_data = self._load_per_requirement_checkpoint(per_requirement_checkpoint_path)

                paragraph_text = item.paragraph_text
                reqs_data = None

                if checkpoint_data and checkpoint_data.get("paragraph_text") == item.paragraph_text:
                    paragraph_text = checkpoint_data.get("paragraph_text") or item.paragraph_text
                    accepted_requirements = checkpoint_data.get("accepted_requirements") or []

                    reqs_with_context: list[dict] = []

                    for req in accepted_requirements:
                        try:
                            req_obj = Requirement.model_validate(req)
                        except Exception:
                            logger.warning("Не удалось провалидировать accepted_requirement: %r", req)
                            continue

                        if not req_obj.text or not req_obj.text.strip():
                            continue

                        reqs_with_context.append(
                            {
                                "text": req_obj.text.strip(),
                                "dependence": {
                                    "global": _clean_optional_text(req_obj.dependence.global_),
                                    "local": _clean_optional_text(req_obj.dependence.local),
                                },
                            }
                        )

                    reqs_data = reqs_with_context or None

                else:
                    if response and response.reqs:
                        reqs_with_context: list[dict] = []

                        for req in response.reqs:
                            if not req or not req.text or not req.text.strip():
                                continue

                            reqs_with_context.append(
                                {
                                    "text": req.text.strip(),
                                    "dependence": {
                                        "global": _clean_optional_text(req.dependence.global_),
                                        "local": _clean_optional_text(req.dependence.local),
                                    },
                                }
                            )

                        paragraph_text = response.paragraph or item.paragraph_text
                        reqs_data = reqs_with_context or None

                full_records.append(
                    {
                        "paragraph": paragraph_text,
                        "reqs": reqs_data,
                    }
                )

                completed_fragment_keys.append(fragment_key)
                completed_set.add(fragment_key)

                self._save_checkpoint(
                    checkpoint_path=checkpoint_path,
                    completed_fragment_keys=completed_fragment_keys,
                    records=full_records,
                )
                self.save_to_json(full_records, output_path)

                self._clear_per_requirement_checkpoint(per_requirement_checkpoint_path)

                logger.info(
                    "Завершена обработка фрагмента %s/%s, требований сохранено: %s",
                    idx + 1,
                    len(inputs),
                    len(reqs_data or []),
                )
                pbar.update(1)

        return full_records

    @staticmethod
    def save_to_json(
        data: Sequence[dict],
        output_path: Union[str, Path],
    ) -> None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with output_path.open("w", encoding="utf-8") as f:
            json.dump(list(data), f, ensure_ascii=False, indent=2)

        logger.info(f"Saved {len(data)} records to {output_path}")

    def extract_and_save(
        self,
        fragments: Sequence[str],
        output_path: Union[str, Path],
        checkpoint_path: Union[str, Path],
        per_requirement_checkpoint_path: Union[str, Path],
    ) -> list[dict]:
        data = self.extract_requirement_records_from_fragments(
            fragments=fragments,
            checkpoint_path=checkpoint_path,
            per_requirement_checkpoint_path=per_requirement_checkpoint_path,
            output_path=output_path,
        )
        return data