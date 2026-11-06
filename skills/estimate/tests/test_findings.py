"""`findings.texts` on hand-built DATA dicts: every marker and note is produced, each rule fires or stays silent at its threshold,
the numbers in the text are the data's, data strings are HTML-escaped, and missing statistics degrade without 'None' or 'nan'."""
import copy
import unittest

from estimation.findings import texts

MARKERS = ("eyebrow", "lede", "rho_note", "verdict_tag", "verdict_big", "catches", "findings", "spcore_note", "scaling_callout",
           "quality_sub", "quality_note", "quality_callout", "outliers_sub", "usage_title", "usage_sub", "usage_points",
           "forecast_sub", "forecast_points", "method", "footer")
METRICS = [["tasks", "Tasks"], ["hours", "Estimated hours"], ["commits_excl", "Commits (exclusive)"], ["code", "Code lines"],
           ["phases", "Phases"], ["churn", "Lines changed (raw)"], ["commits", "Commits (raw)"], ["src", "Source lines"]]
MVARS = [["sp", "Story points"], ["phases", "Phases"], ["tasks", "Tasks"], ["hours", "Est. hours"],
         ["commits", "Commits (raw)"], ["commits_excl", "Commits (excl.)"], ["churn", "Lines (raw)"], ["code", "Code lines"],
         ["src", "Source lines"]]


def _row(label, sp, **values):
    row = {"label": label, "name": label, "id": None, "desc": "", "sp": sp, "est": None, "estimated_at": None,
           "estimated_by": None, "phases": 4, "tasks": sp * 2, "hours": None, "commits": sp * 2, "commits_excl": sp * 2,
           "added": sp * 100, "removed": 0, "churn": sp * 100, "src": sp * 40, "tests": sp * 40, "docs": sp * 20, "vendored": 0,
           "other": 0, "code": sp * 80, "git_churn": sp * 100, "missing": 0, "sessions": 0, "agent_min": None, "cost": None,
           "tokens": None, "cache_read": None, "matched": "none", "first": "2026-01-01", "last": "2026-01-02",
           "fit_sp": sp, "fit_step": 0, "resid": 0, "resid_log": 0.0}
    row.update(values)
    return row


def _res(rho, n=6):
    return {"label": "", "n": n, "spearman": rho, "ci": None, "pearson": rho, "pearson_log": rho, "conc": rho, "n_core": n,
            "spearman_core": rho, "conc_core": rho}


