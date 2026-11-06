"""`analyse(document, changes, project, today)`: the report's DATA from the stored features and the git changes of the commits the
reference features list — the reference rows (exclusive commits, lines split 1/k per kind over the features sharing a commit,
the git check of stored lines without dependency files), the per-metric statistics, the core and baseline rules, the combined fit,
the line totals, the palette, the partial-session rule and the forecast grading; a non-numeric scale or no reference feature is
refused."""
import unittest
from datetime import date, datetime

from estimation.errors import EstimateError
from estimation.git_history import CommitChanges, FileChange
from estimation.line_kinds import RULES
from estimation.report_data import analyse
from tests.support import DEFAULT_SCALE, document, new_feature

TODAY = date(2026, 11, 5)
PROJECT = "demo"


def full_hash(number):
    return "%040x" % number


def change(number, files, day="2026-10-01", subject=None):
    """CommitChanges of commit `number` with `files` = {path: (added, removed)}."""
    return CommitChanges(full_hash(number), datetime.fromisoformat(day + "T12:00:00+00:00"), subject or "commit %d" % number,
                         tuple(FileChange(path, added, removed) for path, (added, removed) in files.items()))


def reference(name, sp, commits=(), lines=(0, 0), feature_id=None, **fields):
    """A delivered feature with after-delivery size `sp` listing commits `commits` (numbers), its stored git counts `lines`."""
    return new_feature(name, id=feature_id, status="delivered", updatedStoryPoints=sp,
                       git={"commits": len(commits), "linesAdded": lines[0], "linesRemoved": lines[1]},
                       sources={"docs": [], "commits": [full_hash(number) for number in commits], "sessions": []}, **fields)


def plan(tasks=None, hours=None, phases=None):
    return {"phases": phases, "tasks": tasks, "estimatedHours": hours}


def usage(sessions, minutes, cost=None, total=None, cache_read=None, matched="prompt"):
    tokens = {"input": None, "output": None, "reasoning": None, "cacheRead": cache_read, "cacheWrite": None, "total": total}
    return {"matchedBy": matched, "sessions": sessions, "agentTimeMinutes": minutes, "tokens": tokens, "costUsd": cost}


def run(*features, changes=(), scale=None):
    return analyse(document(*features, scale=scale), {item.hash: item for item in changes}, PROJECT, TODAY)


def row(data, name):
    return next(item for item in data["rows"] if item["name"] == name)


class SelectionTest(unittest.TestCase):
    def test_only_references_become_rows_in_store_order(self):
        data = run(reference("Beta", 3, feature_id="B-2"), new_feature("Planned", status="estimated", storyPoints=5),
                   new_feature("Unsized", status="delivered"), reference("Alpha", 5))
        self.assertEqual(["Beta", "Alpha"], [item["name"] for item in data["rows"]])
        self.assertEqual({"stored": 4, "references": 2, "not_delivered": 1, "unsized": 1}, data["counts"])
        self.assertEqual("B-2 · Beta", row(data, "Beta")["label"])
        self.assertEqual("Alpha", row(data, "Alpha")["label"])

    def test_header_fields(self):
        data = run(reference("Alpha", 5))
        self.assertEqual((PROJECT, "2026-11-05", ".harness-goodies/estimations", DEFAULT_SCALE),
                         (data["project"], data["generated_on"], data["store"], data["scale"]))
        self.assertEqual(RULES, data["kind_rules"])

    def test_row_copies_the_stored_fields(self):
        data = run(reference("Alpha", 5, lines=(30, 12), feature_id="A1", description="Does A", storyPoints=3,
                             estimatedAt="2026-09-01", estimatedBy={"harness": "claude", "models": None},
                             plan=plan(tasks=7, hours=12.5, phases=2), usage=usage(2, 90, cost=4.5, total=1000, cache_read=800)))
        alpha = row(data, "Alpha")
        self.assertEqual({"id": "A1", "desc": "Does A", "sp": 5, "est": 3, "estimated_at": "2026-09-01", "estimated_by": "claude",
                          "phases": 2, "tasks": 7, "hours": 12.5, "added": 30, "removed": 12, "churn": 42, "sessions": 2,
                          "agent_min": 90, "cost": 4.5, "tokens": 1000, "cache_read": 800, "matched": "prompt"},
                         {key: alpha[key] for key in ("id", "desc", "sp", "est", "estimated_at", "estimated_by", "phases",
                                                      "tasks", "hours", "added", "removed", "churn", "sessions", "agent_min",
                                                      "cost", "tokens", "cache_read", "matched")})

    def test_a_feature_without_usage_has_no_sessions(self):
        alpha = row(run(reference("Alpha", 5)), "Alpha")
        self.assertEqual((0, None, None, None, None, "none", None),
                         tuple(alpha[key] for key in ("sessions", "agent_min", "cost", "tokens", "cache_read", "matched", "est")))

    def test_no_reference_is_refused(self):
        with self.assertRaisesRegex(EstimateError, "no delivered feature has an after-delivery SP; run /estimate init"):
            run(new_feature("Unsized", status="delivered"))

    def test_a_non_numeric_scale_is_refused(self):
        for scale in (["XS", "S", "M"], [1, True, 3]):
            with self.subTest(scale=scale), self.assertRaisesRegex(EstimateError, "report needs a numeric scale"):
                run(reference("Alpha", 1), scale=scale)


