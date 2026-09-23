from .configurable import ConfigurableIssueAdapter


class HMIAdapter(ConfigurableIssueAdapter):
    business_type = "HMI"
    issue_id_fields = ("TRC单号",)


class PLCAdapter(ConfigurableIssueAdapter):
    business_type = "PLC"
    issue_id_fields = ("TRC单号", "ITR单号", "问题单号")


class IFAAdapter(ConfigurableIssueAdapter):
    business_type = "IFA"
    issue_id_fields = ("ITR单号",)


ADAPTERS = {"HMI": HMIAdapter, "PLC": PLCAdapter, "IFA": IFAAdapter}

def register_product_adapter(product_code: str):
    code = str(product_code or '').strip().upper()
    if not code:
        raise ValueError('PRODUCT_CODE_REQUIRED')
    if code not in ADAPTERS:
        ADAPTERS[code] = type(f'{code.title()}Adapter', (ConfigurableIssueAdapter,), {
            'business_type': code,
            'issue_id_fields': ('问题编号', 'TRC单号', 'ITR单号', '问题单号'),
        })
    return ADAPTERS[code]
