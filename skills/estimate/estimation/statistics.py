"""The report's pure statistics on plain lists (standard library only). A statistic that cannot be computed (too few pairs, a
constant side, no comparable pair, an empty side) is None, never an exception: the report shows such cells as "—".
Quantiles interpolate linearly between the closest ranks (numpy's default), so box summaries match spreadsheet and numpy
output."""
import math
import random

MIN_PAIRS = 3
HALF = 0.5
LOG_TIE_TOLERANCE = 1e-12  # log distances closer than this are a tie (√2 sits exactly between 1 and 2)


def average_ranks(values):
    """1-based ranks of `values` in their order; tied values share the average of the ranks they span."""
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        rank = (start + end) / 2 + 1
        for position in range(start, end + 1):
            ranks[order[position]] = rank
        start = end + 1
    return ranks


def pearson(xs, ys):
    """Pearson correlation; None below MIN_PAIRS pairs or when a side is constant."""
    if len(xs) < MIN_PAIRS:
        return None
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    sxx, syy = sum(d * d for d in dx), sum(d * d for d in dy)
    if sxx == 0 or syy == 0:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / math.sqrt(sxx * syy)


def spearman(xs, ys):
    """Spearman's ρ: the Pearson correlation of the average ranks."""
    if len(xs) < MIN_PAIRS:
        return None
    return pearson(average_ranks(xs), average_ranks(ys))


def concordance(sizes, values):
    """Of the pairs with different sizes, the share whose larger size has the larger value (a tie in values counts half)."""
    agreeing = pairs = 0.0
    for first in range(len(sizes)):
        for second in range(first + 1, len(sizes)):
            if sizes[first] == sizes[second]:
                continue
            pairs += 1
            if values[first] == values[second]:
                agreeing += HALF
            elif (sizes[first] < sizes[second]) == (values[first] < values[second]):
                agreeing += 1
    return agreeing / pairs if pairs else None


def separation(lower, higher):
    """AUC of two buckets: P(higher value > lower value) + ½·P(equal) over every (lower, higher) pair; None with an empty side."""
    if not lower or not higher:
        return None
    score = sum(1.0 if high > low else HALF if high == low else 0.0 for low in lower for high in higher)
    return score / (len(lower) * len(higher))


def quantile(sorted_values, q):
    """The q-quantile of ascending `sorted_values` by linear interpolation between closest ranks (numpy's default)."""
    position = (len(sorted_values) - 1) * q
    below = math.floor(position)
    above = min(below + 1, len(sorted_values) - 1)
    return sorted_values[below] + (sorted_values[above] - sorted_values[below]) * (position - below)


QUARTILES = {"q1": 0.25, "med": 0.5, "q3": 0.75}


def box(values):
    """{"n", "min", "q1", "med", "q3", "max"} of non-empty `values`."""
    ordered = sorted(values)
    summary = {"n": len(ordered), "min": ordered[0]}
    summary.update({key: quantile(ordered, q) for key, q in QUARTILES.items()})
    summary["max"] = ordered[-1]
    return summary


def median(values):
    return quantile(sorted(values), QUARTILES["med"])


def least_squares(xs, ys):
    """(intercept, slope, r²) of the ordinary least-squares line y = intercept + slope·x; None below MIN_PAIRS points or when x is
    constant. r² is 1 when y is constant (the line explains all of no variance)."""
    if len(xs) < MIN_PAIRS:
        return None
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sxx
    intercept = mean_y - slope * mean_x
    total = sum((y - mean_y) ** 2 for y in ys)
    residual = sum((y - intercept - slope * x) ** 2 for x, y in zip(xs, ys))
    return intercept, slope, 1.0 if total == 0 else 1 - residual / total


def bootstrap_interval(xs, ys, statistic, resamples, seed, level):
    """The percentile interval at `level` of `statistic` over `resamples` draws (with replacement, `random.Random(seed)`) of the
    pairs; draws whose statistic is None are dropped, and the interval is None when fewer than half the draws gave a value."""
    generator = random.Random(seed)
    count = len(xs)
    results = []
    for _ in range(resamples):
        picks = [generator.randrange(count) for _ in range(count)] if count else []
        value = statistic([xs[index] for index in picks], [ys[index] for index in picks])
        if value is not None:
            results.append(value)
    if len(results) * 2 < resamples or not results:
        return None
    results.sort()
    return quantile(results, (1 - level) / 2), quantile(results, (1 + level) / 2)


def nearest_step(scale, value):
    """The index in `scale` of the positive scale value nearest to positive `value` in log space; a tie takes the smaller
    value."""
    target = math.log(value)
    best = None
    for index, step in enumerate(scale):
        if step <= 0:
            continue
        distance = abs(math.log(step) - target)
        if best is None or distance < best[0] - LOG_TIE_TOLERANCE or (
                abs(distance - best[0]) <= LOG_TIE_TOLERANCE and step < scale[best[1]]):
            best = (distance, index)
    return best[1]
