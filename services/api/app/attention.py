"""One follow-up per call; transaction approvals and provider errors stay distinct."""


def group_call_followups(items):
    result, groups = [], {}
    for item in sorted(items, key=lambda i: i["created_at"], reverse=True):
        if item.get("call_id") and item["kind"] in {"failed_action", "staff_followup"}:
            groups.setdefault(item["call_id"], []).append(item)
        else:
            result.append(item)
    for related in groups.values():
        representative = next((i for i in related if i["kind"] == "staff_followup"), related[0])
        result.append({**representative,
                       "title": "Staff assistance requested" if representative["kind"] == "staff_followup" else "Call follow-up",
                       "created_at": related[0]["created_at"],
                       "related_ids": [i["id"] for i in related],
                       "attempt_count": len(related)})
    return sorted(result, key=lambda i: i["created_at"], reverse=True)
