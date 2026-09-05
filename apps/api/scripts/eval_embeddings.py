"""Small live Chinese retrieval eval. No fixture vectors or cloud calls."""

import asyncio
import json
import math
import time

from forge_erp.modules.catalog.infrastructure.embeddings import embed, model_identity

PRODUCTS = [
    "可调节活动扳手，用于拧紧和松开不同尺寸的螺母",
    "十字螺丝刀，用于拆装十字槽螺钉",
    "聚四氟乙烯生料带，缠绕水管螺纹接口防止漏水",
    "电工绝缘胶带，用于包扎电线接头防止漏电",
    "304不锈钢外六角螺栓 M8×30，连接固定零件",
    "砂轮切割片，用于切割金属材料",
]
CASES = [
    ("拧螺母用的工具", 0),
    ("拆十字头螺丝的工具", 1),
    ("水管接头防漏缠的白色带子", 2),
    ("包电线接头防漏电的胶布", 3),
    ("割铁管用的圆片", 5),
]


async def main() -> None:
    vectors = [json.loads(await embed(p, indexing=True)) for p in PRODUCTS]
    results = []
    for query, expected in CASES:
        start = time.perf_counter()
        q = json.loads(await embed(query))
        scores = [sum(a * b for a, b in zip(q, v, strict=True)) for v in vectors]
        top = max(range(len(scores)), key=lambda i: scores[i])
        results.append(
            {
                "query": query,
                "expected": PRODUCTS[expected],
                "top": PRODUCTS[top],
                "score": round(scores[top], 4),
                "correct": top == expected,
                "latency_ms": round((time.perf_counter() - start) * 1000),
            }
        )
    assert all(math.isfinite(r["score"]) for r in results)
    output = {
        "model_identity": model_identity(),
        "correct": sum(r["correct"] for r in results),
        "cases": len(results),
        "results": results,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if not all(r["correct"] for r in results):
        raise SystemExit("Live semantic eval failed")


if __name__ == "__main__":
    asyncio.run(main())
