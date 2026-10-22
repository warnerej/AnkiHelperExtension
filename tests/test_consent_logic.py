from addon.consent_logic import CONSENT_KEY, has_consent, with_consent


def test_no_consent_by_default():
    assert not has_consent(None)
    assert not has_consent({})
    assert not has_consent({CONSENT_KEY: False})


def test_only_true_counts():
    assert has_consent({CONSENT_KEY: True})
    assert not has_consent({CONSENT_KEY: "yes"})
    assert not has_consent({CONSENT_KEY: 1})


def test_with_consent_keeps_other_keys():
    config = {"base_url": "https://x"}
    assert with_consent(config, True) == {"base_url": "https://x", CONSENT_KEY: True}
    assert config == {"base_url": "https://x"}
