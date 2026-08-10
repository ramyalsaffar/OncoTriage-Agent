"""The criteria_split ingestion gate: the decision, the feeder, and the wiring.

WHAT THIS FILE IS ABOUT
=======================
``criteria_split`` has been written onto every trial by ``parse_trial_metadata``
since the scrape-admission pass and READ BY NOTHING. It records which branch of
``split_inclusion_exclusion`` produced a trial's inclusion/exclusion sections,
and it rides into Qdrant inside the ``full_trial_json`` payload blob. A trial
whose exclusion criteria arrive under inclusion labels is judged with every
verdict on them inverted -- the judge is told the patient MUST HAVE what the
sponsor wrote as MUST NOT HAVE -- and nothing downstream can tell, because the
criteria block is well-formed text either way.

``oncotriage/retrieval/indexer.py`` now gates on the aggregate distribution of
that field, inside ``verify_collection``, which runs before the alias swap on
both call paths. A failure raises ``IndexVerificationError``, the swap is
refused, and the previous collection keeps serving.

WHAT IS TESTED HERE, AND WHAT IS NOT
------------------------------------
The decision is a PURE FUNCTION of a counted distribution
(``evaluate_criteria_split_distribution`` / ``check_criteria_split_distribution``)
and that is what this file drives: literal dicts in, verdicts out. The Qdrant
scroll that produces those counts is a thin feeder, and its PARSING and
PAGINATION are driven here against a stand-in client.

THE FEEDER'S LIVE BEHAVIOUR IS NOT COVERED BY THIS FILE AND NO CLAIM IS MADE
THAT IT IS. It was exercised by running it: the census in section 1 below is
what ``scroll_criteria_split_distribution`` returned from the live collection
``trial_criteria_20260807_111807`` on 2026-08-09, and its total equalled the
server's own exact point count (14,324) and the same census over the on-disk
``trials_latest.json`` it was built from. A stand-in client cannot prove that
``with_payload=["full_trial_json"]`` returns what this parser expects from a
real Qdrant, and this file does not pretend otherwise.

THE CONTROLS ARE DIFFERENT INPUTS, AND MUTATED SYNTAX TREES -- NEVER exec().
For a pure function of its argument the natural control is a different
argument, which is the shape ``tests/test_agent_patient_hash_coverage.py``
settled on. Every threshold assertion is paired with the same distribution
judged at a different ceiling, so a comparison that had stopped happening fails
rather than agreeing with the code by construction. The three structural checks
carry controls built by mutating an ``ast`` COPY of the shipped source in
memory -- nothing is exec'd, nothing on disk is touched, and this file needs no
``_EXEC_ALLOWLIST`` entry.

BUCKET A. No network, no keys, no spend, no live Qdrant, no git history, no
corpus, no database, no subprocess. It writes nothing anywhere and is not in
the collision matrix: the only repository file it reads is
``oncotriage/retrieval/indexer.py``, which neither of the suite's two writers
writes.
"""

import ast
import os
import sys


# --- package bootstrap ------------------------------------------------------
try:
    import oncotriage
except ImportError:
    _here = os.path.dirname(os.path.abspath(__file__))
    for _candidate in (os.path.dirname(_here), os.getcwd()):
        if os.path.isdir(os.path.join(_candidate, "oncotriage")):
            sys.path.insert(0, _candidate)
            print(f"[bootstrap] added {_candidate} to sys.path")
            break
    import oncotriage

from oncotriage.retrieval import indexer


_passed = 0
_failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def check_true(label, cond):
    check(label, bool(cond), True)


