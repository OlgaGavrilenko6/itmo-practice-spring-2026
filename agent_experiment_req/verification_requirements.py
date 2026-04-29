import json
import logging
from typing import List, Dict
from pathlib import Path
from datetime import datetime
from langchain_community.llms.vllm import VLLMOpenAI

# Логирование
LOG_FILE = "llm_check_requirements.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8")
    ]
)
logger = logging.getLogger("req_checker")

# Подключение к LLM
model_kwargs = {
    "temperature": 0.01,
    "top_p": 0.95,
    "max_tokens": 1024,
    "openai_api_key": "token-abc123",
    "openai_api_base": "http://d.dgx:8031/v1"
}
model = "/model"
llm = VLLMOpenAI(model=model, streaming=False, **model_kwargs)

# Критерии
CRITERIA = {
    1: "Требование должно содержать конкретный объект или элемент проверки — явно указанную сущность (помещение, конструкцию, оборудование, систему, участок и т.п.).",
    2: "Требование должно содержать формализуемое условие вида 'свойство – оператор – значение' (например, 'ширина не менее 1,5 м', 'высота не менее 0,9 м', 'из негорючих материалов').",
    3: "Требование должно быть выражено в императивной форме ('должны', 'не допускается', 'следует') и не содержать слов неопределённости ('может', 'желательно', 'при необходимости' и т.п.).",
    4: "Требование не должно зависеть от внешних расчётов, заданий или ссылок на другие документы, за исключением упоминания классификаций или стандартов (например, 'по ГОСТ 30244').",
    5: "Требование может включать уточнения или ссылки на отдельные пункты или значения, если они не препятствуют прямой формализации условия.",
    6: "Требование должно быть однозначно сопоставимо с конкретными типами объектов (например, жилые здания, кладовые, балконы, террасы, лифты, мусоропроводы и т.п.).",
    7: "Требование должно содержать нормируемые или бинарные показатели, подлежащие проверке ('не менее', 'не допускается', 'из негорючих материалов').",
    8: "Требование должно иметь явно заданный контекст применения — тип здания, помещение, конструктивный элемент, высоту, расположение и т.п.",
    9: "Требование должно быть атомарным — описывать одно проверяемое условие без необходимости дополнительного разложения на части.",
}


