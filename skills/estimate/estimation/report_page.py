"""`render(template, chart_js, data, texts)`: the report page from `report/estimation-report.html`. One pass over the template
replaces each `<!--@name-->` with `texts["html"][name]`, `/*@data*/` with `const D = <data + D.notes>;` and `/*@chartjs*/` with
the vendored Chart.js source, so neither the inserted texts nor the Chart.js source is ever scanned for markers. The JSON is
embedded with `<`, `>`, `&`, U+2028 and U+2029 escaped, so no data string can close the script element or break the JS. A template
marker without a text, a text without a marker, or a script marker that is missing or repeated raises EstimateError: a defect."""
import json
import re

from estimation.errors import EstimateError

TEXT_MARKER = "<!--@%s-->"
DATA_MARKER = "/*@data*/"
CHARTJS_MARKER = "/*@chartjs*/"
SCRIPT_MARKERS = (DATA_MARKER, CHARTJS_MARKER)
MARKERS = re.compile(r"<!--@([a-z_]+)-->|/\*@(?:data|chartjs)\*/")
JSON_ESCAPES = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026", "\u2028": "\\u2028", "\u2029": "\\u2029"}
JSON_UNSAFE = re.compile("[%s]" % "".join(JSON_ESCAPES))


def _script_json(value):
    return JSON_UNSAFE.sub(lambda match: JSON_ESCAPES[match.group()], json.dumps(value, ensure_ascii=False))


def _check(template, html):
    names = set(MARKERS.findall(template)) - {""}
    missing = sorted(names - html.keys())
    unused = sorted(html.keys() - names)
    if missing:
        raise EstimateError("report template marker without a text: %s" % ", ".join(missing))
    if unused:
        raise EstimateError("report text without a template marker: %s" % ", ".join(unused))
    for marker in SCRIPT_MARKERS:
        if template.count(marker) != 1:
            raise EstimateError("report template needs %s exactly once" % marker)


def render(template, chart_js, data, texts):
    html = texts["html"]
    _check(template, html)
    scripts = {DATA_MARKER: "const D = %s;" % _script_json({**data, "notes": texts["notes"]}), CHARTJS_MARKER: chart_js}
    return MARKERS.sub(lambda match: html[match.group(1)] if match.group(1) else scripts[match.group()], template)
