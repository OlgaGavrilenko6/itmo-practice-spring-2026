import json
import re
from collections import defaultdict
from typing import List, Dict, Any, Tuple, Optional
from ontology.taxonomy_clustering.embedding_filter import ServerEmbeddings
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity


# =============================
# Параметры
# =============================
TEXT_THRESHOLD = 0.85
GLOBAL_THRESHOLD = 0.80
LOCAL_THRESHOLD = 0.80

embedding_host="http://10.32.15.7:8082/embed"
batch_size = 8

expert_file = "/Users/olgagavrilenko/PycharmProjects/itmo-practice-2026/agent_experiment_req/requirement_gold_251.json"
model_file = "/Users/olgagavrilenko/PycharmProjects/itmo-practice-2026/agent_experiment_req/requirements_selected_fragments_with_global_context_save_graph_v14.json"


# =============================
# Эмбеддинги
# =============================
class EmbeddingSimilarity:
    def __init__(self, host: str, batch_size: int = 8):
        self.client = ServerEmbeddings(
            embedding_host=host,
            batch_size=batch_size
        )

    def encode(self, texts: List[str]) -> np.ndarray:
        if not texts:
            return np.array([])

        embeddings = self.client.get_embeddings(texts)
        if isinstance(embeddings, list):
            embeddings = np.array(embeddings)
        return embeddings

    def cosine(self, texts1: List[str], texts2: List[str]) -> np.ndarray:
        if not texts1 or not texts2:
            return np.array([])

        emb1 = self.encode(texts1)
        emb2 = self.encode(texts2)

        if emb1.size == 0 or emb2.size == 0:
            return np.array([])

        return cosine_similarity(emb1, emb2)