class PendingTest(unittest.TestCase):
    def setUp(self):
        self.data = run(reference("Done", 5, plan=plan(tasks=2)),
                        new_feature("Planned", id="P-1", status="estimated", storyPoints=8, estimatedAt="2026-10-01",
                                    estimatedBy={"harness": "omp", "models": None}, plan=plan(tasks=6, hours=9.5, phases=2)),
                        new_feature("Running", status="in-progress", storyPoints=3, plan=plan(tasks=2)),
                        new_feature("Unsized", status="delivered", storyPoints=13),
                        new_feature("Unestimated", status="estimated"))

    def test_features_with_only_an_estimate_are_listed_in_store_order_at_their_estimated_size(self):
        self.assertEqual([("Planned", "estimated", 8), ("Running", "in-progress", 3), ("Unsized", "delivered", 13)],
                         [(item["name"], item["status"], item["sp"]) for item in self.data["pending"]])

    def test_a_pending_row_carries_the_plan_values_and_no_delivered_actuals(self):
        planned = self.data["pending"][0]
        self.assertEqual(("P-1 · Planned", "About Planned", 2, 6, 9.5, "2026-10-01", "omp"),
                         tuple(planned[key] for key in ("label", "desc", "phases", "tasks", "hours", "estimated_at", "estimated_by")))
        self.assertNotIn("commits", planned)
        self.assertNotIn("code", planned)

    def test_pending_features_stay_out_of_the_statistics(self):
        self.assertEqual(["Done"], [item["name"] for item in self.data["rows"]])
        self.assertEqual(1, self.data["res"]["tasks"]["n"])
        self.assertEqual(["5"], list(self.data["bstats"]["tasks"]))

    def test_pending_sizes_get_palette_colors(self):
        self.assertEqual({"3", "5", "8", "13"}, set(self.data["colors"]))

    def test_no_pending_feature_gives_an_empty_list(self):
        self.assertEqual([], run(reference("Alpha", 5))["pending"])



