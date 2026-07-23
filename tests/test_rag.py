"""单独调用分诊 Agent 的 RAG 命中测试。

运行方式:
    python -m tests.test_rag              # 只验证 RAG 片段命中
    python -m tests.test_rag --run-agent  # 额外真实执行 triage_agent（需要 DefectDojo + test_id）

设计目标:
1. 构造一条“一定能命中分诊片段”的检索 query（覆盖分诊片段中的核心词：
   漏洞分诊 / CVSS / 已知在野利用 / 风险接受 / 补偿控制）。
2. 验证 load_knowledge_context 返回的上下文里确实包含分诊片段的关键内容。
3. 可选地把该 query 作为 knowledge_query 单独驱动 triage_agent。
"""

import os
import sys

from crewai import Crew, Process

from defectdojo_crewai.agents.triage import triage_agent
from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.services.knowledge_prompt import (
    load_knowledge_context,
    prepare_task_with_knowledge,
)
from defectdojo_crewai.tasks.triage_tasks import triage_task


# 这条 query 刻意使用分诊片段（data/knowledge 中“在漏洞管理系统中的应用”一节）
# 里出现的高区分度词汇，用来稳定命中该片段而不是补丁流程等其它片段。
TRIAGE_QUERY = (
    "漏洞分诊时不能只看 CVSS 分数，"
    "如何结合已知在野利用、互联网暴露和资产重要性判断处置优先级，"
    "无法立即修复时的补偿控制与风险接受审批要求是什么？"
)

# 命中分诊片段时，上下文里应当出现的关键短语（全部来自知识库原文）。
EXPECTED_PHRASES = [
    "漏洞分诊",
    "CVSS",
    "已知在野利用",
    "风险接受",
]


def test_triage_fragment_is_retrieved() -> None:
    """验证分诊 query 能命中 RAG 分诊片段。"""
    context = load_knowledge_context(TRIAGE_QUERY)
    print("\n===== 检索到的知识库上下文 =====")
    print(context)
    print("===== 上下文结束 =====\n")

    assert "知识片段" in context, (
        "没有检索到任何知识片段，请确认 KNOWLEDGE_ENABLED 与向量库已构建。"
    )

    missing = [phrase for phrase in EXPECTED_PHRASES if phrase not in context]
    assert not missing, (
        f"检索到的上下文缺少分诊片段关键内容: {missing}\n"
        f"实际上下文:\n{context}"
    )
    print(f"[OK] 分诊片段命中，包含全部关键短语: {EXPECTED_PHRASES}")


def test_triage_task_prompt_contains_fragment() -> None:
    """验证分诊片段确实被注入到 triage_task 的 prompt 里。"""
    prepared = prepare_task_with_knowledge(triage_task, TRIAGE_QUERY)
    assert "企业知识库上下文" in prepared.description
    # prepare 阶段会把 {} 转义成 {{ }}，这里只做包含性检查。
    for phrase in EXPECTED_PHRASES:
        assert phrase in prepared.description, (
            f"triage_task prompt 未包含分诊片段短语: {phrase}"
        )
    print("[OK] 分诊片段已注入 triage_task.description")


def run_triage_agent(test_id: int) -> None:
    """单独真实执行 triage_agent，使用命中分诊片段的 query 作为知识检索词。"""
    prepared = prepare_task_with_knowledge(triage_task, TRIAGE_QUERY)
    crew = Crew(
        agents=[triage_agent],
        tasks=[prepared],
        process=Process.sequential,
        verbose=settings.crew_verbose,
    )
    result = crew.kickoff(inputs={"test_id": test_id})
    print("\n===== triage_agent 执行结果 =====")
    print(result)
    print("===== 执行结束 =====")


if __name__ == "__main__":
    test_triage_fragment_is_retrieved()
    test_triage_task_prompt_contains_fragment()

    if "--run-agent" in sys.argv:
        raw_test_id = os.getenv("TRIAGE_TEST_ID")
        if not raw_test_id:
            raise SystemExit(
                "请设置环境变量 TRIAGE_TEST_ID 指定要分诊的 DefectDojo test_id，"
                "例如: TRIAGE_TEST_ID=1 python -m tests.test_rag --run-agent"
            )
        run_triage_agent(int(raw_test_id))
    else:
        print(
            "\n提示: 加上 --run-agent 且设置 TRIAGE_TEST_ID 可真实执行分诊 Agent。"
        )
