from __future__ import annotations

# Internal Chinese labels are governed aliases. Codes and hierarchy are the stable interface.
PRODUCT_QUALITY_MODEL = "ISO_IEC_25010_2023_PRODUCT"
QUALITY_IN_USE_MODEL = "ISO_IEC_25010_2011_QIU"
CUSTOMER_EXPERIENCE_MODEL = "CUSTOMER_QUALITY_EXPERIENCE_V1"

PRODUCT_QUALITY = {
    "FUNCTIONAL_SUITABILITY": ("功能适合性", {
        "FUNCTIONAL_COMPLETENESS":"功能完整性", "FUNCTIONAL_CORRECTNESS":"功能正确性", "FUNCTIONAL_APPROPRIATENESS":"功能适当性"}),
    "PERFORMANCE_EFFICIENCY": ("性能效率", {
        "TIME_BEHAVIOUR":"时间特性", "RESOURCE_UTILIZATION":"资源利用率", "CAPACITY":"容量"}),
    "COMPATIBILITY": ("兼容性", {"CO_EXISTENCE":"共存性", "INTEROPERABILITY":"互操作性"}),
    "INTERACTION_CAPABILITY": ("交互能力", {
        "APPROPRIATENESS_RECOGNIZABILITY":"适当可识别性", "LEARNABILITY":"易学性", "OPERABILITY":"易操作性",
        "USER_ERROR_PROTECTION":"用户差错防御性", "USER_ENGAGEMENT":"用户参与度", "INCLUSIVITY":"包容性",
        "USER_ASSISTANCE":"用户辅助性", "SELF_DESCRIPTIVENESS":"自描述性"}),
    "RELIABILITY": ("可靠性", {
        "FAULTLESSNESS":"无故障性", "AVAILABILITY":"可用性", "FAULT_TOLERANCE":"容错性", "RECOVERABILITY":"可恢复性"}),
    "SECURITY": ("信息安全性", {
        "CONFIDENTIALITY":"保密性", "INTEGRITY":"完整性", "NON_REPUDIATION":"抗抵赖性", "ACCOUNTABILITY":"可核查性",
        "AUTHENTICITY":"真实性", "RESISTANCE":"抵抗性"}),
    "MAINTAINABILITY": ("可维护性", {
        "MODULARITY":"模块化", "REUSABILITY":"可复用性", "ANALYSABILITY":"易分析性", "MODIFIABILITY":"易修改性", "TESTABILITY":"易测试性"}),
    "FLEXIBILITY": ("灵活性", {
        "ADAPTABILITY":"适应性", "SCALABILITY":"可伸缩性", "INSTALLABILITY":"易安装性", "REPLACEABILITY":"可替换性"}),
    "SAFETY": ("安全性", {
        "OPERATIONAL_CONSTRAINT":"运行约束性", "RISK_IDENTIFICATION":"风险识别性", "FAIL_SAFE":"失效安全性",
        "HAZARD_WARNING":"危险警告性", "SAFE_INTEGRATION":"安全集成性"}),
}

QUALITY_IN_USE = {
    "EFFECTIVENESS":"有效性", "EFFICIENCY":"效率", "SATISFACTION":"满意度",
    "FREEDOM_FROM_RISK":"免除风险", "CONTEXT_COVERAGE":"情境覆盖",
}

CUSTOMER_EXPERIENCES = {
    "CORRECT_EFFECTIVE":"正确有效", "EFFICIENT_SMOOTH":"高效流畅", "STABLE_CONTINUOUS":"稳定连续",
    "TRUSTWORTHY_CONTROLLABLE":"可信可控", "EASY_TO_LEARN_USE":"易学易用", "CLEAR_DIAGNOSABLE":"清晰可诊断",
    "COMPATIBLE_AVAILABLE":"兼容可用", "SAFE_REASSURING":"安全放心", "EASY_TO_RECOVER":"易恢复",
}

EXPERIENCE_MAP = {
    "CORRECT_EFFECTIVE": (["EFFECTIVENESS"], ["FUNCTIONAL_SUITABILITY"]),
    "EFFICIENT_SMOOTH": (["EFFICIENCY","SATISFACTION"], ["PERFORMANCE_EFFICIENCY","INTERACTION_CAPABILITY"]),
    "STABLE_CONTINUOUS": (["EFFECTIVENESS","FREEDOM_FROM_RISK"], ["RELIABILITY"]),
    "TRUSTWORTHY_CONTROLLABLE": (["EFFECTIVENESS","FREEDOM_FROM_RISK"], ["RELIABILITY","FUNCTIONAL_SUITABILITY","SECURITY"]),
    "EASY_TO_LEARN_USE": (["EFFICIENCY","SATISFACTION"], ["INTERACTION_CAPABILITY"]),
    "CLEAR_DIAGNOSABLE": (["EFFICIENCY","SATISFACTION"], ["MAINTAINABILITY","INTERACTION_CAPABILITY"]),
    "COMPATIBLE_AVAILABLE": (["EFFECTIVENESS","CONTEXT_COVERAGE"], ["COMPATIBILITY","FLEXIBILITY"]),
    "SAFE_REASSURING": (["FREEDOM_FROM_RISK"], ["SAFETY","SECURITY","RELIABILITY"]),
    "EASY_TO_RECOVER": (["EFFECTIVENESS","FREEDOM_FROM_RISK"], ["RELIABILITY","MAINTAINABILITY"]),
}

def model_payload():
    characteristics=[];subcharacteristics=[]
    for code,(label,children) in PRODUCT_QUALITY.items():
        characteristics.append({"code":code,"label_zh":label})
        subcharacteristics.extend({"code":child,"label_zh":child_label,"parent_code":code} for child,child_label in children.items())
    return {
        "versions":{"product_quality":PRODUCT_QUALITY_MODEL,"quality_in_use":QUALITY_IN_USE_MODEL,"customer_experience":CUSTOMER_EXPERIENCE_MODEL},
        "product_characteristics":characteristics,"product_subcharacteristics":subcharacteristics,
        "quality_in_use":[{"code":code,"label_zh":label} for code,label in QUALITY_IN_USE.items()],
        "customer_experiences":[{"code":code,"label_zh":label,"quality_in_use_codes":EXPERIENCE_MAP[code][0],"product_characteristic_codes":EXPERIENCE_MAP[code][1]} for code,label in CUSTOMER_EXPERIENCES.items()],
    }
