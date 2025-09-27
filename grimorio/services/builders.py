def build_spell_panels(spells):
    return [{'spell': sp, 'attributes': sp.attributes_json or {}} for sp in spells]
