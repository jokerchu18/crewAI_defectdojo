from defectdojo_crewai.models.schemas import RiskAcceptanceCandidate


def person_review(results : list[RiskAcceptanceCandidate]) :

    approved_candidates = []
    rejected_by_agent = []

    for candidate in results:
        if candidate.decision != "Accept":
            rejected_by_agent.append(candidate)
            print(f"finding {candidate.finding_id} 被 review agent 判定为 Reject，跳过人工审批。")
            continue

        print("\n==============================")
        print(f"finding_id: {candidate.finding_id}")
        print(f"severity: {candidate.severity}")
        print(f"title: {candidate.title}")
        print(f"decision: {candidate.decision}")
        print(f"reason: {candidate.reason}")
        print(f"expiration_date: {candidate.expiration_date}")
        print(f"reactivate_expired: {candidate.reactivate_expired}")
        print(f"restart_sla_expired: {candidate.restart_sla_expired}")

        action = input("请输入审批操作 approve / reject / edit: ").strip().lower()

        if action == "approve":
            approved_candidates.append(candidate)

        elif action == "edit":
            new_expiration_date = input("新的 expiration_date (YYYY-MM-DD): ").strip()
            new_reactivate_expired = input("新的 reactivate_expired (true/false): ").strip().lower() == "true"
            new_restart_sla_expired = input("新的 restart_sla_expired (true/false): ").strip().lower() == "true"

            candidate.expiration_date = new_expiration_date
            candidate.reactivate_expired = new_reactivate_expired
            candidate.restart_sla_expired = new_restart_sla_expired
            approved_candidates.append(candidate)

        elif action == "reject":
            print(f"{candidate.finding_id}已拒绝accept请求")

    if not approved_candidates:
        print("没有任何 finding 被批准，流程结束。")
        return None

    approved_candidates_payload = [
        candidate.model_dump() for candidate in approved_candidates
    ]

    return approved_candidates_payload