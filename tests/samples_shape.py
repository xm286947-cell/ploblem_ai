def fact(value, evidence_type="INFERRED", confidence=0.8, reason=""):
    return {"value": value, "evidence_type": evidence_type, "confidence": confidence, "reason": reason}


def mixed_shape_response():
    return {
        "problem_summary": fact("队列拥堵并重启", "SUMMARIZED", 0.9),
        "standard_problem_description": fact("接线错误导致附加变量输出异常并引发队列拥堵", "SUMMARIZED", 0.9),
        "failure_objects": [fact("附加变量输出逻辑")],
        "phenomena": [fact("队列拥堵", "EXPLICIT", 1.0)],
        "trigger_conditions": [fact("错误接线", "EXPLICIT", 1.0)],
        "impacts": [fact("任务阻塞", "EXPLICIT", 1.0)],
        "operating_context": fact("客户现场", "EXPLICIT", 1.0),
        "trc": fact("输入校验不足"),
        "mrc": fact("", "UNKNOWN", 0.0),
        "root_causes": [fact("边界控制不足")],
        "failure_mechanisms": [fact("异常输入触发队列持续堆积")],
        "contributing_factors": [],
        "classification": {
            "cause_level1": fact("软件"),
            "cause_level2": fact("逻辑设计"),
            "cause_level3": fact("边界控制"),
            "cause_level4": fact("", "UNKNOWN", 0.0),
        },
        "keywords": [fact("接线错误", "EXPLICIT", 1.0), fact("队列拥堵", "EXPLICIT", 1.0)],
        "tags": [fact("软件问题", "EXPLICIT", 1.0)],
        "solution": {
            "current_solution": fact("增加输入校验"),
            "solution_object": fact("接线处理逻辑"),
            "solution_mechanism": fact("异常输入拦截"),
            "expected_effect": fact("避免队列持续堆积"),
        },
        "information_gaps": [],
        "overall_confidence": fact(0.8, "INFERRED", 0.8),
    }
