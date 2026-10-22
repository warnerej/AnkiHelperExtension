from addon.about_logic import ABOUT_HTML, REVIEW_HEATMAP_URL, SUPPORT_URL


def test_attribution_links_and_wording():
    assert f'<a href="{REVIEW_HEATMAP_URL}">Review Heatmap</a>' in ABOUT_HTML
    assert f'<a href="{SUPPORT_URL}">Click here to support Glutanimate' in ABOUT_HTML
    assert "Based on the Anki add-on" in ABOUT_HTML
    assert REVIEW_HEATMAP_URL == "https://github.com/glutanimate/review-heatmap/"
    assert SUPPORT_URL == "https://glutanimate.com/support-my-work/"