class LinesTest(unittest.TestCase):
    """Alpha lists commits 1 and 2, Beta lists 2, 3 and a commit git lacks; commit 2 is shared (k = 2) and holds a lockfile, whose
    lines are dependency lines: kept per kind, never counted."""

    def setUp(self):
        self.changes = [change(1, {"tests/test_a.py": (6, 0), "README.md": (2, 0)}, day="2026-09-03"),
                        change(2, {"src/app.py": (8, 2), "docs/guide.md": (4, 0), "yarn.lock": (100, 0)}, day="2026-09-05",
                               subject="shared work"),
                        change(3, {"setup.cfg": (1, 1), "src/b.py": (3, 0)}, day="2026-09-09")]
        self.data = run(reference("Alpha", 3, commits=(1, 2), lines=(20, 2)),
                        reference("Beta", 5, commits=(2, 3, 99), lines=(10, 1)), changes=self.changes)

    def test_each_kind_is_split_over_the_features_sharing_a_commit(self):
        alpha, beta = row(self.data, "Alpha"), row(self.data, "Beta")
        self.assertEqual({"src": 5.0, "tests": 6.0, "docs": 4.0, "vendored": 50.0, "other": 0.0, "code": 11.0},
                         {key: alpha[key] for key in ("src", "tests", "docs", "vendored", "other", "code")})
        self.assertEqual({"src": 8.0, "tests": 0.0, "docs": 2.0, "vendored": 50.0, "other": 2.0, "code": 8.0},
                         {key: beta[key] for key in ("src", "tests", "docs", "vendored", "other", "code")})

    def test_exclusive_commits_git_churn_and_missing(self):
        alpha, beta = row(self.data, "Alpha"), row(self.data, "Beta")
        # git_churn leaves the lockfile's 100 lines out: Alpha 8 + 14, Beta 14 + 5.
        self.assertEqual((1, 22, 0), (alpha["commits_excl"], alpha["git_churn"], alpha["missing"]))
        self.assertEqual((2, 19, 1), (beta["commits_excl"], beta["git_churn"], beta["missing"]))

    def test_first_and_last_days_come_from_the_commits_git_holds(self):
        self.assertEqual(("2026-09-03", "2026-09-05"), (row(self.data, "Alpha")["first"], row(self.data, "Alpha")["last"]))
        self.assertEqual(("2026-09-05", "2026-09-09"), (row(self.data, "Beta")["first"], row(self.data, "Beta")["last"]))
        self.assertEqual(["2026-09-03", "2026-09-09"], self.data["date_range"])

    def test_totals_count_each_commit_once(self):
        lines = self.data["lines"]
        self.assertEqual({"src": 13, "tests": 6, "docs": 6, "vendored": 100, "other": 2}, lines["cats"])
        self.assertEqual((33, 41, 27, 14, 1, 5, 4), (lines["raw_total"], lines["recorded_git"], lines["unique_total"],
                                                    lines["inflation"], lines["shared_count"], lines["commit_refs"],
                                                    lines["unique_commits"]))
        self.assertEqual([full_hash(99)[:8]], lines["missing_commits"])

    def test_stored_lines_without_dependency_files_match_git(self):
        self.assertEqual([{"label": "Beta", "stored": 11, "git": 19, "missing": 1}], self.data["lines"]["mismatches"])

    def test_shared_and_vendored_commits(self):
        lines = self.data["lines"]
        self.assertEqual([{"hash": full_hash(2)[:8], "n": 2, "subj": "shared work", "lines": 14, "date": "2026-09-05",
                           "labels": ["Alpha", "Beta"]}], lines["shared_top"])
        self.assertEqual({"hash": full_hash(2)[:8], "subj": "shared work", "lines": 100, "labels": ["Alpha", "Beta"]},
                         lines["vendored_commit"])

    def test_shared_commits_are_ordered_by_the_lines_they_double_count(self):
        changes = [change(1, {"a.py": (10, 0)}), change(2, {"b.py": (100, 0)}), change(3, {"c.py": (50, 0)}),
                   change(4, {"d.py": (25, 0)})]
        data = run(reference("A", 3, commits=(1, 2, 3, 4)), reference("B", 3, commits=(1, 2, 3, 4)),
                   reference("C", 3, commits=(1, 4)), changes=changes)
        # double-counted (n − 1) × lines: commit 2 → 100, commit 4 → 50 (n 3), commit 3 → 50 (n 2), commit 1 → 20
        self.assertEqual([(2, 100), (3, 25), (2, 50), (3, 10)],
                         [(item["n"], item["lines"]) for item in data["lines"]["shared_top"]])

    def test_no_vendored_lines_and_no_dates(self):
        data = run(reference("Alpha", 3))
        self.assertIsNone(data["lines"]["vendored_commit"])
        self.assertIsNone(data["date_range"])
        self.assertEqual((None, None), (data["rows"][0]["first"], data["rows"][0]["last"]))


def sized(sps, **metrics):
    """References named R0, R1, … with the given SPs and, per metric (tasks, hours, phases), one value per row."""
    return [reference("R%d" % index, sp, plan=plan(**{key: values[index] for key, values in metrics.items()}))
            for index, sp in enumerate(sps)]


