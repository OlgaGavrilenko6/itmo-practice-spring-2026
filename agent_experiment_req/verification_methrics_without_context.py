import json
import re
from collections import defaultdict
from typing import List, Dict, Any, Tuple
from ontology.taxonomy_clustering.embedding_filter import ServerEmbeddings
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity


# -----------------------------
# Параметры
# -----------------------------
Threshold_test = 0.85

embedding_host="http://10.32.15.7:8082/embed"
batch_size = 8

expert_file = "/Users/olgagavrilenko/PycharmProjects/itmo-practice-2026/agent_experiment_req/requirement_gold_251.json"
model_file = "/Users/olgagavrilenko/PycharmProjects/itmo-practice-2026/agent_experiment_req/requirements_without_context_for_practice.json"


# -----------------------------
# Эмбеддинги
# -----------------------------
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

    def similarity_matrix(self, emb1: np.ndarray, emb2: np.ndarray) -> np.ndarray:
        if emb1.size == 0 or emb2.size == 0:
            return np.array([])
        return cosine_similarity(emb1, emb2)


# -----------------------------
# Вспомогательные функции
# -----------------------------
def load_json_any(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def load_expert_as_list(path: str) -> List[Dict[str, Any]]:
    data = load_json_any(path)

    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data

    raise ValueError("Экспертный файл должен содержать объект или список объектов")


def load_model_as_list(path: str) -> List[Dict[str, Any]]:
    data = load_json_any(path)

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]

    raise ValueError("Файл модели должен содержать список объектов")


def extract_expert_reqs(item: Dict[str, Any]) -> List[str]:
    reqs = item.get("reqs") or []
    result = []

    for req in reqs:
        if isinstance(req, dict):
            text = normalize_text(req.get("text", ""))
            if text:
                result.append(text)

    return result


def extract_model_requirements(items: List[Dict[str, Any]]) -> List[str]:
    result = []

    for item in items:
        text = normalize_text(item.get("requirement", ""))
        if text:
            result.append(text)

    return result


# -----------------------------
# Greedy matching
# -----------------------------
def greedy_match_reqs(
    expert_reqs: List[str],
    model_reqs: List[str],
    sim_matrix: np.ndarray,
    threshold: float
) -> Tuple[List[Tuple[int, int, float]], List[int], List[int]]:
    n_expert = len(expert_reqs)
    n_model = len(model_reqs)

    if n_expert == 0 or n_model == 0:
        return [], list(range(n_expert)), list(range(n_model))

    pairs = []
    for i in range(n_expert):
        for j in range(n_model):
            pairs.append((i, j, sim_matrix[i, j]))

    pairs.sort(key=lambda x: -x[2])

    used_expert = set()
    used_model = set()
    matches = []

    for i, j, sim in pairs:
        if sim < threshold:
            break
        if i not in used_expert and j not in used_model:
            matches.append((i, j, sim))
            used_expert.add(i)
            used_model.add(j)

    unmatched_expert = [i for i in range(n_expert) if i not in used_expert]
    unmatched_model = [j for j in range(n_model) if j not in used_model]

    return matches, unmatched_expert, unmatched_model


def compute_metrics_for_paragraph(
    expert_reqs: List[str],
    model_reqs: List[str],
    matches: List[Tuple[int, int, float]]
) -> Dict[str, int]:
    tp = len(matches)
    fn = len(expert_reqs) - len(matches)
    fp = len(model_reqs) - len(matches)

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn
    }


# -----------------------------
# Основная логика
# -----------------------------
def main():
    print("Загрузка данных...")
    expert_data = load_expert_as_list(expert_file)
    model_data = load_model_as_list(model_file)

    # Эксперт: один paragraph -> один объект с reqs
    expert_by_paragraph = {}
    for item in expert_data:
        paragraph = normalize_text(item.get("paragraph", ""))
        if paragraph:
            expert_by_paragraph[paragraph] = item

    # Модель: один paragraph -> список объектов с requirement
    model_by_paragraph = defaultdict(list)
    for item in model_data:
        paragraph = normalize_text(item.get("paragraph", ""))
        if paragraph:
            model_by_paragraph[paragraph].append(item)

    all_paragraphs = set(expert_by_paragraph.keys()) | set(model_by_paragraph.keys())

    print(f"Всего уникальных paragraph: {len(all_paragraphs)}")
    print(f"Инициализация эмбеддингов: host={embedding_host}, batch_size={batch_size}")

    sim_calculator = EmbeddingSimilarity(host=embedding_host, batch_size=batch_size)

    total_tp = 0
    total_fp = 0
    total_fn = 0

    paragraph_metrics = {}

    for idx, paragraph in enumerate(all_paragraphs):
        if idx % 50 == 0:
            print(f"Обработка paragraph {idx + 1}/{len(all_paragraphs)}")

        expert_item = expert_by_paragraph.get(paragraph)
        model_items = model_by_paragraph.get(paragraph, [])

        expert_reqs = extract_expert_reqs(expert_item) if expert_item else []
        model_reqs = extract_model_requirements(model_items)

        if expert_reqs and model_reqs:
            expert_emb = sim_calculator.encode(expert_reqs)
            model_emb = sim_calculator.encode(model_reqs)
            sim_mat = sim_calculator.similarity_matrix(expert_emb, model_emb)

            matches, unmatched_expert, unmatched_model = greedy_match_reqs(
                expert_reqs,
                model_reqs,
                sim_mat,
                threshold=Threshold_test
            )
        else:
            matches = []
            unmatched_expert = list(range(len(expert_reqs)))
            unmatched_model = list(range(len(model_reqs)))

        metrics = compute_metrics_for_paragraph(expert_reqs, model_reqs, matches)

        total_tp += metrics["tp"]
        total_fp += metrics["fp"]
        total_fn += metrics["fn"]

        paragraph_metrics[paragraph] = {
            "expert_count": len(expert_reqs),
            "model_count": len(model_reqs),
            "tp": metrics["tp"],
            "fp": metrics["fp"],
            "fn": metrics["fn"],
            "matches": [
                {
                    "expert_text": expert_reqs[i],
                    "model_requirement": model_reqs[j],
                    "similarity": float(sim)
                }
                for i, j, sim in matches
            ],
            "unmatched_expert": [expert_reqs[i] for i in unmatched_expert],
            "unmatched_model": [model_reqs[j] for j in unmatched_model]
        }

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    results = {
        "global": {
            "tp": total_tp,
            "fp": total_fp,
            "fn": total_fn,
            "precision": precision,
            "recall": recall,
            "f1": f1
        },
        "config": {
            "threshold": Threshold_test,
            "embedding_host": embedding_host,
            "batch_size": batch_size
        },
        "paragraph_metrics": paragraph_metrics
    }

    print("\nМетрики:")
    print(f"TP: {total_tp}")
    print(f"FP: {total_fp}")
    print(f"FN: {total_fn}")
    print(f"Precision: {precision:.4f}")
    print(f"Recall: {recall:.4f}")
    print(f"F1: {f1:.4f}")

    with open("requirement_metrics_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("\nРезультаты сохранены в requirement_metrics_results.json")


if __name__ == "__main__":
    main()