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
model_file = "/Users/olgagavrilenko/PycharmProjects/itmo-practice-2026/agent_experiment_req/requirements_with_context_for_practice_qwen.json"


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
# Вспомогательные функции
# =============================
def normalize_text(s: Optional[str]) -> str:
    s = s or ""
    s = s.replace("-", "-").replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def load_json_any(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_expert_as_list(path: str) -> List[Dict[str, Any]]:
    data = load_json_any(path)
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data
    raise ValueError("Экспертный файл должен быть dict или list")


def load_model_as_list(path: str) -> List[Dict[str, Any]]:
    data = load_json_any(path)
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data
    raise ValueError("Файл модели должен быть dict или list")


# =============================
# Извлечение данных
# =============================
def extract_expert_reqs(item: Dict[str, Any]) -> List[Dict[str, str]]:
    result = []
    reqs = item.get("reqs") or []

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


def extract_model_reqs(items: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    result = []

    for item in items:
        text = normalize_text(item.get("requirement"))
        if not text:
            continue

        result.append({
            "text": text,
            "global": normalize_text(item.get("global_relevant_context")),
            "local": normalize_text(item.get("local_relevant_context")),
        })

    return result


# =============================
# Сравнение контекста
# =============================
def context_match(
    expert_value: str,
    model_value: str,
    sim_calc: EmbeddingSimilarity,
    threshold: float
) -> Tuple[bool, float]:
    expert_value = normalize_text(expert_value)
    model_value = normalize_text(model_value)

    # оба пустые -> считаем совпадением
    if not expert_value and not model_value:
        return True, 1.0

    # один пустой, другой нет -> не совпали
    if not expert_value or not model_value:
        return False, 0.0

    sim_mat = sim_calc.cosine([expert_value], [model_value])
    if sim_mat.size == 0:
        return False, 0.0

    score = float(sim_mat[0, 0])
    return score >= threshold, score


# =============================
# Матрица сходства по text
# =============================
def build_text_similarity_matrix(
    expert_reqs: List[Dict[str, str]],
    model_reqs: List[Dict[str, str]],
    sim_calc: EmbeddingSimilarity
) -> np.ndarray:
    expert_texts = [x["text"] for x in expert_reqs]
    model_texts = [x["text"] for x in model_reqs]
    return sim_calc.cosine(expert_texts, model_texts)


# =============================
# Matching только по требованиям
# =============================
def greedy_match_text_only(
    expert_reqs: List[Dict[str, str]],
    model_reqs: List[Dict[str, str]],
    text_sim_matrix: np.ndarray,
    text_threshold: float
) -> Tuple[List[Tuple[int, int, float]], List[int], List[int]]:
    n_expert = len(expert_reqs)
    n_model = len(model_reqs)

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
# Matching по требованиям + контекстам
# =============================
def greedy_match_with_context(
    expert_reqs: List[Dict[str, str]],
    model_reqs: List[Dict[str, str]],
    text_sim_matrix: np.ndarray,
    sim_calc: EmbeddingSimilarity,
    text_threshold: float,
    global_threshold: float,
    local_threshold: float,
) -> Tuple[List[Dict[str, Any]], List[int], List[int]]:
    n_expert = len(expert_reqs)
    n_model = len(model_reqs)

    if n_expert == 0 or n_model == 0:
        return [], list(range(n_expert)), list(range(n_model))

    candidates = []

    for i in range(n_expert):
        for j in range(n_model):
            text_sim = float(text_sim_matrix[i, j])
            if text_sim < text_threshold:
                continue

            global_ok, global_sim = context_match(
                expert_reqs[i]["global"],
                model_reqs[j]["global"],
                sim_calc,
                global_threshold
            )
            local_ok, local_sim = context_match(
                expert_reqs[i]["local"],
                model_reqs[j]["local"],
                sim_calc,
                local_threshold
            )

            if global_ok and local_ok:
                candidates.append({
                    "i": i,
                    "j": j,
                    "text_sim": text_sim,
                    "global_sim": global_sim,
                    "local_sim": local_sim,
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
def compute_metrics(expert_count: int, model_count: int, match_count: int) -> Dict[str, int]:
    tp = match_count
    fn = expert_count - match_count
    fp = model_count - match_count
    tn = 0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def finalize_metrics(tp: int, fp: int, fn: int, tn: int = 0) -> Dict[str, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


# =============================
# Основная логика
# =============================
def main():
    print("Загрузка данных...")
    expert_data = load_expert_as_list(expert_file)
    model_data = load_model_as_list(model_file)

    # эксперт: 1 paragraph -> 1 объект с reqs
    expert_by_paragraph = {}
    for item in expert_data:
        paragraph = normalize_text(item.get("paragraph"))
        if paragraph:
            expert_by_paragraph[paragraph] = item

    # модель: 1 paragraph -> список объектов
    model_by_paragraph = defaultdict(list)
    for item in model_data:
        paragraph = normalize_text(item.get("paragraph"))
        if paragraph:
            model_by_paragraph[paragraph].append(item)

    all_paragraphs = set(expert_by_paragraph.keys()) | set(model_by_paragraph.keys())

    print(f"Всего paragraph: {len(all_paragraphs)}")

    sim_calc = EmbeddingSimilarity(
        host=embedding_host,
        batch_size=batch_size
    )

    # глобальные счетчики
    text_tp = text_fp = text_fn = text_tn = 0
    ctx_tp = ctx_fp = ctx_fn = ctx_tn = 0

    paragraph_details = {}

    for idx, paragraph in enumerate(all_paragraphs):
        if idx % 50 == 0:
            print(f"Обработка paragraph {idx + 1}/{len(all_paragraphs)}")

        expert_item = expert_by_paragraph.get(paragraph)
        model_items = model_by_paragraph.get(paragraph, [])

        expert_reqs = extract_expert_reqs(expert_item) if expert_item else []
        model_reqs = extract_model_reqs(model_items)

        if expert_reqs and model_reqs:
            text_sim_matrix = build_text_similarity_matrix(
                expert_reqs,
                model_reqs,
                sim_calc
            )

            text_matches, text_unmatched_expert, text_unmatched_model = greedy_match_text_only(
                expert_reqs,
                model_reqs,
                text_sim_matrix,
                TEXT_THRESHOLD
            )

            ctx_matches, ctx_unmatched_expert, ctx_unmatched_model = greedy_match_with_context(
                expert_reqs,
                model_reqs,
                text_sim_matrix,
                sim_calc,
                TEXT_THRESHOLD,
                GLOBAL_THRESHOLD,
                LOCAL_THRESHOLD
            )
        else:
            text_matches = []
            text_unmatched_expert = list(range(len(expert_reqs)))
            text_unmatched_model = list(range(len(model_reqs)))

            ctx_matches = []
            ctx_unmatched_expert = list(range(len(expert_reqs)))
            ctx_unmatched_model = list(range(len(model_reqs)))

        # только требования
        m_text = compute_metrics(len(expert_reqs), len(model_reqs), len(text_matches))
        text_tp += m_text["tp"]
        text_fp += m_text["fp"]
        text_fn += m_text["fn"]
        text_tn += m_text["tn"]

        # требования + контекст
        m_ctx = compute_metrics(len(expert_reqs), len(model_reqs), len(ctx_matches))
        ctx_tp += m_ctx["tp"]
        ctx_fp += m_ctx["fp"]
        ctx_fn += m_ctx["fn"]
        ctx_tn += m_ctx["tn"]

        paragraph_details[paragraph] = {
            "expert_count": len(expert_reqs),
            "model_count": len(model_reqs),

            "requirements_only": {
                "tp": m_text["tp"],
                "fp": m_text["fp"],
                "fn": m_text["fn"],
                "tn": m_text["tn"],
                "matches": [
                    {
                        "expert_text": expert_reqs[i]["text"],
                        "model_requirement": model_reqs[j]["text"],
                        "text_similarity": sim
                    }
                    for i, j, sim in text_matches
                ],
                "unmatched_expert": [expert_reqs[i] for i in text_unmatched_expert],
                "unmatched_model": [model_reqs[j] for j in text_unmatched_model],
            },

            "requirements_with_context": {
                "tp": m_ctx["tp"],
                "fp": m_ctx["fp"],
                "fn": m_ctx["fn"],
                "tn": m_ctx["tn"],
                "matches": [
                    {
                        "expert_text": expert_reqs[m["i"]]["text"],
                        "model_requirement": model_reqs[m["j"]]["text"],
                        "text_similarity": m["text_sim"],
                        "expert_global": expert_reqs[m["i"]]["global"],
                        "model_global": model_reqs[m["j"]]["global"],
                        "global_similarity": m["global_sim"],
                        "expert_local": expert_reqs[m["i"]]["local"],
                        "model_local": model_reqs[m["j"]]["local"],
                        "local_similarity": m["local_sim"],
                    }
                    for m in ctx_matches
                ],
                "unmatched_expert": [expert_reqs[i] for i in ctx_unmatched_expert],
                "unmatched_model": [model_reqs[j] for j in ctx_unmatched_model],
            }
        }

    metrics_text_only = finalize_metrics(text_tp, text_fp, text_fn, text_tn)
    metrics_with_context = finalize_metrics(ctx_tp, ctx_fp, ctx_fn, ctx_tn)

    results = {
        "config": {
            "text_threshold": TEXT_THRESHOLD,
            "global_threshold": GLOBAL_THRESHOLD,
            "local_threshold": LOCAL_THRESHOLD,
            "embedding_host": embedding_host,
            "batch_size": batch_size,
        },
        "metrics_requirements_only": metrics_text_only,
        "metrics_requirements_with_context": metrics_with_context,
        "paragraph_details": paragraph_details
    }

    print("\n=== Только требования ===")
    print(f"TP: {metrics_text_only['tp']}")
    print(f"FP: {metrics_text_only['fp']}")
    print(f"FN: {metrics_text_only['fn']}")
    print(f"TN: {metrics_text_only['tn']}")
    print(f"Precision: {metrics_text_only['precision']:.4f}")
    print(f"Recall: {metrics_text_only['recall']:.4f}")
    print(f"F1: {metrics_text_only['f1']:.4f}")

    print("\n=== Требования + контекст ===")
    print(f"TP: {metrics_with_context['tp']}")
    print(f"FP: {metrics_with_context['fp']}")
    print(f"FN: {metrics_with_context['fn']}")
    print(f"TN: {metrics_with_context['tn']}")
    print(f"Precision: {metrics_with_context['precision']:.4f}")
    print(f"Recall: {metrics_with_context['recall']:.4f}")
    print(f"F1: {metrics_with_context['f1']:.4f}")

    with open("requirement_metrics_flat_model.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("\nРезультаты сохранены в requirement_metrics_flat_model.json")


if __name__ == "__main__":
    main()