class MetricStatisticsTest(unittest.TestCase):
    def setUp(self):
        self.data = run(*sized([2, 2, 3, 5, 5, 8], tasks=[2, 3, 4, 6, 7, 9], hours=[4, None, 6, None, 10, 16]))

    def test_every_metric_has_its_statistics(self):
        self.assertEqual(["tasks", "hours", "commits_excl", "code", "phases", "churn", "commits", "src"],
                         [key for key, _ in self.data["metrics"]])
        self.assertEqual(set(key for key, _ in self.data["metrics"]), set(self.data["res"]))
        tasks = self.data["res"]["tasks"]
        self.assertEqual(("Tasks", 6, 0.971, 1.0), (tasks["label"], tasks["n"], tasks["spearman"], tasks["conc"]))
        self.assertEqual(4, self.data["res"]["hours"]["n"])

    def test_a_metric_without_values_has_no_statistics(self):
        phases = self.data["res"]["phases"]
        self.assertEqual((0, None, None, None, None), (phases["n"], phases["spearman"], phases["ci"], phases["pearson"],
                                                       phases["conc"]))
        self.assertIsNone(self.data["pred"]["phases"])
        self.assertEqual({}, self.data["bstats"]["phases"])

    def test_box_per_sp_bucket(self):
        self.assertEqual({"2": {"n": 2, "min": 2, "q1": 2.25, "med": 2.5, "q3": 2.75, "max": 3}},
                         {key: value for key, value in self.data["bstats"]["tasks"].items() if key == "2"})
        self.assertEqual(["2", "3", "5", "8"], list(self.data["bstats"]["tasks"]))

    def test_separation_of_consecutive_buckets_holding_values(self):
        self.assertEqual({"2→3": [1.0, 1, 1], "3→5": [1.0, 1, 1], "5→8": [1.0, 1, 1]}, self.data["sep"]["hours"])
        self.assertEqual([1.0, 2, 1], self.data["sep"]["tasks"]["2→3"])

    def test_correlation_matrix_over_pairs_with_both_values(self):
        names = [key for key, _ in self.data["mvars"]]
        sp, hours, tasks = names.index("sp"), names.index("hours"), names.index("tasks")
        self.assertEqual(1.0, self.data["corr"][hours][hours])
        self.assertEqual(4, self.data["corrn"][sp][hours])
        self.assertEqual(6, self.data["corrn"][sp][tasks])
        self.assertEqual(1.0, self.data["corr"][sp][hours])
        self.assertIsNone(self.data["corr"][sp][names.index("phases")])

    def test_power_fit_and_single_metric_prediction(self):
        data = run(*sized([1, 2, 4, 8], tasks=[3, 6, 12, 24]), scale=[0, 1, 2, 4, 8, 16])
        tasks = data["pred"]["tasks"]
        self.assertEqual((1.0, 1.0, 1.0, 1.0, 1.0, 4), (tasks["exp"], tasks["r2"], tasks["b2"], tasks["r2rev"], tasks["exact"],
                                                        tasks["n"]))
        self.assertAlmostEqual(-1.0986, tasks["a2"], places=3)


class CoreAndBaselineTest(unittest.TestCase):
    def test_the_largest_buckets_are_dropped_while_they_stay_within_a_tenth(self):
        data = run(*sized([2] * 10 + [5] * 8 + [13, 20]))
        self.assertEqual({"max_sp": 5, "dropped": [13, 20], "n_dropped": 2}, data["core"])
        self.assertEqual(18, data["res"]["churn"]["n_core"])

    def test_no_core_when_the_largest_bucket_is_too_big(self):
        data = run(*sized([2] * 8 + [5] * 2))
        self.assertIsNone(data["core"])
        self.assertEqual((None, None, None), tuple(data["res"]["tasks"][key] for key in ("n_core", "spearman_core", "conc_core")))

    def test_base_sp_is_five_with_three_features_of_five(self):
        self.assertEqual(5, run(*sized([3] * 6 + [5] * 3))["base_sp"])

    def test_base_sp_is_otherwise_the_most_common_positive_sp_smaller_first(self):
        self.assertEqual(3, run(*sized([0] * 9 + [3] * 3 + [5] * 2))["base_sp"])
        self.assertEqual(2, run(*sized([2, 2, 8, 8]))["base_sp"])


