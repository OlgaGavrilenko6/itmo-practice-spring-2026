import logging
from pathlib import Path

from docx_utils import (
    DEFAULT_INVALID_HEADINGS,
    build_global_contexts_from_docx,
    extract_requirement_fragments_from_docx,
)
from extractor import RequirementExtractor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    BASE_URL = "xxx"
    API_KEY = "xxx"
    MODEL_NAME = "xxx"

    input_docx = Path(
        "СП_251.1325800.2016_с_И1_И2_И3_И4_И5.docx"
    )
    output_json = Path("requirements_selected_fragments_with_global_context_save_graph.json")
    checkpoint_json = Path("requirements_selected_fragments_checkpoint_save.json")
    per_requirement_checkpoint_json = Path("current_fragment_requirement_checkpoint_save.json")

    logger.info("Извлечение глобального контекста из docx")
    scope_text, terms_text = build_global_contexts_from_docx(input_docx)

    logger.info(f"Scope context found: {'yes' if scope_text else 'no'}")
    logger.info(f"Terms context found: {'yes' if terms_text else 'no'}")

    if not scope_text:
        logger.warning('Не удалось извлечь главу "1 Область применения"')
    if not terms_text:
        logger.warning('Не удалось извлечь главу "3 Термины, определения и сокращения"')

    logger.info("Парсинг рабочих фрагментов из docx по аналогии с lllm_entity_experiment")
    selected_fragments = extract_requirement_fragments_from_docx(
        file_path=input_docx,
        invalid_headings=DEFAULT_INVALID_HEADINGS,
        extract_tables=False,
    )
    logger.info("Подготовлено фрагментов для извлечения: %s", len(selected_fragments))

    if not selected_fragments:
        raise RuntimeError("Не удалось получить ни одного рабочего фрагмента из документа")

    extractor = RequirementExtractor(
        openai_base_url=BASE_URL,
        openai_api_token=API_KEY,
        model_name=MODEL_NAME,
        scope_text=scope_text,
        terms_text=terms_text,
        max_repair_attempts=2,
    )

    logger.info("Запуск извлечения требований по выбранным фрагментам")
    data = extractor.extract_and_save(
        fragments=selected_fragments,
        output_path=output_json,
        checkpoint_path=checkpoint_json,
        per_requirement_checkpoint_path=per_requirement_checkpoint_json,
    )

    logger.info(f"Extracted records: {len(data)}")


if __name__ == "__main__":
    main()