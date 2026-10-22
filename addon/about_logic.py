"""Wording of the About dialog, including the attribution Review Heatmap's license requires.

Free of aqt for plain-Python tests.
"""

REVIEW_HEATMAP_URL = "https://github.com/glutanimate/review-heatmap/"
SUPPORT_URL = "https://glutanimate.com/support-my-work/"

ABOUT_TITLE = "About AnkiHelper"
ABOUT_HTML = (
    "<p><b>AnkiHelper</b> shares your study stats with friends in groups.</p>"
    "<p>Based on the Anki add-on "
    f'<a href="{REVIEW_HEATMAP_URL}">Review Heatmap</a> by Glutanimate. '
    f'<a href="{SUPPORT_URL}">Click here to support Glutanimate\'s work</a>.</p>'
    "<p>Review Heatmap is licensed under the GNU Affero General Public License "
    "version 3 with additional terms. A copy of its license is in the "
    "<code>licenses/review-heatmap</code> folder of this add-on. Licenses of the "
    "bundled libraries are in <code>licenses/</code> too.</p>"
    "<p>This program comes with no warranty, to the extent permitted by law.</p>"
)
