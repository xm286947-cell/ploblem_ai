"""Controlled context selectors used to steer the four quality-analysis stages."""
from __future__ import annotations

DOMAIN_PROFILES = ('AUTO', 'SOFTWARE', 'EMBEDDED', 'HARDWARE', 'MECHANICAL', 'SYSTEM_SOLUTION')
ISSUE_TYPES = (
    'FUNCTIONAL_DEFECT', 'PERFORMANCE', 'RELIABILITY', 'COMPATIBILITY',
    'SAFETY', 'PROCESS', 'ASSEMBLY', 'REQUIREMENT', 'VERSION_COMBINATION', 'DELIVERY',
)
LIFECYCLE_PHASES = (
    'AUTO', 'REQUIREMENT', 'SOLUTION_DESIGN', 'PRODUCT_COMBINATION', 'DEVELOPMENT',
    'UNIT_TEST', 'INTEGRATION_TEST', 'SOLUTION_INTEGRATION', 'RELEASE', 'DELIVERY',
    'UPGRADE', 'OPERATION', 'CHANGE_MANAGEMENT',
)

DOMAIN_LABELS = {
    'AUTO': '自动识别', 'SOFTWARE': '软件', 'EMBEDDED': '嵌入式',
    'HARDWARE': '硬件', 'MECHANICAL': '机械', 'SYSTEM_SOLUTION': '系统解决方案',
}
ISSUE_TYPE_LABELS = {
    'FUNCTIONAL_DEFECT': '功能缺陷', 'PERFORMANCE': '性能问题', 'RELIABILITY': '可靠性问题',
    'COMPATIBILITY': '兼容性问题', 'SAFETY': '安全问题', 'PROCESS': '工艺/流程问题',
    'ASSEMBLY': '装配问题', 'REQUIREMENT': '需求问题', 'VERSION_COMBINATION': '版本组合问题',
    'DELIVERY': '交付问题',
}
LIFECYCLE_LABELS = {
    'AUTO': '自动判断', 'REQUIREMENT': '需求澄清', 'SOLUTION_DESIGN': '方案设计',
    'PRODUCT_COMBINATION': '产品组合设计', 'DEVELOPMENT': '开发实现', 'UNIT_TEST': '单元测试',
    'INTEGRATION_TEST': '集成测试', 'SOLUTION_INTEGRATION': '解决方案联调', 'RELEASE': '版本发布',
    'DELIVERY': '交付部署', 'UPGRADE': '升级迁移', 'OPERATION': '运行维护',
    'CHANGE_MANAGEMENT': '变更管理',
}


def normalize_analysis_profile(profile=None) -> dict:
    raw = profile or {}
    if isinstance(raw, str):
        raw = {'domain_profile': raw}
    domain = str(raw.get('domain_profile') or 'AUTO').upper()
    lifecycle = str(raw.get('lifecycle_phase') or 'AUTO').upper()
    if domain not in DOMAIN_PROFILES:
        domain = 'AUTO'
    if lifecycle not in LIFECYCLE_PHASES:
        lifecycle = 'AUTO'
    values = raw.get('issue_types') or []
    if isinstance(values, str):
        values = [x for x in values.replace('，', ',').split(',') if x.strip()]
    issue_types = []
    for value in values:
        value = str(value).upper().strip()
        if value in ISSUE_TYPES and value not in issue_types:
            issue_types.append(value)
    return {
        'domain_profile': domain,
        'issue_types': issue_types,
        'lifecycle_phase': lifecycle,
        'selection_source': str(raw.get('selection_source') or ('USER' if (domain != 'AUTO' or issue_types or lifecycle != 'AUTO') else 'AUTO')).upper(),
        'taxonomy_version': str(raw.get('taxonomy_version') or '1.0'),
    }