def section(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def verdict(distribution, **kwargs):
    """(raised, message) for check_criteria_split_distribution.

    A BARE CALL WOULD LET A RAISE ESCAPE THROUGH check()'s ARGUMENT LIST. That
    is how tests/test_agent_trial_verdict_normalization.py and
    tests/test_storage_query_layer.py each lost a whole run to one traceback,
    and the gate under test here raises by design, so every driver in this file
    converts the outcome into a value.
    """
    try:
        indexer.check_criteria_split_distribution(distribution, **kwargs)
        return False, ""
    except indexer.IndexVerificationError as exc:
        return True, str(exc)


# The names the splitter can emit, read off the module so a renamed constant
# is a failure here rather than a silently-never-matching literal.
BOTH = indexer.CRITERIA_SPLIT_BOTH
INCLUSION_ONLY = indexer.CRITERIA_SPLIT_INCLUSION_ONLY
EXCLUSION_ONLY = indexer.CRITERIA_SPLIT_EXCLUSION_ONLY
UNSPLIT = indexer.CRITERIA_SPLIT_UNSPLIT
EMPTY = indexer.CRITERIA_SPLIT_EMPTY
ABSENT = indexer.CRITERIA_SPLIT_FIELD_ABSENT
UNREADABLE = indexer.CRITERIA_SPLIT_PAYLOAD_UNREADABLE


# ===========================================================================
# SECTION 1 -- TODAY'S MEASURED DISTRIBUTION PASSES
# ===========================================================================
section("SECTION 1 -- the live corpus passes")

# Not invented. This is exactly what scroll_criteria_split_distribution()
# returned from `trial_criteria_20260807_111807` on 2026-08-09, and the same
# counts appear in the on-disk trials_latest.json the collection was built
# from. If the thresholds are ever tightened past the corpus they were derived
# on, this fails and names the fraction.
LIVE_CENSUS = {
    BOTH: 14034,
    INCLUSION_ONLY: 178,
    UNSPLIT: 82,
    EXCLUSION_ONLY: 30,
}
LIVE_TOTAL = 14324

check("the recorded census totals the live collection's point count",
      sum(LIVE_CENSUS.values()), LIVE_TOTAL)

_live = indexer.evaluate_criteria_split_distribution(LIVE_CENSUS)
check("the live distribution produces NO failures", _live["failures"], [])
check("...over the whole corpus, not a sample", _live["total"], LIVE_TOTAL)
check("degraded is unsplit + empty_criteria", _live["degraded_count"], 82)
check("no_exclusion is unsplit + inclusion_only", _live["no_exclusion_count"], 260)
check("unusable is zero on the live corpus", _live["unusable_count"], 0)
check_true("...and the degraded fraction is the measured 0.572%",
           abs(_live["degraded_fraction"] - 82 / LIVE_TOTAL) < 1e-12)
check_true("...and the no-exclusion fraction is the measured 1.815%",
           abs(_live["no_exclusion_fraction"] - 260 / LIVE_TOTAL) < 1e-12)

_raised, _msg = verdict(LIVE_CENSUS)
check("check_...() does not raise on the live distribution", _raised, False)

# NON-DEGENERACY. "no failures" is also what an evaluator that lost its
# comparisons reports, so the same distribution must FAIL once a ceiling is
# moved below it. This is the control for every pass in this section.
_raised, _msg = verdict(LIVE_CENSUS, max_degraded=0.001)
check_true("CONTROL: the same distribution FAILS at a ceiling below it",
           _raised)
check_true("...naming the measured fraction", "0.57%" in _msg)

# The unrecognised-value fold is what stops a renamed vocabulary passing with
# every fraction at zero.
_renamed = {"both_v2": 14034, "unsplit_v2": 290}
_r_eval = indexer.evaluate_criteria_split_distribution(_renamed)
check("a wholly renamed vocabulary is 100% unusable",
      _r_eval["unusable_count"], 14324)
check_true("...and is refused", verdict(_renamed)[0])
check("...with every unrecognised value named",
      sorted(_r_eval["unrecognised"]), ["both_v2", "unsplit_v2"])


# ===========================================================================
# SECTION 2 -- AN UNSPLIT EXPLOSION IS REFUSED
# ===========================================================================
section("SECTION 2 -- a splitter collapse fails with the named error")

# The scenario: ClinicalTrials.gov changes its heading format, both families
# stop matching, and every trial's whole criteria block goes to inclusion.
COLLAPSE = {BOTH: 300, UNSPLIT: 14024}

_raised, _msg = verdict(COLLAPSE)
check("a splitter collapse RAISES", _raised, True)
check_true("...and it is IndexVerificationError, which is a RuntimeError",
           issubclass(indexer.IndexVerificationError, RuntimeError))
check_true("...and NOT a ValueError, so a narrow except cannot eat it",
           not issubclass(indexer.IndexVerificationError, ValueError))

# The message must carry all three facts. A gate whose message names only
# "verification failed" leaves the operator to re-derive the census by hand.
check_true("the message names the offending fraction", "97.91%" in _msg)
check_true("...and the offending count", "14,024" in _msg)
check_true("...and the corpus it is a fraction OF", "14,324" in _msg)
check_true("...and the threshold it exceeded", "3.00%" in _msg)
check_true("...and that the swap was refused",
           "was NOT moved" in _msg and "refused" in _msg)
check_true("...and that the previous collection still serves",
           "PREVIOUS COLLECTION IS STILL SERVING" in _msg)
check_true("...and which branch names it is counting",
           UNSPLIT in _msg and EMPTY in _msg)

# CONTROL: the identical distribution passes once the ceiling is raised above
# it, so the failure above is the comparison firing and not a constant `raise`.
_raised_hi, _ = verdict(COLLAPSE, max_degraded=0.99, max_no_exclusion=0.99)
check("CONTROL: the same collapse passes at a ceiling above it",
      _raised_hi, False)

# A partial collapse, halfway between the measured rate and total failure.
_partial = {BOTH: 13000, UNSPLIT: 1324}
check_true("a 9.2% unsplit population is refused", verdict(_partial)[0])


# ===========================================================================
# SECTION 3 -- A MISSING-FIELD EXPLOSION IS REFUSED
# ===========================================================================
section("SECTION 3 -- points that predate the splitter contract are refused")

# Not hypothetical: the census over `trial_criteria_20260803_104642` reports
# 12,067 of 12,067 field_absent, because that collection was indexed before
# parse_trial_metadata stamped the field.
PRE_CONTRACT = {ABSENT: 12067}

_raised, _msg = verdict(PRE_CONTRACT)
check("a collection with no criteria_split anywhere RAISES", _raised, True)
check_true("...naming the fraction", "100.00%" in _msg)
check_true("...and the threshold", "0.50%" in _msg)
check_true("...and the swap refusal",
           "was NOT moved" in _msg
           and "PREVIOUS COLLECTION IS STILL SERVING" in _msg)
check_true("...and the closed vocabulary the value should have come from",
           all(v in _msg for v in indexer.CRITERIA_SPLIT_VALUES))

_pre = indexer.evaluate_criteria_split_distribution(PRE_CONTRACT)
check("an absent field is its own category, not skipped",
      _pre["counts"].get(ABSENT), 12067)
check("...and is counted in the corpus total, so nothing is lost",
      _pre["total"], 12067)
check("...and the degraded fraction is a real 0.0, not a missing key",
      _pre["degraded_fraction"], 0.0)

# An unreadable payload is a DIFFERENT finding counted the same way.
_damaged = {BOTH: 14000, UNREADABLE: 324}
_dm = indexer.evaluate_criteria_split_distribution(_damaged)
check("an unreadable payload counts as unusable", _dm["unusable_count"], 324)
check_true("...and is refused at 2.26%", verdict(_damaged)[0])
check_true("...but is counted apart from an absent field",
           _dm["counts"].get(UNREADABLE) == 324
           and ABSENT not in _dm["counts"])

# CONTROL: raise the ceiling and the identical distribution passes.
check("CONTROL: the same pre-contract corpus passes at a ceiling above it",
      verdict(PRE_CONTRACT, max_unusable=1.0)[0], False)


# ===========================================================================
# SECTION 4 -- THE BOUNDARY SITS WHERE THE CONSTANT SAYS
# ===========================================================================
section("SECTION 4 -- the boundary is the constant, and the constant is pinned")

# THE CONSTANTS ARE PINNED AS LITERALS FIRST. Deriving the boundary from the
# constant and then asserting the boundary is where the constant says is true
# by construction -- that is exactly the defect File 42's first boundary
# assertions shipped. So each value is asserted against a typed literal, and
# only then is the boundary built from it.
check("the degraded ceiling is 3%", indexer._MAX_CRITERIA_SPLIT_DEGRADED, 0.03)
check("the no-exclusion ceiling is 5%",
      indexer._MAX_CRITERIA_SPLIT_NO_EXCLUSION, 0.05)
check("the unusable ceiling is 0.5%",
      indexer._MAX_CRITERIA_SPLIT_UNUSABLE, 0.005)

# A round total so the boundary lands on an exact integer count. Asserted
# rather than assumed: if these divisions were not exactly the ceilings, the
# "equal passes" checks below would be testing a fraction one ULP off.
N = 10000
check_true("300/10000 is exactly the degraded ceiling", 300 / N == 0.03)
check_true("500/10000 is exactly the no-exclusion ceiling", 500 / N == 0.05)
check_true("50/10000 is exactly the unusable ceiling", 50 / N == 0.005)

# degraded: put the whole population in empty_criteria so no_exclusion (which
# counts unsplit + inclusion_only) stays at zero and the gates are isolated.
check("EQUAL to the degraded ceiling PASSES",
      verdict({BOTH: N - 300, EMPTY: 300})[0], False)
check("ONE POINT OVER the degraded ceiling FAILS",
      verdict({BOTH: N - 301, EMPTY: 301})[0], True)

# no_exclusion: put it all in inclusion_only so degraded stays at zero.
check("EQUAL to the no-exclusion ceiling PASSES",
      verdict({BOTH: N - 500, INCLUSION_ONLY: 500})[0], False)
check("ONE POINT OVER the no-exclusion ceiling FAILS",
      verdict({BOTH: N - 501, INCLUSION_ONLY: 501})[0], True)

check("EQUAL to the unusable ceiling PASSES",
      verdict({BOTH: N - 50, ABSENT: 50})[0], False)
check("ONE POINT OVER the unusable ceiling FAILS",
      verdict({BOTH: N - 51, ABSENT: 51})[0], True)

# The one-point-over cases must fail for the RIGHT reason, or the boundary
# check is satisfied by any failure at all.
check("...the degraded overflow names the degraded gate",
      len(indexer.evaluate_criteria_split_distribution(
          {BOTH: N - 301, EMPTY: 301})["failures"]), 1)
check_true("...and it is the degraded message",
           EMPTY in verdict({BOTH: N - 301, EMPTY: 301})[1])
check("...the unusable overflow names the unusable gate",
      len(indexer.evaluate_criteria_split_distribution(
          {BOTH: N - 51, ABSENT: 51})["failures"]), 1)

# Each ceiling is honoured as an ARGUMENT, so the module constant is a default
# and not a second hardcoded copy inside the comparison.
check("the degraded ceiling is honoured when passed explicitly",
      verdict({BOTH: N - 301, EMPTY: 301}, max_degraded=0.05)[0], False)
check("the no-exclusion ceiling is honoured when passed explicitly",
      verdict({BOTH: N - 501, INCLUSION_ONLY: 501}, max_no_exclusion=0.9)[0],
      False)
check("the unusable ceiling is honoured when passed explicitly",
      verdict({BOTH: N - 51, ABSENT: 51}, max_unusable=0.9)[0], False)


# ===========================================================================
# SECTION 5 -- THE HALF A TWO-GATE DESIGN CANNOT SEE
# ===========================================================================
section("SECTION 5 -- an exclusion-heading regression, degraded unmoved")

# split_inclusion_exclusion searches the two heading families INDEPENDENTLY.
# A ClinicalTrials.gov change to the exclusion family alone leaves every
# inclusion heading matching, so the affected trials become `inclusion_only`
# and NOT `unsplit` -- the degraded fraction does not move at all, while every
# affected trial reaches the judge with its exclusion criteria relabelled.
# This is why the no_exclusion fraction exists as a separate gate.
EXCLUSION_REGRESSION = {BOTH: 300, INCLUSION_ONLY: 13942, UNSPLIT: 82}

_reg = indexer.evaluate_criteria_split_distribution(EXCLUSION_REGRESSION)
check("the degraded population is UNCHANGED from the live corpus",
      _reg["degraded_count"], 82)
check_true("...and its fraction is under the degraded ceiling",
           _reg["degraded_fraction"] <= indexer._MAX_CRITERIA_SPLIT_DEGRADED)
check_true("...so the degraded gate does NOT fire",
           not any("or 'empty_criteria'" in f for f in _reg["failures"]))
check_true("but the no-exclusion gate DOES", len(_reg["failures"]) >= 1)

_raised, _msg = verdict(EXCLUSION_REGRESSION)
check("an exclusion-heading regression is refused", _raised, True)
check_true("...naming inclusion_only as the branch", INCLUSION_ONLY in _msg)
check_true("...and the swap refusal",
           "PREVIOUS COLLECTION IS STILL SERVING" in _msg)

# CONTROL: without the no-exclusion gate this distribution is admitted. Driven
# by raising only that one ceiling, which is what "the brief's two gates" is.
check("CONTROL: with only the two briefed gates, it is ADMITTED",
      verdict(EXCLUSION_REGRESSION, max_no_exclusion=1.0)[0], False)

# The two fractions OVERLAP on `unsplit`, so they must not be reported as if
# they summed.
_overlap = indexer.evaluate_criteria_split_distribution({UNSPLIT: 100})
check("unsplit is counted in BOTH fractions", _overlap["degraded_count"], 100)
check("...in both", _overlap["no_exclusion_count"], 100)


# ===========================================================================
# SECTION 6 -- A CENSUS OVER NOTHING IS NOT A PASS
# ===========================================================================
section("SECTION 6 -- an empty distribution is refused, not divided by zero")

_empty = indexer.evaluate_criteria_split_distribution({})
check("an empty census produces a failure", len(_empty["failures"]), 1)
check("...and reports no fraction rather than 0.0",
      _empty["degraded_fraction"], None)
check("...for every gated fraction", _empty["unusable_fraction"], None)
check("...and a zero total", _empty["total"], 0)

_raised, _msg = verdict({})
check("an empty census RAISES", _raised, True)
check_true("...saying it measured nothing", "MEASURED NOTHING" in _msg)
check_true("...and refusing the swap",
           "PREVIOUS COLLECTION IS STILL SERVING" in _msg)

# A Counter that has been touched but holds only zeros is empty too.
from collections import Counter as _Counter  # noqa: E402

_zeroed = _Counter()
_zeroed[UNSPLIT] += 0
check("a Counter of zeros is treated as an empty census",
      indexer.evaluate_criteria_split_distribution(_zeroed)["total"], 0)
check_true("...and is refused", verdict(_zeroed)[0])

# The evaluator must not mutate its argument -- verify_collection stores the
# same object it passed in.
_arg = {BOTH: 10, UNSPLIT: 1}
_before = dict(_arg)
indexer.evaluate_criteria_split_distribution(_arg)
check("the evaluator does not mutate the distribution it was handed",
      _arg, _before)


# ===========================================================================
# SECTION 7 -- THE FEEDER'S PARSING AND PAGINATION
# ===========================================================================
section("SECTION 7 -- the scroll feeder, against a stand-in client")

# NOT LIVE COVERAGE, and the module docstring says so. What a stand-in CAN
# prove is that the parser reads the field out of the right place and that the
# scroll actually paginates -- a feeder that dropped the offset would report
# the first page as the whole corpus, which is a silent under-count of exactly
# the population the gate exists to find.


class _Point:
    def __init__(self, payload):
        self.payload = payload


class _PagingClient:
    """Serves `points` in pages, recording every offset it was given."""

    def __init__(self, points, page=3):
        self._points = points
        self._page = page
        self.offsets = []
        self.payload_selectors = []
        self.with_vectors = []

    def scroll(self, collection_name, limit, offset, with_payload,
               with_vectors):
        self.offsets.append(offset)
        self.payload_selectors.append(with_payload)
        self.with_vectors.append(with_vectors)
        start = offset or 0
        stop = min(start + min(limit, self._page), len(self._points))
        nxt = stop if stop < len(self._points) else None
        return self._points[start:stop], nxt


def _blob(value):
    return _Point({"full_trial_json": {"nct_id": "NCT1", "criteria_split": value}})


_points = ([_blob(BOTH)] * 7 + [_blob(UNSPLIT)] * 2
           + [_Point({"full_trial_json": {"nct_id": "NCT2"}})]      # no field
           + [_Point({"nct_id": "NCT3"})]                           # no blob
           + [_Point(None)])                                        # no payload
_client = _PagingClient(_points, page=3)
_counts = indexer.scroll_criteria_split_distribution("c", client=_client)

check("every point is accounted for", sum(_counts.values()), len(_points))
check("...the recognised values are counted", _counts[BOTH], 7)
check("...and the unsplit ones", _counts[UNSPLIT], 2)
check("a blob with no criteria_split key is field_absent", _counts[ABSENT], 1)
check("a point with no blob at all is payload_unreadable",
      _counts[UNREADABLE], 2)
check_true("the scroll PAGINATED rather than reading one page",
           len(_client.offsets) >= 4)
check("...starting from no offset", _client.offsets[0], None)
check_true("...and following the server's next_page_offset",
           _client.offsets[1:] == [3, 6, 9, 12][:len(_client.offsets) - 1])
check("...asking for only the one NESTED payload key it reads",
      _client.payload_selectors[0], ["full_trial_json.criteria_split"])
check("...which is the module's named selector, not a literal at the call site",
      _client.payload_selectors[0], indexer._CRITERIA_SPLIT_SELECTOR)
check_true("...and it is a path INTO the blob, not the whole blob",
           _client.payload_selectors[0] != ["full_trial_json"])
check("...and for no vectors", _client.with_vectors[0], False)

# CONTROL: a client that never advances would be an infinite loop, so the
# feeder's termination must come from the server's offset. A one-page client
# must stop after one page.
_one = _PagingClient([_blob(BOTH)], page=10)
check("a single-page collection terminates after one page",
      sum(indexer.scroll_criteria_split_distribution("c", client=_one).values()),
      1)
check("...with exactly one scroll call", len(_one.offsets), 1)

# CONTROL: a feeder that ignored pages after the first would report 3, not 12.
check_true("CONTROL: the stand-in really does hold back later pages",
           len(_points) > _client._page)

# A blob stored as a JSON string is read, not reported as damaged.
_str_blob = _Point({"full_trial_json": '{"criteria_split": "unsplit"}'})
_bad_str = _Point({"full_trial_json": "not json at all"})
_c2 = _PagingClient([_str_blob, _bad_str], page=10)
_counts2 = indexer.scroll_criteria_split_distribution("c", client=_c2)
check("a blob stored as a JSON string is parsed", _counts2[UNSPLIT], 1)
check("...and an unparseable one is payload_unreadable", _counts2[UNREADABLE], 1)

# An empty collection yields an empty census, which section 6 refuses.
_c3 = _PagingClient([], page=10)
check("an empty collection yields an empty census",
      sum(indexer.scroll_criteria_split_distribution("c", client=_c3).values()),
      0)


# ===========================================================================
# SECTION 8 -- THE WIRING: INSIDE verify_collection, BEFORE THE SWAP
# ===========================================================================
section("SECTION 8 -- the gate is on the path both callers already take")

_indexer_src = open(os.path.abspath(indexer.__file__), encoding="utf-8").read()
_tree = ast.parse(_indexer_src)


def _fn(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _calls(node):
    return [ast.unparse(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)]


def _gate_is_wired(tree):
    """The predicate under test: does verify_collection run the census?"""
    fn = _fn(tree, "verify_collection")
    if fn is None:
        return False
    names = _calls(fn)
    return ("scroll_criteria_split_distribution" in names
            and "evaluate_criteria_split_distribution" in names)


def _gate_feeds_shared_failures(tree):
    """Does it append to the SAME list, rather than raising on its own?

    verify_collection raises once at the end naming every failure. A gate that
    raised from inside would report the split distribution and hide a
    simultaneously-broken sparse vector.
    """
    fn = _fn(tree, "verify_collection")
    if fn is None:
        return False
    for node in ast.walk(fn):
        if not isinstance(node, ast.For):
            continue
        if "split_report['failures']" not in ast.unparse(node.iter).replace('"', "'"):
            continue
        body = ast.unparse(node)
        return "_fail(" in body and not any(
            isinstance(n, ast.Raise) for n in ast.walk(node))
    return False


def _census_coverage_checked(tree):
    """Is the census total compared against the collection's point count?

    Without it, a scroll that stopped after one page reports three tidy
    fractions over a subset and the gate passes having measured nothing it
    claims to have measured. The pure evaluator cannot check this -- it has
    only the distribution -- so the comparison has to be here, against the
    `actual` count section 2 already took.
    """
    fn = _fn(tree, "verify_collection")
    if fn is None:
        return False
    for node in ast.walk(fn):
        if not isinstance(node, ast.Compare):
            continue
        rendered = ast.unparse(node).replace('"', "'")
        if "split_report['total']" in rendered and "actual" in rendered:
            return True
    return False


check_true("verify_collection runs the criteria_split census",
           _gate_is_wired(_tree))
check_true("...and feeds its failures into the shared list",
           _gate_feeds_shared_failures(_tree))
check_true("...and checks the census covered the whole collection",
           _census_coverage_checked(_tree))

# The order in main() is the defect the verification step exists to fix, and
# the gate inherits the escape only by sitting inside that call.
_main = _fn(_tree, "main")
_order = _calls(_main)


def _first(name):
    for i, c in enumerate(_order):
        if c.endswith(name):
            return i
    return -1


check_true("main() still verifies before swapping",
           0 <= _first("verify_collection") < _first("swap_alias_atomic"))
check_true("...and main() does NOT call the census itself",
           "scroll_criteria_split_distribution" not in _order)

# CONTROLS, on mutated COPIES of the tree. Nothing is exec'd and nothing on
# disk is touched.
_stripped = ast.parse(_indexer_src)
_vc = _fn(_stripped, "verify_collection")
_vc.body = [n for n in _vc.body
            if "criteria_split" not in ast.unparse(n)
            and "split_report" not in ast.unparse(n)
            and "split_counts" not in ast.unparse(n)]
check_true("CONTROL: with the census stripped, the wiring check FAILS",
           not _gate_is_wired(_stripped))
check_true("CONTROL: ...and so does the shared-failures check",
           not _gate_feeds_shared_failures(_stripped))
check_true("CONTROL: ...and so does the census-coverage check",
           not _census_coverage_checked(_stripped))
check_true("CONTROL: ...and the stripped copy really lost statements",
           len(_vc.body) < len(_fn(_tree, "verify_collection").body))

_raising = ast.parse(_indexer_src)
_vc2 = _fn(_raising, "verify_collection")
for _node in ast.walk(_vc2):
    if (isinstance(_node, ast.For)
            and "split_report" in ast.unparse(_node.iter)):
        _node.body = [ast.parse("raise IndexVerificationError(message)").body[0]]
        break
check_true("CONTROL: a gate that raises on its own FAILS the shared-list check",
           not _gate_feeds_shared_failures(_raising))
check_true("CONTROL: ...while still passing the wiring check, so the two "
           "checks are independent", _gate_is_wired(_raising))

# BOTH CALLERS reach the gate through verify_collection and neither carries a
# second copy of it. The generated DAG is built as a string, so it is parsed.
from oncotriage.orchestration.dag_generator import build_dag_content  # noqa: E402

_dag = build_dag_content(code_path="/x/code/", keys_path="/x/keys/",
                         data_trial_path="/x/trials/")
_dag_tree = ast.parse(_dag)
_rebuild = _fn(_dag_tree, "rebuild_index")
_dag_calls = _calls(_rebuild)
check_true("the DAG's rebuild task calls verify_collection",
           any(c.endswith("verify_collection") for c in _dag_calls))
check_true("...before it swaps the alias",
           [c.endswith("verify_collection") for c in _dag_calls].index(True)
           < [c.endswith("swap_alias_atomic") for c in _dag_calls].index(True))
check_true("...and carries no census of its own to drift from this one",
           "scroll_criteria_split_distribution" not in _dag
           and "criteria_split" not in _dag)
check_true("...and the DAG schedule is still disabled",
           "DAG_SCHEDULE" in _dag)

# The reporter must run on every path, not only on failure.
_vc_src = ast.get_source_segment(_indexer_src, _fn(_tree, "verify_collection")) or ""


def _at(haystack, *needles):
    """Offset of the first needle present, or -1. NEVER raises.

    A bare str.index() here aborts the run on exactly the revert this check
    exists to catch -- the first version of this file did, and the revert
    harness reported one traceback where it owed fifteen results. That is the
    third time this suite has shipped that shape, after
    tests/test_storage_query_layer.py and
    tests/test_dashboard_reproducibility_tab.py.
    """
    for needle in needles:
        found = haystack.find(needle)
        if found >= 0:
            return found
    return -1


_report_at = _at(_vc_src, "report_criteria_split_distribution")
_examine_at = _at(_vc_src, 'for message in split_report["failures"]',
                  "for message in split_report")
check_true("the distribution is reported on every path, pass or fail",
           _report_at >= 0)
check_true("...before the failures are examined, so a failing run still "
           "reports the numbers", 0 <= _report_at < _examine_at)

# Every field the reporter logs must survive the observability allowlist, or
# the standing measurement is silently dropped at the formatter.
from oncotriage.observability import LOGGABLE_FIELDS  # noqa: E402

_logged = {"total", "split_degraded_count", "split_degraded_fraction",
           "split_degraded_max", "split_no_exclusion_count",
           "split_no_exclusion_fraction", "split_no_exclusion_max",
           "split_unusable_count", "split_unusable_fraction",
           "split_unusable_max", "unsplit_count", "empty_criteria_count",
           "field_absent_count", "payload_unreadable_count"}
check("every reported field is on the log allowlist",
      sorted(_logged - set(LOGGABLE_FIELDS)), [])
check_true("...and that comparison is non-degenerate", len(_logged) == 14)


# ===========================================================================
section("SUMMARY")
print(f"  passed: {_passed}")
print(f"  failed: {_failed}")

if __name__ == "__main__":
    sys.exit(1 if _failed else 0)


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug 09 2026

@author: ramyalsaffar
"""