def make_data(rho=0.9):
    """A minimal valid DATA: six features at SP 2, 2, 3, 3, 5, 5, every metric at the same ρ."""
    rows = [_row("F%d" % index, sp) for index, sp in enumerate((2, 2, 3, 3, 5, 5))]
    keys = [key for key, _ in METRICS]
    return {
        "project": "demo", "generated_on": "2026-11-05", "store": ".harness-goodies/estimations", "scale": [1, 2, 3, 5, 8],
        "counts": {"stored": 6, "references": 6, "not_delivered": 0, "unsized": 0}, "colors": {}, "rows": rows,
        "metrics": copy.deepcopy(METRICS), "res": {key: _res(rho) for key in keys},
        "pred": {key: None for key in keys}, "bstats": {key: {} for key in keys}, "sep": {key: {} for key in keys},
        "mvars": copy.deepcopy(MVARS), "corr": [[rho] * len(MVARS) for _ in MVARS], "corrn": [[6] * len(MVARS) for _ in MVARS],
        "core": None, "base_sp": 3,
        "fit": {"n": 6, "exact": 6, "within1": 6, "max_steps": 0, "baseline_sp": 2, "baseline_exact": 2, "baseline_within1": 4},
        "lines": {"cats": {"src": 400, "tests": 400, "docs": 200, "vendored": 0, "other": 0}, "raw_total": 2000,
                  "recorded_git": 2000, "unique_total": 2000, "inflation": 0, "shared_count": 0, "commit_refs": 20,
                  "unique_commits": 20, "missing_commits": [], "mismatches": [], "shared_top": [], "vendored_commit": None},
        "kind_rules": {"src": "code files", "tests": "test files", "docs": "docs", "vendored": "deps", "other": "rest"},
        "tokens": {"input": 0, "output": 0, "reasoning": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
        "date_range": ["2026-01-01", "2026-03-01"], "partial": [],
        "forecast": {"n": 0, "exact": 0, "within1": 0, "under": 0, "over": 0, "mean_abs_steps": None, "bias": None,
                     "errors": {}, "grid": {"est": [], "sp": [], "counts": []}, "rows": []}}


def _all_text(result):
    return " ".join(list(result["html"].values()) + list(result["notes"].values()))


def _graded(data, n, within1, exact=None):
    data["rows"][0]["est"] = 1
    data["forecast"].update({"n": n, "exact": within1 if exact is None else exact, "within1": within1, "under": n - within1,
                             "over": 0, "mean_abs_steps": 0.5, "bias": 0.5,
                             "rows": [{"label": "F0", "est": 1, "sp": 2, "steps": 1, "estimated_at": None, "by": None}]})


class MarkersTest(unittest.TestCase):
    def test_every_marker_and_note_is_produced(self):
        result = texts(make_data())
        self.assertEqual(set(MARKERS), set(result["html"]))
        self.assertEqual({key for key, _ in METRICS}, set(result["notes"]))

    def test_all_null_statistics_degrade_without_none_or_nan(self):
        data = make_data(rho=None)
        data["fit"].update({"n": 0, "exact": 0, "within1": 0})
        data["date_range"] = None
        data["lines"].update({"recorded_git": 0, "unique_total": 0, "cats": dict.fromkeys(data["lines"]["cats"], 0)})
        data["base_sp"] = None
        data["pred"]["code"] = {"exp": 1.6, "a": 0, "r2": 1, "a2": 0, "b2": 1, "r2rev": 1, "exact": 1, "within1": 1, "n": 6}
        text = _all_text(texts(data))
        self.assertNotIn("None", text)
        self.assertNotIn("nan", text)
        self.assertIn("Not enough data", texts(data)["html"]["verdict_tag"])

    def test_feature_labels_are_escaped(self):
        data = make_data()
        data["rows"][5]["label"] = "<script>&"
        data["rows"][5]["vendored"] = 500
        result = texts(data)
        self.assertIn("&lt;script&gt;&amp;", result["html"]["findings"])
        self.assertNotIn("<script>", _all_text(result))

    def test_footer_and_eyebrow_carry_project_and_dates(self):
        data = make_data()
        data["project"] = "a&b"
        result = texts(data)["html"]
        self.assertIn("a&amp;b · ", result["eyebrow"])
        self.assertIn("2026-01-01 → 2026-03-01", result["eyebrow"])
        self.assertIn("on 2026-11-05", result["footer"])


class VerdictTest(unittest.TestCase):
    def test_zero_forecasts_say_untested_and_explain(self):
        result = texts(make_data())["html"]
        self.assertIn("Consistent, not yet tested as a forecast", result["verdict_tag"])
        self.assertIn("never been tested", result["verdict_big"])
        self.assertIn("No forecasts to grade", result["catches"])
        self.assertIn("/estimate &lt;file&gt;", result["forecast_sub"])
        self.assertEqual("", result["forecast_points"])
        self.assertIn("There's a catch, though: init calculated these SP values from the same planned hours, or placed them by "
                      "comparison after delivery.", result["lede"])

    def test_the_circular_catch_says_init_calculates_from_hours_and_places_the_rest_by_comparison(self):
        catches = texts(make_data())["html"]["catches"]
        self.assertIn("<code>/estimate init</code> step 6 calculates the SP of every feature with planned hours from those hours "
                      "(the anchor, 13 SP = 75 h by default) and places the others by comparison with picked references, so the "
                      "correlation with planned hours is built in. It shows consistency, not prediction.", catches)
        self.assertNotIn("agent time", catches)
        self.assertNotIn("real size", catches)

    def test_graded_forecasts_drive_the_verdict(self):
        data = make_data()
        _graded(data, 5, 4)
        result = texts(data)["html"]
        self.assertIn('class="tag good"', result["verdict_tag"])
        self.assertIn("80%", result["verdict_big"])
        self.assertIn('href="#forecast"', result["catches"])
        self.assertIn("5 forecasts graded", result["catches"])
        self.assertIn("<li>", result["forecast_points"])
        self.assertIn("<code>F0</code>", result["forecast_points"])
        self.assertNotIn("<i>after</i>", result["lede"])
        self.assertIn("80%", result["lede"])

    def test_within_one_share_bands(self):
        for within1, kind in ((4, "good"), (3, "warn"), (2, "bad")):
            data = make_data()
            _graded(data, 5, within1)
            self.assertIn('class="tag %s"' % kind, texts(data)["html"]["verdict_tag"])

    def test_four_forecasts_fall_back_to_rho(self):
        data = make_data()
        _graded(data, 4, 4)
        result = texts(data)["html"]
        self.assertIn("Consistent", result["verdict_tag"])
        self.assertIn("only 4 forecasts", result["verdict_big"])

    def test_weak_rho_tag(self):
        result = texts(make_data(rho=0.49))["html"]
        self.assertIn('class="tag bad"', result["verdict_tag"])
        self.assertIn("Weak link", result["verdict_tag"])
        moderate = texts(make_data(rho=0.5))["html"]["verdict_tag"]
        self.assertIn('class="tag warn"', moderate)
        self.assertIn("Partly consistent", moderate)

    def test_catch_count_in_tag(self):
        self.assertIn("with two catches", texts(make_data())["html"]["verdict_tag"])

    def test_overlap_catch_threshold(self):
        data = make_data()
        data["sep"]["phases"] = {"3→5": [0.699, 3, 3]}
        result = texts(data)["html"]
        self.assertIn("a 5 has more phases than a 3 only <b>70%</b>", result["catches"])
        data["sep"]["phases"] = {"3→5": [0.7, 3, 3]}
        self.assertNotIn("Neighbouring", texts(data)["html"]["catches"])
        data["sep"]["phases"] = {"3→5": [0.5, 2, 3]}
        self.assertNotIn("Neighbouring", texts(data)["html"]["catches"])

    def test_code_median_inversion(self):
        data = make_data()
        data["bstats"]["code"] = {"3": {"n": 2, "min": 1, "q1": 1, "med": 5886, "q3": 1, "max": 1},
                                  "5": {"n": 2, "min": 1, "q1": 1, "med": 5327, "q3": 1, "max": 1}}
        self.assertIn("(5,327 vs. 5,886 lines)", texts(data)["html"]["catches"])


class FindingsTest(unittest.TestCase):
    def test_best_metric_and_hours_per_sp(self):
        data = make_data()
        data["res"]["hours"]["spearman"] = 0.95
        data["bstats"]["hours"] = {"2": {"n": 3, "min": 1, "q1": 1, "med": 12, "q3": 1, "max": 1},
                                   "5": {"n": 2, "min": 1, "q1": 1, "med": 30, "q3": 1, "max": 1}}
        findings = texts(data)["html"]["findings"]
        self.assertIn("<b>Planned hours are the best match for SP</b>: ρ 0.95 (n=6)", findings)
        self.assertIn("At SP 2 the median of planned hours is 6.0 per SP.", findings)

    def test_the_hours_per_sp_sentence_names_planned_hours_when_another_metric_is_the_best_match(self):
        data = make_data()
        data["res"]["churn"]["spearman"] = 0.95
        data["bstats"]["hours"] = {sp: {"n": 3, "min": 1, "q1": 1, "med": hours, "q3": 1, "max": 1}
                                   for sp, hours in (("2", 12), ("5", 30))}
        findings = texts(data)["html"]["findings"]
        self.assertIn("<b>Recorded lines are the best match for SP</b>: ρ 0.95 (n=6), and 90% of pairs are in the same order. "
                      "In the SP 2–5 range the median of planned hours is <b>~6 per SP</b> (6.0 / 6.0 h per SP at SP 2 / 5).",
                      findings)
        self.assertNotIn("the median is", findings)

    def test_code_gain_threshold(self):
        data = make_data()
        data["res"]["churn"]["spearman"] = 0.8
        self.assertIn("once the noise is removed", texts(data)["html"]["findings"])
        self.assertIn("Raw lines changed look weaker (ρ 0.80)", texts(data)["html"]["lede"])
        data["res"]["churn"]["spearman"] = 0.81
        self.assertIn("makes little difference", texts(data)["html"]["findings"])
        self.assertNotIn("look weaker", texts(data)["html"]["lede"])

    def test_double_count_threshold(self):
        data = make_data()
        data["lines"].update({"recorded_git": 1000, "inflation": 49, "shared_count": 3})
        self.assertNotIn("counted more than once", texts(data)["html"]["findings"])
        data["lines"]["inflation"] = 50
        data["lines"]["shared_top"] = [{"hash": "eaebda1a", "n": 12, "subj": "s", "lines": 8805, "date": "d",
                                        "labels": ["F0", "F1"]}]
        findings = texts(data)["html"]["findings"]
        self.assertIn("<b>5% of the recorded lines are counted more than once.</b> 3 commits are", findings)
        self.assertIn("(<code>eaebda1a</code>, 8,805 lines) is counted in 12 features", findings)

    def test_dependency_lines_threshold(self):
        data = make_data()
        data["rows"][5]["vendored"] = 499  # F5 counts 500 lines: dependency lines below half of 500 + 499
        self.assertNotIn("committed dependencies", texts(data)["html"]["findings"])
        data["rows"][5]["vendored"] = 500
        data["lines"]["vendored_commit"] = {"hash": "c4b8e6dd", "subj": "s", "lines": 500, "labels": ["F5"]}
        self.assertIn("<li><code>F5</code>'s commits also hold <b>500 lines of committed dependencies</b> (commit "
                      "<code>c4b8e6dd</code>), which are not counted.</li>", texts(data)["html"]["findings"])
        data["lines"]["vendored_commit"]["labels"] = ["F4"]
        self.assertIn("<li><code>F5</code>'s commits also hold <b>500 lines of committed dependencies</b>, which are not "
                      "counted.</li>", texts(data)["html"]["findings"])

    def test_the_dependency_share_uses_the_lines_git_counts_not_stale_stored_ones(self):
        data = make_data()
        data["rows"][5].update({"churn": 178325, "git_churn": 7322, "vendored": 171003})  # stored before the fix
        self.assertIn("also hold <b>171,003 lines of committed dependencies</b>", texts(data)["html"]["findings"])

    def test_growth_and_scaling(self):
        data = make_data()
        for key, exp in (("tasks", 0.6), ("hours", 1.02), ("code", 1.6), ("churn", 1.0), ("src", 2.0), ("commits", 1.0)):
            data["pred"][key] = {"exp": exp, "a": 0, "r2": 1, "a2": 0, "b2": 1, "r2rev": 1, "exact": 1, "within1": 1, "n": 6}
        data["rows"][3]["sp"] = 5
        data["res"]["churn"]["spearman"] = 0.69
        data["res"]["phases"]["spearman"] = 0.5
        data["pred"]["phases"] = dict(data["pred"]["tasks"], exp=2.5)
        result = texts(data)["html"]
        self.assertIn("tasks grow ×1.52", result["findings"])
        self.assertIn("SP is close to linear in planned hours, and code lines grow faster than SP.", result["findings"])
        self.assertIn("<b>planned-hours scale</b> (exponent ≈1.02)", result["scaling_callout"])
        self.assertIn("a 5 is 1.7× a 3 in SP but ~2.3× in typical code lines", result["scaling_callout"])
        self.assertIn("Tasks grow slower than SP, so plans for big features have bigger tasks", result["scaling_callout"])

    def test_weakest_threshold(self):
        data = make_data()
        data["res"]["phases"]["spearman"] = 0.7
        data["res"]["churn"]["spearman"] = 0.3
        self.assertNotIn("tell you little", texts(data)["html"]["findings"])
        self.assertNotIn("weakest link", texts(data)["html"]["findings"])
        data["res"]["phases"]["spearman"] = 0.69
        self.assertIn("<b>Phases are the weakest link to size</b> (ρ 0.69).", texts(data)["html"]["findings"])
        data["res"]["phases"]["spearman"] = 0.4
        self.assertIn("<b>Phases tell you little about size</b>", texts(data)["html"]["findings"])
        self.assertIn("Most features have 4 phases at any size.", texts(data)["html"]["findings"])

    def test_commits_per_task_rule(self):
        data = make_data()
        self.assertIn("median 1.00 (IQR 1.00–1.00), so the one-commit-per-task rule is followed",
                      texts(data)["html"]["findings"])
        for row in data["rows"]:
            row["commits_excl"] = row["tasks"] * 2
        self.assertIn("do not map one-to-one", texts(data)["html"]["findings"])

    def test_combined_fit(self):
        data = make_data()
        data["fit"].update({"exact": 4, "within1": 6, "max_steps": 1})
        findings = texts(data)["html"]["findings"]
        self.assertIn("exact SP for 4 of 6 features (67%), and all 6 are within ±1", findings)
        data["fit"]["within1"] = 5
        self.assertIn("(67%), and 5 are within ±1", texts(data)["html"]["findings"])
        self.assertIn("Always answering \"2\" scores 33% / 67%", findings)
        self.assertIn("The 2 features that are one step off are listed", findings)


class SectionsTest(unittest.TestCase):
    def test_no_shared_commits_leave_callout_empty(self):
        self.assertEqual("", texts(make_data())["html"]["quality_callout"])

    def test_shared_callout(self):
        data = make_data()
        data["lines"]["shared_top"] = [{"hash": "eaebda1a", "n": 2, "subj": "s", "lines": 10, "date": "d",
                                        "labels": ["F4", "F5"]}]
        self.assertIn("<code>F4</code> and <code>F5</code> share <code>eaebda1a</code> and each record 500 lines. "
                      "<code>F2</code> records 300 lines but has only 240 lines of code.",
                      texts(data)["html"]["quality_callout"])

    def test_quality_sub_counts(self):
        data = make_data()
        self.assertIn("all 6 match exactly", texts(data)["html"]["quality_sub"])
        data["lines"]["mismatches"] = [{"label": "F0", "stored": 1, "git": 2, "missing": 1}]
        data["lines"]["missing_commits"] = ["abcd1234"]
        self.assertIn("1 of 6 differ (1 listed commit git does not hold)", texts(data)["html"]["quality_sub"])
        self.assertIn("of the feature's listed commits without committed dependency files", texts(data)["html"]["quality_sub"])

    def test_method_says_lines_changed_leave_out_dependency_files_and_names_the_rule(self):
        method = texts(make_data())["html"]["method"]
        self.assertIn("<b>Lines changed</b> (stored and recomputed from git) leave out committed dependency files (deps). The "
                      "report shows those lines apart as <b>Dependencies (not counted)</b>.", method)
        self.assertIn("<b>Dependencies (not counted)</b> = deps.", method)

    def test_spcore_note(self):
        data = make_data()
        self.assertIn("not shown", texts(data)["html"]["spcore_note"])
        data["core"] = {"max_sp": 3, "dropped": [5], "n_dropped": 2}
        self.assertIn("without the two largest features (5, 5 SP)", texts(data)["html"]["spcore_note"])
        self.assertIn("They are not.", texts(data)["html"]["spcore_note"])
        data["res"]["code"]["spearman_core"] = 0.8
        self.assertIn("They do drive it for code lines (ρ 0.90 → 0.80).", texts(data)["html"]["spcore_note"])

    def test_outliers_sub(self):
        data = make_data()
        self.assertIn("nothing to re-size", texts(data)["html"]["outliers_sub"])
        data["rows"][0]["resid"] = 1
        data["rows"][1]["resid"] = -1
        data["fit"]["max_steps"] = 1
        self.assertIn("None is further off", texts(data)["html"]["outliers_sub"])

    def test_usage(self):
        data = make_data()
        self.assertEqual("Agent usage (no features)", texts(data)["html"]["usage_title"])
        self.assertIn("No feature has matched", texts(data)["html"]["usage_points"])
        for index in range(3):
            data["rows"][index].update({"sessions": 1, "agent_min": 60, "hours": 6, "cost": 20, "tokens": 100,
                                        "cache_read": 90, "matched": "prompt"})
        data["rows"][2]["cost"] = 150
        data["tokens"].update({"total": 200, "cacheRead": 180})
        result = texts(data)["html"]
        self.assertEqual("Agent usage (3 features only)", result["usage_title"])
        self.assertIn("Only 3 of 6 features have matched agent sessions. 3 have", result["usage_sub"])
        self.assertIn("<b>6.0×</b> the recorded agent time for the 3 features", result["usage_points"])
        self.assertIn("2 features cost <b>~$10 per SP</b> (2 and 2 SP).", result["usage_points"])
        self.assertIn("<b>90%</b> of all tokens are cache reads", result["usage_points"])
        self.assertIn("<code>F2</code> cost <b>$50.0 per SP</b>, with 90% of its tokens cache reads", result["usage_points"])

    def test_spread_costs_per_sp_give_their_median_and_range(self):
        data = make_data()
        for index, cost in enumerate((10, 16, 30)):  # SP 2, 2, 3 → $5, $8, $10 per SP
            data["rows"][index].update({"sessions": 1, "agent_min": 60, "cost": cost, "tokens": 100, "matched": "prompt"})
        self.assertIn("Cost per SP has a median of <b>~$8</b> across 3 features ($5.0–$10.0 per SP; 2, 2 and 3 SP).",
                      texts(data)["html"]["usage_points"])

    def test_partial_rows_named(self):
        data = make_data()
        data["rows"][0].update({"sessions": 1, "agent_min": 46, "hours": 139})
        data["partial"] = ["F0"]
        self.assertIn("<code>F0</code> has 1 session of 46 minutes against 139 planned hours",
                      texts(data)["html"]["usage_points"])

    def test_metric_note(self):
        data = make_data()
        data["bstats"]["tasks"] = {"2": {"n": 3, "min": 2, "q1": 3, "med": 4, "q3": 4, "max": 20}}
        data["rows"][0]["tasks"] = 20
        data["rows"][1]["tasks"] = 0
        data["sep"]["tasks"] = {"2→3": [0.8, 3, 3], "3→5": [0.96, 3, 3], "5→8": [0.99, 2, 3]}
        note = texts(data)["notes"]["tasks"]
        self.assertIn("ρ 0.90. 90% of feature pairs", note)
        self.assertIn("SP-2 features range from 2 to 20 tasks.", note)
        self.assertIn("The SP 3 → 5 split is the cleanest (AUC 0.96).", note)
        self.assertIn("The SP-2 outlier at 20 tasks is <code>F0</code>.", note)
        self.assertIn("1 feature has no tasks (hidden on this log axis).", note)

    def test_line_metric_notes_print_whole_lines(self):
        data = make_data()
        data["bstats"]["src"] = {"2": {"n": 3, "min": 2.4, "q1": 3, "med": 100, "q3": 4, "max": 1501.2}}
        data["rows"][0]["src"] = 392.5
        note = texts(data)["notes"]["src"]
        self.assertIn("SP-2 features range from 2 to 1,501 source lines.", note)
        self.assertIn("The SP-2 outlier at 393 source lines is <code>F0</code>.", note)


if __name__ == "__main__":
    unittest.main()