# =============================
# Нормализация и загрузка
# =============================
def normalize_text(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def load_json_any(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_expert_as_list(path: str) -> List[Dict[str, Any]]:
    data = load_json_any(path)
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data
    raise ValueError("Экспертный файл должен содержать dict или list")


def load_model_as_list(path: str) -> List[Dict[str, Any]]:
    data = load_json_any(path)
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data
    raise ValueError("Файл модели должен содержать dict или list")


# =============================
# Извлечение требований
# =============================
def extract_req_items(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Универсально извлекает требования из формата:
    1) {"reqs": [{"text": ..., "dependence": {...}}, ...]}
    2) {"requirement": "...", "dependence": {...}}  # если вдруг модель в плоском формате
    """
    result = []

    # Формат с reqs
    reqs = item.get("reqs")
    if isinstance(reqs, list):
        for req in reqs:
            if not isinstance(req, dict):
                continue

            text = normalize_text(req.get("text"))
            if not text:
                continue

            dep = req.get("dependence") or {}
            result.append({
                "text": text,
                "global": normalize_text(dep.get("global")),
                "local": normalize_text(dep.get("local")),
            })
        return result

    # Плоский формат модели
    requirement = normalize_text(item.get("requirement"))
    if requirement:
        dep = item.get("dependence") or {}
        result.append({
            "text": requirement,
            "global": normalize_text(dep.get("global")),
            "local": normalize_text(dep.get("local")),
        })

    return result


# =============================
# Сравнение контекстов
# =============================
def context_field_matches(
    expert_value: str,
    model_value: str,
    similarity_calculator: EmbeddingSimilarity,
    threshold: float
) -> Tuple[bool, float]:
    """
    Правила:
    - оба пустые -> match=True
    - один пустой, другой нет -> match=False
    - оба непустые -> cosine >= threshold
    """
    expert_value = normalize_text(expert_value)
    model_value = normalize_text(model_value)

    if not expert_value and not model_value:
        return True, 1.0

    if not expert_value or not model_value:
        return False, 0.0

    sim_matrix = similarity_calculator.cosine([expert_value], [model_value])
    if sim_matrix.size == 0:
        return False, 0.0

    score = float(sim_matrix[0, 0])
    return score >= threshold, score


def build_text_similarity_matrix(
    expert_items: List[Dict[str, Any]],
    model_items: List[Dict[str, Any]],
    similarity_calculator: EmbeddingSimilarity
) -> np.ndarray:
    expert_texts = [x["text"] for x in expert_items]
    model_texts = [x["text"] for x in model_items]
    return similarity_calculator.cosine(expert_texts, model_texts)


# =============================
# Matching без контекста
# =============================
def greedy_match_text_only(
    expert_items: List[Dict[str, Any]],
    model_items: List[Dict[str, Any]],
    text_sim_matrix: np.ndarray,
    text_threshold: float,
) -> Tuple[List[Tuple[int, int, float]], List[int], List[int]]:
    n_expert = len(expert_items)
    n_model = len(model_items)

    if n_expert == 0 or n_model == 0:
        return [], list(range(n_expert)), list(range(n_model))

    pairs = []
    for i in range(n_expert):
        for j in range(n_model):
            pairs.append((i, j, float(text_sim_matrix[i, j])))

    pairs.sort(key=lambda x: -x[2])

    used_expert = set()
    used_model = set()
    matches = []

    for i, j, sim in pairs:
        if sim < text_threshold:
            break
        if i not in used_expert and j not in used_model:
            matches.append((i, j, sim))
            used_expert.add(i)
            used_model.add(j)

    unmatched_expert = [i for i in range(n_expert) if i not in used_expert]
    unmatched_model = [j for j in range(n_model) if j not in used_model]

    return matches, unmatched_expert, unmatched_model


# =============================
# Matching с контекстом
# =============================
def greedy_match_with_context(
    expert_items: List[Dict[str, Any]],
    model_items: List[Dict[str, Any]],
    text_sim_matrix: np.ndarray,
    similarity_calculator: EmbeddingSimilarity,
    text_threshold: float,
    global_threshold: float,
    local_threshold: float,
) -> Tuple[List[Dict[str, Any]], List[int], List[int]]:
    """
    Пара допустима только если:
    - text >= text_threshold
    - global matched
    - local matched
    """
    n_expert = len(expert_items)
    n_model = len(model_items)

    if n_expert == 0 or n_model == 0:
        return [], list(range(n_expert)), list(range(n_model))

    candidates = []

    for i in range(n_expert):
        for j in range(n_model):
            text_sim = float(text_sim_matrix[i, j])
            if text_sim < text_threshold:
                continue

            global_ok, global_sim = context_field_matches(
                expert_items[i]["global"],
                model_items[j]["global"],
                similarity_calculator,
                global_threshold
            )

            local_ok, local_sim = context_field_matches(
                expert_items[i]["local"],
                model_items[j]["local"],
                similarity_calculator,
                local_threshold
            )

            if global_ok and local_ok:
                candidates.append({
                    "i": i,
                    "j": j,
                    "text_sim": text_sim,
                    "global_sim": global_sim,
                    "local_sim": local_sim,
                    # сортируем сначала по text, потом по context
                    "score": text_sim + global_sim + local_sim
                })

    candidates.sort(key=lambda x: -x["score"])

    used_expert = set()
    used_model = set()
    matches = []

    for cand in candidates:
        i = cand["i"]
        j = cand["j"]

        if i not in used_expert and j not in used_model:
            matches.append(cand)
            used_expert.add(i)
            used_model.add(j)

    unmatched_expert = [i for i in range(n_expert) if i not in used_expert]
    unmatched_model = [j for j in range(n_model) if j not in used_model]

    return matches, unmatched_expert, unmatched_model


# =============================
# Метрики
# =============================
def compute_metrics(
    expert_count: int,
    model_count: int,
    match_count: int
) -> Dict[str, int]:
    tp = match_count
    fn = expert_count - match_count
    fp = model_count - match_count
    return {"tp": tp, "fp": fp, "fn": fn}


def finalize_metrics(tp: int, fp: int, fn: int) -> Dict[str, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": 0,  # для extraction-задачи неинформативно
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


# =============================
# Основной пайплайн
# =============================
def main():
    print("Загрузка данных...")
    expert_data = load_expert_as_list(expert_file)
    model_data = load_model_as_list(model_file)

    # индексируем по paragraph
    expert_by_paragraph = {}
    for item in expert_data:
        paragraph = normalize_text(item.get("paragraph"))
        if paragraph:
            expert_by_paragraph[paragraph] = item

    model_by_paragraph = {}
    for item in model_data:
        paragraph = normalize_text(item.get("paragraph"))
        if paragraph:
            model_by_paragraph[paragraph] = item

    all_paragraphs = set(expert_by_paragraph.keys()) | set(model_by_paragraph.keys())

    print(f"Всего paragraph: {len(all_paragraphs)}")
    print(f"Embedding host={embedding_host}, batch_size={batch_size}")

    similarity_calculator = EmbeddingSimilarity(
        host=embedding_host,
        batch_size=batch_size
    )

    # глобальные счетчики
    text_only_tp = text_only_fp = text_only_fn = 0
    with_ctx_tp = with_ctx_fp = with_ctx_fn = 0

    details = {}

    for idx, paragraph in enumerate(all_paragraphs):
        if idx % 50 == 0:
            print(f"Обработка paragraph {idx + 1}/{len(all_paragraphs)}")

        expert_item = expert_by_paragraph.get(paragraph)
        model_item = model_by_paragraph.get(paragraph)

        expert_reqs = extract_req_items(expert_item) if expert_item else []
        model_reqs = extract_req_items(model_item) if model_item else []

        if expert_reqs and model_reqs:
            text_sim_matrix = build_text_similarity_matrix(
                expert_reqs,
                model_reqs,
                similarity_calculator
            )

            # 1) только text
            text_only_matches, text_only_unmatched_expert, text_only_unmatched_model = greedy_match_text_only(
                expert_reqs,
                model_reqs,
                text_sim_matrix,
                TEXT_THRESHOLD
            )

            # 2) text + context
            with_ctx_matches, with_ctx_unmatched_expert, with_ctx_unmatched_model = greedy_match_with_context(
                expert_reqs,
                model_reqs,
                text_sim_matrix,
                similarity_calculator,
                TEXT_THRESHOLD,
                GLOBAL_THRESHOLD,
                LOCAL_THRESHOLD
            )
        else:
            text_only_matches = []
            text_only_unmatched_expert = list(range(len(expert_reqs)))
            text_only_unmatched_model = list(range(len(model_reqs)))

            with_ctx_matches = []
            with_ctx_unmatched_expert = list(range(len(expert_reqs)))
            with_ctx_unmatched_model = list(range(len(model_reqs)))

        # text-only metrics
        m1 = compute_metrics(len(expert_reqs), len(model_reqs), len(text_only_matches))
        text_only_tp += m1["tp"]
        text_only_fp += m1["fp"]
        text_only_fn += m1["fn"]

        # context-aware metrics
        m2 = compute_metrics(len(expert_reqs), len(model_reqs), len(with_ctx_matches))
        with_ctx_tp += m2["tp"]
        with_ctx_fp += m2["fp"]
        with_ctx_fn += m2["fn"]

        details[paragraph] = {
            "expert_count": len(expert_reqs),
            "model_count": len(model_reqs),

            "text_only": {
                "tp": m1["tp"],
                "fp": m1["fp"],
                "fn": m1["fn"],
                "matches": [
                    {
                        "expert_text": expert_reqs[i]["text"],
                        "model_text": model_reqs[j]["text"],
                        "text_similarity": sim
                    }
                    for i, j, sim in text_only_matches
                ],
                "unmatched_expert": [expert_reqs[i] for i in text_only_unmatched_expert],
                "unmatched_model": [model_reqs[j] for j in text_only_unmatched_model],
            },

            "with_context": {
                "tp": m2["tp"],
                "fp": m2["fp"],
                "fn": m2["fn"],
                "matches": [
                    {
                        "expert_text": expert_reqs[m["i"]]["text"],
                        "model_text": model_reqs[m["j"]]["text"],
                        "text_similarity": m["text_sim"],
                        "global_similarity": m["global_sim"],
                        "local_similarity": m["local_sim"],
                        "expert_global": expert_reqs[m["i"]]["global"],
                        "model_global": model_reqs[m["j"]]["global"],
                        "expert_local": expert_reqs[m["i"]]["local"],
                        "model_local": model_reqs[m["j"]]["local"],
                    }
                    for m in with_ctx_matches
                ],
                "unmatched_expert": [expert_reqs[i] for i in with_ctx_unmatched_expert],
                "unmatched_model": [model_reqs[j] for j in with_ctx_unmatched_model],
            }
        }

    text_only_global = finalize_metrics(text_only_tp, text_only_fp, text_only_fn)
    with_context_global = finalize_metrics(with_ctx_tp, with_ctx_fp, with_ctx_fn)

    results = {
        "config": {
            "text_threshold": TEXT_THRESHOLD,
            "global_threshold": GLOBAL_THRESHOLD,
            "local_threshold": LOCAL_THRESHOLD,
            "embedding_host": embedding_host,
            "batch_size": batch_size
        },
        "metrics_text_only": text_only_global,
        "metrics_with_context": with_context_global,
        "paragraph_details": details
    }

    print("\n=== Метрики без контекста ===")
    print(f"TP: {text_only_global['tp']}")
    print(f"FP: {text_only_global['fp']}")
    print(f"FN: {text_only_global['fn']}")
    print(f"Precision: {text_only_global['precision']:.4f}")
    print(f"Recall: {text_only_global['recall']:.4f}")
    print(f"F1: {text_only_global['f1']:.4f}")

    print("\n=== Метрики с контекстом ===")
    print(f"TP: {with_context_global['tp']}")
    print(f"FP: {with_context_global['fp']}")
    print(f"FN: {with_context_global['fn']}")
    print(f"Precision: {with_context_global['precision']:.4f}")
    print(f"Recall: {with_context_global['recall']:.4f}")
    print(f"F1: {with_context_global['f1']:.4f}")

    with open("requirement_metrics_with_context.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("\nРезультаты сохранены в requirement_metrics_with_context.json")


if __name__ == "__main__":
    main()