# Проверка одного требования через LLM
def check_requirement(requirement: str) -> Dict:
    """
    Проверяет одно требование по критериям через LLM.
    Возвращает результат в виде словаря.
    """

    prompt = f"""
**Role**
Ты — эксперт Минстроя РФ с 20-летним стажем, специализирующийся на формулировке и проверке требований нормативно-технических документов в строительной сфере.

**Task**
Твоя задача — проанализировать приведённое нормативное требование и оценить его соответствие девяти установленным критериям. На основе результатов проверки необходимо определить, является ли требование формализуемым (formalizable = true/false).

**Evaluation Logic**
1. Для каждого из 9 критериев укажи, прошло ли требование проверку:
   * Возможные значения: "прошло" или "не прошло".
2. Если требование прошло не менее 7 критериев, тогда formalizable = true, иначе formalizable = false.

**Input**
Требование:
{requirement}

Критерии:
1. {CRITERIA[1]}
2. {CRITERIA[2]}
3. {CRITERIA[3]}
4. {CRITERIA[4]}
5. {CRITERIA[5]}
6. {CRITERIA[6]}
7. {CRITERIA[7]}
8. {CRITERIA[8]}
9. {CRITERIA[9]}

**Output Format**
Верни результат строго в следующем формате:
```text
требование: <текст требования>

критерий 1: <прошло/не прошло>
критерий 2: <прошло/не прошло>
...
критерий 9: <прошло/не прошло>

formalizable: <true/false>

**Rules**

1) Не добавляй пояснений, комментариев, рассуждений или промежуточных выводов — только требуемый формат.
2) Формулировки "прошло" и "не прошло" пиши без кавычек и строго в нижнем регистре.
3) Значение formalizable должно быть булевым без кавычек: true или false.

**Task Examples**
Пример 1:
Input: "При двухрядном расположении лифтов ширина лифтового холла должна быть не менее 1,8 м при установке лифтов с глубиной кабины менее 2100 мм."
Output: 
  {{
    "requirement": "При двухрядном расположении лифтов ширина лифтового холла должна быть не менее 1,8 м при установке лифтов с глубиной кабины менее 2100 мм.",
    "criterion_1": "прошло",
    "criterion_2": "прошло",
    "criterion_3": "прошло",
    "criterion_4": "прошло",
    "criterion_5": "прошло",
    "criterion_6": "прошло",
    "criterion_7": "прошло",
    "criterion_8": "прошло",
    "criterion_9": "прошло",
    "formalizable": true,
    "paragraph": "6.2.3.18"
  }}

---

Пример 2:
Input: "При устройстве аварийных выходов из мансардных этажей на кровлю необходимо предусматривать площадки и переходные мостики, ведущие к лестницам П2 (по СП 1.13130, ГОСТ Р 53254)."
Output: 
  {{
    "requirement": "При устройстве аварийных выходов из мансардных этажей на кровлю необходимо предусматривать площадки и переходные мостики, ведущие к лестницам П2 (по СП 1.13130, ГОСТ Р 53254).",
    "criterion_1": "прошло",
    "criterion_2": "прошло",
    "criterion_3": "прошло",
    "criterion_4": "не прошло",
    "criterion_5": "не прошло",
    "criterion_6": "прошло",
    "criterion_7": "не прошло",
    "criterion_8": "прошло",
    "criterion_9": "прошло",
    "formalizable": false,
    "paragraph": "6.2.2.13"
  }}

---

Пример 3: 
Input: "При проектировании односторонне ориентированных квартир на территориях климатических районов строительства III и IV по СП 131.13330, сквозное или угловое проветривание помещений допускается выполнять через лестничную клетку."
Output: 
  {{
    "requirement": "При проектировании односторонне ориентированных квартир на территориях климатических районов строительства III и IV по СП 131.13330, сквозное или угловое проветривание помещений допускается выполнять через лестничную клетку.",
    "criterion_1": "прошло",
    "criterion_2": "прошло",
    "criterion_3": "прошло",
    "criterion_4": "прошло",
    "criterion_5": "прошло",
    "criterion_6": "прошло",
    "criterion_7": "прошло",
    "criterion_8": "прошло",
    "criterion_9": "прошло",
    "formalizable": true,
    "paragraph": "9.10"
  }}
  ---

Пример 4: 
Input: "Для размещения в многоквартирном жилом здании мусоросборной камеры, которая предназначена для временного хранения (сбора) ТКО без мусоропровода, следует применять планировочные приемы и (или) инженерно-технические средства, обеспечивающие контроль зоны входа в нее по СП 134.13330."
Output: 
  {{
    "requirement": "Для размещения в многоквартирном жилом здании мусоросборной камеры, которая предназначена для временного хранения (сбора) ТКО без мусоропровода, следует применять планировочные приемы и (или) инженерно-технические средства, обеспечивающие контроль зоны входа в нее по СП 134.13330.",
    "criterion_1": "прошло",
    "criterion_2": "не прошло",
    "criterion_3": "не прошло",
    "criterion_4": "не прошло",
    "criterion_5": "не прошло",
    "criterion_6": "прошло",
    "criterion_7": "не прошло",
    "criterion_8": "прошло",
    "criterion_9": "прошло",
    "formalizable": false,
    "paragraph": "7.33"
  }}
"""

    try:
        response = llm.invoke(prompt)
        text = response.lower()
        result = {
            "requirement": requirement,
            "criterion_1": "прошло" if "критерий 1: прошло" in text else "не прошло",
            "criterion_2": "прошло" if "критерий 2: прошло" in text else "не прошло",
            "criterion_3": "прошло" if "критерий 3: прошло" in text else "не прошло",
            "criterion_4": "прошло" if "критерий 4: прошло" in text else "не прошло",
            "criterion_5": "прошло" if "критерий 5: прошло" in text else "не прошло",
            "criterion_6": "прошло" if "критерий 6: прошло" in text else "не прошло",
            "criterion_7": "прошло" if "критерий 7: прошло" in text else "не прошло",
            "criterion_8": "прошло" if "критерий 8: прошло" in text else "не прошло",
            "criterion_9": "прошло" if "критерий 9: прошло" in text else "не прошло",
            "formalizable": "formalizable: true" in text,
            "raw_response": response.strip()
        }
    except Exception as e:
        logger.error(f"Ошибка при обработке требования: {requirement[:80]}... → {e}")
        result = {
            "requirement": requirement,
            **{f"criterion_{i}": "ошибка" for i in range(1, 10)},
            "formalizable": False,
            "raw_response": str(e)
        }

    return result


