"""`report_page.render` fills the report template: every `<!--@name-->` gets its TEXTS html, `/*@data*/` becomes `const D = …;`
with the notes merged as `D.notes`, `/*@chartjs*/` becomes the Chart.js source. Data strings cannot leave the script element, a
marker without text or a text without marker is a defect, and the Chart.js source is never scanned for markers. The real template
must carry a marker for every key the findings produce and render into a page with no marker left."""
import json
import os
import re
import unittest

from estimation import report_page
from estimation.errors import EstimateError

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = """<html><body><h1><!--@title--></h1><p><!--@lede--></p>
<script>/*@chartjs*/</script>
<script>/*@data*/
document.title = D.project;</script></body></html>"""
CHART_JS = "window.Chart = function () {};"
DATA_PATTERN = re.compile(r"const D = (.*?);\n", re.DOTALL)
CONTRACT_MARKERS = ("eyebrow", "lede", "rho_note", "verdict_tag", "verdict_big", "catches", "findings", "spcore_note",
                    "scaling_callout", "quality_sub", "quality_note", "quality_callout", "outliers_sub", "usage_title",
                    "usage_sub", "usage_points", "forecast_sub", "forecast_points", "method", "footer")
LEFTOVER = re.compile(r"<!--@|/\*@data\*/|/\*@chartjs\*/")


def texts(**html):
    return {"html": html, "notes": {"tasks": "Tasks note"}}


def embedded(page):
    return json.loads(DATA_PATTERN.search(page).group(1))


class RenderTest(unittest.TestCase):
    def test_fills_every_marker(self):
        page = report_page.render(TEMPLATE, CHART_JS, {"project": "demo"}, texts(title="Title <b>x</b>", lede="Lede"))
        self.assertIn("<h1>Title <b>x</b></h1><p>Lede</p>", page)
        self.assertIn("<script>" + CHART_JS + "</script>", page)
        self.assertIsNone(LEFTOVER.search(page))

    def test_embeds_data_with_notes(self):
        page = report_page.render(TEMPLATE, CHART_JS, {"project": "demo", "rows": [1, 2]}, texts(title="", lede=""))
        self.assertEqual(embedded(page), {"project": "demo", "rows": [1, 2], "notes": {"tasks": "Tasks note"}})

    def test_data_strings_cannot_leave_the_script(self):
        label = "</script><script>alert(1)</script><!-- a\u2028b\u2029c & d > e"
        page = report_page.render(TEMPLATE, CHART_JS, {"project": label}, texts(title="", lede=""))
        self.assertEqual(page.count("</script>"), TEMPLATE.count("</script>"))
        self.assertNotIn("<!--", page[page.index("const D = "):])
        for raw in ("<", ">", "&", "\u2028", "\u2029"):
            self.assertNotIn(raw, DATA_PATTERN.search(page).group(1))
        self.assertEqual(embedded(page)["project"], label)

    def test_missing_text_raises(self):
        with self.assertRaisesRegex(EstimateError, "lede"):
            report_page.render(TEMPLATE, CHART_JS, {}, texts(title="Title"))

    def test_unused_text_raises(self):
        with self.assertRaisesRegex(EstimateError, "extra"):
            report_page.render(TEMPLATE, CHART_JS, {}, texts(title="", lede="", extra="Extra"))

    def test_missing_script_marker_raises(self):
        with self.assertRaisesRegex(EstimateError, "chartjs"):
            report_page.render(TEMPLATE.replace("/*@chartjs*/", ""), CHART_JS, {}, texts(title="", lede=""))

    def test_chart_source_is_not_scanned_for_markers(self):
        chart_js = "/* <!--@title--> /*@data*/ <!--@unknown--> */"
        page = report_page.render(TEMPLATE, chart_js, {}, texts(title="T", lede="L"))
        self.assertIn("<script>" + chart_js + "</script>", page)

    def test_texts_are_not_scanned_for_markers(self):
        page = report_page.render(TEMPLATE, CHART_JS, {}, texts(title="<!--@lede-->", lede="/*@data*/"))
        self.assertIn("<h1><!--@lede--></h1><p>/*@data*/</p>", page)
        self.assertEqual(page.count("const D = "), 1)


class ShippedTemplateTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(SKILL_ROOT, "report", "estimation-report.html"), encoding="utf-8") as handle:
            self.template = handle.read()
        with open(os.path.join(SKILL_ROOT, "report", "chart.umd.js"), encoding="utf-8") as handle:
            self.chart_js = handle.read()

    def test_renders_with_the_contract_texts(self):
        page = report_page.render(self.template, self.chart_js, {"rows": []},
                                  {"html": dict.fromkeys(CONTRACT_MARKERS, "x"), "notes": {}})
        self.assertIsNone(LEFTOVER.search(page.replace(self.chart_js, "")))

    def test_vendored_chart_js_carries_both_licenses(self):
        self.assertIn("Chart.js v4.4.7", self.chart_js)
        self.assertIn("Copyright (c) 2014-2024 Chart.js Contributors", self.chart_js)
        self.assertIn("Copyright (c) 2018-2021 Jukka Kurkela", self.chart_js)
        self.assertNotIn("sourceMappingURL", self.chart_js)


if __name__ == "__main__":
    unittest.main()