class CombinedFitTest(unittest.TestCase):
    def test_a_perfect_relation_fits_every_feature_exactly(self):
        data = run(*sized([1, 2, 2, 3, 5, 5, 8], tasks=[2, 4, 4, 6, 10, 10, 16]))
        for item in data["rows"]:
            self.assertEqual((item["sp"], 0, 0.0), (item["fit_sp"], item["resid"], item["resid_log"]))
            self.assertEqual(DEFAULT_SCALE.index(item["sp"]), item["fit_step"])
        self.assertEqual({"n": 7, "exact": 7, "within1": 7, "max_steps": 0, "baseline_sp": 2, "baseline_exact": 2,
                          "baseline_within1": 4}, data["fit"])

    def test_a_feature_sized_above_its_actuals_has_a_positive_residual(self):
        data = run(*sized([1, 1, 2, 2, 3, 3, 5, 5, 8, 8, 8], tasks=[2, 2, 4, 4, 6, 6, 10, 10, 16, 16, 2]))
        outlier = data["rows"][-1]
        self.assertGreater(outlier["resid"], 0)
        self.assertEqual(DEFAULT_SCALE.index(8) - outlier["fit_step"], outlier["resid"])
        self.assertGreater(outlier["resid_log"], 0)
        self.assertEqual(outlier["resid"], data["fit"]["max_steps"])

    def test_a_feature_without_a_usable_metric_has_no_fit(self):
        data = run(*sized([1, 2, 3, 5], tasks=[2, 4, 6, None]))
        self.assertEqual((None, None, None, None),
                         tuple(data["rows"][-1][key] for key in ("fit_sp", "fit_step", "resid", "resid_log")))
        self.assertEqual(3, data["fit"]["n"])


class ForecastTest(unittest.TestCase):
    def test_steps_errors_and_grid(self):
        data = run(reference("Under", 5, storyPoints=3, estimatedBy={"harness": "codex", "models": None}),
                   reference("Exact", 5, storyPoints=5), reference("Over", 3, storyPoints=8), reference("Unestimated", 2))
        forecast = data["forecast"]
        self.assertEqual((3, 1, 2, 1, 1, 1.0, -0.333), tuple(forecast[key] for key in (
            "n", "exact", "within1", "under", "over", "mean_abs_steps", "bias")))
        self.assertEqual({"-2": 1, "-1": 0, "0": 1, "1": 1}, forecast["errors"])
        self.assertEqual({"est": [3, 5, 8], "sp": [3, 5], "counts": [[0, 1], [0, 1], [1, 0]]}, forecast["grid"])
        self.assertEqual(["Over", "Under", "Exact"], [item["label"] for item in forecast["rows"]])
        self.assertEqual({"label": "Under", "est": 3, "sp": 5, "steps": 1, "estimated_at": None, "by": "codex"},
                         forecast["rows"][1])

    def test_no_forecasts(self):
        forecast = run(reference("Alpha", 5))["forecast"]
        self.assertEqual((0, None, None, {}, []),
                         (forecast["n"], forecast["mean_abs_steps"], forecast["bias"], forecast["errors"], forecast["rows"]))


class UsageAndPaletteTest(unittest.TestCase):
    def test_a_ratio_far_above_the_median_is_partial(self):
        features = [reference("U%d" % index, 3, plan=plan(hours=hours), usage=usage(1, minutes))
                    for index, (hours, minutes) in enumerate([(1, 60), (2, 120), (6, 300), (10, 60)])]
        self.assertEqual(["U3"], run(*features)["partial"])

    def test_no_partial_rule_below_three_usage_rows(self):
        features = [reference("U%d" % index, 3, plan=plan(hours=hours), usage=usage(1, minutes))
                    for index, (hours, minutes) in enumerate([(1, 60), (100, 60)])]
        self.assertEqual([], run(*features)["partial"])

    def test_tokens_add_up_with_unknown_as_zero(self):
        data = run(reference("A", 3, usage=usage(1, 10, total=100, cache_read=40)),
                   reference("B", 3, usage=usage(1, 10, total=50)), reference("C", 3))
        self.assertEqual({"input": 0, "output": 0, "reasoning": 0, "cacheRead": 40, "cacheWrite": 0, "total": 150},
                         data["tokens"])

    def test_one_value_takes_the_middle_anchor(self):
        self.assertEqual({"5": "#8b5cf6"}, run(reference("A", 5))["colors"])

    def test_up_to_nine_values_take_spread_anchors_including_estimates(self):
        data = run(reference("A", 0.5), reference("B", 13, storyPoints=40), reference("C", 5))
        self.assertEqual({"0.5": "#14b8a6", "5": "#6366f1", "13": "#a855f7", "40": "#f43f5e"}, data["colors"])

    def test_more_than_nine_values_interpolate(self):
        scale = list(range(1, 18))
        colors = run(*[reference("S%d" % value, value) for value in scale], scale=scale)["colors"]
        self.assertEqual(17, len(colors))
        self.assertEqual(("#14b8a6", "#0ea5e9", "#f43f5e"), (colors["1"], colors["3"], colors["17"]))
        self.assertEqual("#11afc8", colors["2"])


if __name__ == "__main__":
    unittest.main()