def evaluate_requirements(requirements: List[Dict], output_json: Path) -> Dict:
    """
    Проверяет список требований и сохраняет результаты.
    Если файл с результатами уже существует, продолжает с непросчитанных.
    """
    results = []
    total = len(requirements)
    # timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_path = output_json.parent / f"llm_results_СП54_verification_new_criterias.json"

    # Проверяем, есть ли промежуточные результаты
    progress_file = output_json.parent / "llm_results_progress.json"
    if progress_file.exists():
        logger.info(f"Обнаружен файл прогресса {progress_file}, восстанавливаем результаты...")
        with progress_file.open("r", encoding="utf-8") as f:
            results = json.load(f)
        processed_requirements = {r.get("requirement", "").strip() for r in results if "requirement" in r}
    else:
        processed_requirements = set()

    logger.info(f"Уже обработано требований: {len(processed_requirements)} / {total}")

    remaining_requirements = [
        r for r in requirements
        if r.get("text", "").strip() not in processed_requirements
    ]

    for i, req in enumerate(remaining_requirements, start=1):
        text = req.get("text", "")
        paragraph = req.get("paragraph", "")
        logger.info(f"[{len(results) + 1}/{total}] Проверка (параграф {paragraph}): {text[:80]}...")

        try:
            result = check_requirement(text)
            result["paragraph"] = paragraph
            results.append(result)

            # сохраняем после каждого требования
            with progress_file.open("w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)

            logger.info(f" Промежуточно сохранено ({len(results)}/{total})")

        except Exception as e:
            logger.error(f"Ошибка при проверке требования: {text[:80]}... → {e}")

    # Сохраняем
    with progress_file.open("w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # Метрики
    checkable = sum(r.get("formalizable", False) for r in results)
    uncheckable = total - checkable

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    logger.info(f" Результаты сохранены в {output_path}")
    logger.info(f"Всего требований: {total}")
    logger.info(f"Проверяемых (formalizable=true): {checkable}")
    logger.info(f"Непроверяемых (formalizable=false): {uncheckable}")
    logger.info(f" Промежуточный файл прогресса: {progress_file}")

    return {"last_results": results, "checkable": checkable, "uncheckable": uncheckable}

def main():
    input_path = Path(
        "/Users/olgagavrilenko/Downloads/mcp-ontology-feature-pipeline/ontology/article/matched_reqs_СП54.json")
    if not input_path.exists():
        raise FileNotFoundError(f"Файл {input_path} не найден")

    with input_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    all_requirements = []
    for item in data:
        paragraph = item.get("paragraph", "")
        reqs_list = item.get("reqs", [])
        for req_text in reqs_list:
            if isinstance(req_text, str) and req_text.strip():
                all_requirements.append({
                    "text": req_text.strip(),
                    "paragraph": paragraph
                })

    logger.info(f"Всего найдено требований для проверки: {len(all_requirements)}")

    if not all_requirements:
        logger.warning("Не найдено ни одного требования в JSON — проверь структуру файла (ключ 'reqs')")
        return

    # Запуск проверки с возможностью продолжения
    evaluate_requirements(all_requirements, input_path)


if __name__ == "__main__":
    main()