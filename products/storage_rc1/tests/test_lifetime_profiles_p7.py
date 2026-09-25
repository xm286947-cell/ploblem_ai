from storage_life import templates, ai

def test_profiles_are_device_specific():
    nor=templates.lifetime_profile('NOR Flash'); nand=templates.lifetime_profile('NAND Flash'); emmc=templates.lifetime_profile('eMMC'); ssd=templates.lifetime_profile('SSD')
    assert 'pe_cycles' in nor['parameters'] and 'tbw' not in nor['parameters']
    assert 'read_retry' in nand['parameters']
    assert 'life_time_a' in emmc['parameters']
    assert 'tbw' in ssd['parameters'] and 'life_time_a' not in ssd['parameters']

def test_parameter_names_are_bilingual():
    for dtype in ('NOR Flash','NAND Flash','eMMC','SSD'):
        for f in ai.expected_fields(dtype):
            assert '（' in f['parameter_name'] and '）' in f['parameter_name']

def test_lifetime_notes_for_core_fields():
    for dtype,key in [('NOR Flash','pe_cycles'),('NAND Flash','ecc_capability'),('eMMC','life_time_a'),('SSD','tbw')]:
        k=templates.parameter_knowledge(dtype,key)
        assert k['role'] and k['operation'] and len(k['note']) >= 12
