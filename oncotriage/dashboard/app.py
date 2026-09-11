"""
The dashboard's top-level render call.

``main()`` -- the page config, the sidebar, and the tabs -- moved verbatim out
of "21- Streamlit Dashboard.py" in pass 20c-3c-1. It rendered NINE tabs then;
the run-reader pass added a tenth, Run Health, over the ``runs`` and
``run_metrics`` tables.

THE TENTH TAB IS THE ONE THAT DOES NOT HONOUR THE SIDEBAR, and it is handed
``filtered_df`` anyway. Every other tab reports a per-patient figure, which a
filter narrows correctly; Run Health reports per-CAMPAIGN figures, which a filter
would turn into subtotals printed under a total's heading. So it reads its own
frames from the database unfiltered, uses ``filtered_df`` only to state how much
of the current selection belongs to a run, and says both things on screen. It
takes the argument rather than none so that the nine calls below stay one
shape.

``st.set_page_config`` STAYS INSIDE ``main()``, where File 21 always had it,
rather than moving up to the entry point. Streamlit requires it to be the first
Streamlit call of the run; keeping it as ``main()``'s first statement preserves
that ordering exactly, and moving it to module scope would make importing this
module a Streamlit side effect, which is what section 2 of
"tests/test_package_invariants.py" exists to forbid.
"""

import streamlit as st

from oncotriage.config import Project_Name
from oncotriage.constants import NOT_FOR_CLINICAL_USE
from oncotriage.dashboard.data import (
    SCHEMA_COLUMNS_MISSING,
    SCHEMA_NO_DATABASE,
    SCHEMA_READY,
    SCHEMA_TABLES_MISSING,
    SCHEMA_UNREADABLE,
    dashboard_schema_readiness,
    load_inferences_data,
    load_trial_matches_data,
)
from oncotriage.dashboard.sidebar import render_sidebar
from oncotriage.dashboard.tabs.cost_tokens import render_cost_tokens_tab
from oncotriage.dashboard.tabs.demographics import render_patient_demographics_tab
from oncotriage.dashboard.tabs.drift import render_drift_detection_tab
from oncotriage.dashboard.tabs.match_quality import render_match_quality_tab
from oncotriage.dashboard.tabs.overview import render_overview_tab
from oncotriage.dashboard.tabs.patient_explorer import render_patient_explorer_tab
from oncotriage.dashboard.tabs.performance import render_performance_tab
from oncotriage.dashboard.tabs.reproducibility import render_reproducibility_tab
from oncotriage.dashboard.tabs.run_health import render_run_health_tab
from oncotriage.dashboard.tabs.trial_explorer import render_trial_explorer_tab
from oncotriage.dashboard.tiers import enrich_match_tiers



# The operator's procedure for a database this dashboard cannot render, quoted
# from the project's own instructions rather than invented here. It is two
# commands and the second is the ordinary one; the FIRST IS A MOVE AND NOT A
# DELETE, because the archive is the only copy of every historical row.
_ARCHIVE_REMEDY = """**The procedure is to ARCHIVE, not to delete.** The
migrations in `oncotriage/storage/database_logger.py` are additive, which is
right for a database being carried forward and wrong for a file whose columns
were RENAMED underneath it: running the pipeline again will not turn
`gpt4o_evaluation_time` into `llm_classifier_evaluation_time`.

```bash
mv {path!r} \\
   {archive!r}
python "25- Batch Runner.py"        # the first write builds the new file
```

The first write calls `initialize_database`, which creates a file with every
column present from the first row, all tables, WAL from the first write, both
header stamps and every index. Nothing else has to be done and no migration is
run. **The archive is MOVED**: it is the only copy of every historical row, and
every query in `oncotriage/storage/queries.py` still reads it through `--db`."""


def _render_schema_diagnosis(readiness):
    """Say what was found, what is required, and what to do. Renders nothing else.

    ONE FUNCTION FOR EVERY NON-READY STATE, because the three states share a
    reader and differ only in which evidence they have to show. A branch per
    state at the call site would be four copies of "name the path, name the
    stamp, name the remedy".

    THE NEWER-DATABASE CASE DOES NOT GET THE ARCHIVE REMEDY, and that is the
    half a generic "your database is wrong" message gets backwards: a file
    stamped with a HIGHER era than this build knows is a database that is FINE
    and a checkout that is behind it. Telling that operator to archive it would
    destroy the newer file to accommodate older code. It is the same ruling
    `run_fingerprint.compare` makes for a stamp from the future, and
    `initialize_database` makes for `PRAGMA user_version`.
    """
    state = readiness["state"]
    path = readiness["db_path"]

    if state == SCHEMA_NO_DATABASE:
        st.error(f"**No database at the configured path.** `{path}`")
        st.markdown(
            "Nothing has been read and nothing is wrong with this build. The "
            "file appears the first time a writer opens it:\n\n"
            "```bash\npython \"25- Batch Runner.py\"\n```\n\n"
            "If that path is not where you expect, `ONCOTRIAGE_MAIN_PATH` "
            "moves the project root and `ONCOTRIAGE_INFERENCES_DB` moves this "
            "file on its own.")
        return

    if state == SCHEMA_UNREADABLE:
        st.error("**The database could not be read.** "
                 f"`{path}`\n\n`{readiness['error']}`")
        st.markdown(
            "This is a statement about the FILE, not about its contents: "
            "nothing was interrogated, so nothing below could be checked. A "
            "file that is not a SQLite database, one whose permissions deny "
            "this process, and one held by a writer all arrive here.")
        return

    if state not in (SCHEMA_TABLES_MISSING, SCHEMA_COLUMNS_MISSING):
        # EVERY MEMBER OF THE CLOSED VOCABULARY IS NAMED, and this is what a
        # bare `else` would have swallowed. `tabs/run_health.py` shipped that
        # `else` once and it rendered an unrecognised availability value as
        # "the file is not there", sending an operator to look for a database
        # sitting where it should be. A state this function does not know is a
        # defect in the loader, not a fact about the database, and it says so.
        st.error(
            f"**The schema preflight returned a state this page does not "
            f"know: {state!r}.** That is a defect in "
            f"`oncotriage/dashboard/data.py`, not a finding about "
            f"`{path}`. No tab has been drawn, because nothing here can say "
            f"whether one safely could.")
        return

    newer = (readiness["recorded_version"] is not None
             and readiness["recorded_version"] > readiness["required_version"])

    st.error(
        "**This database is not one this dashboard can render, so no tab has "
        "been drawn.** Rendering it would have raised a `KeyError` inside the "
        "first panel that reached a column it does not have — one column name "
        "and no diagnosis.")

    st.markdown(f"**Database** &nbsp;`{path}`")
    st.markdown(
        f"**Schema era** &nbsp;recorded **{readiness['recorded_version']}**, "
        f"this build creates **{readiness['required_version']}**"
        + (f" — {readiness['version_note']}" if readiness["version_note"] else "")
    )

    if readiness["missing_tables"]:
        st.markdown(
            "**Tables the dashboard loads before any tab renders, and which "
            "this database does not have:** &nbsp;"
            + ", ".join(f"`{t}`" for t in readiness["missing_tables"]))

    if readiness["missing_columns"]:
        st.markdown(
            f"**Columns the dashboard reads and this database does not have** "
            f"&nbsp;({len(readiness['missing_columns'])} of "
            f"{readiness['checked_columns']} checked):")
        # EVERY ONE OF THEM, not the first. The whole reason this runs before
        # the page is that a caught KeyError names whichever column one tab
        # happened to reach first and says nothing about the rest.
        st.code("\n".join(readiness["missing_columns"]), language="text")

    if readiness["tolerated_missing"]:
        # REPORTED, NEVER A REASON TO REFUSE. These are ADDITIVE columns: the
        # next writer to open the file supplies them, and the tabs that read
        # them handle their absence. Naming them anyway is what stops an
        # operator meeting one that a tab does NOT guard as a bare traceback
        # with no context -- see `_tolerated_columns` for the residual.
        # st.markdown AND NOT st.caption. `tests/test_clinical_use_framing.py`
        # pins that app.py makes EXACTLY ONE st.caption call, which is how
        # "the not-for-clinical-use framing appears once per page" is a
        # property of the source rather than of one render. A second caption
        # here would break that guard for a line that is not a standing
        # caveat -- this is one more piece of the diagnosis, and it belongs in
        # the same register as the rest of it.
        st.markdown(
            "_Also absent, and NOT a reason to refuse:_ "
            + ", ".join(f"`{c}`" for c in readiness["tolerated_missing"])
            + "_. These are ADDITIVE — the next writer to open this file "
              "supplies them — and the tabs that read them handle their "
              "absence._")

    if readiness["parse_failures"]:
        st.warning(
            "The requirement above is DERIVED from the dashboard's own source, "
            "and these modules could not be parsed, so any column only they "
            "read is unchecked: "
            + ", ".join(f"`{p}`" for p in readiness["parse_failures"]))

    if newer:
        st.info(
            "**Do not archive this file.** It records a NEWER schema era than "
            "this build knows, so the file is ahead of the code rather than "
            "behind it. Check out the revision that wrote it, or update this "
            "one.")
        return

    st.markdown(_ARCHIVE_REMEDY.format(
        path=path,
        archive=path.replace(".db", "-archive.db") if path.endswith(".db")
        else path + ".archive"))


def main():
    """Main application."""
    
    st.set_page_config(
        page_title=f"{Project_Name} Dashboard",
        page_icon="🏥",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    st.title(f"🏥 {Project_Name}: Clinical Trial Matching Dashboard")
    st.markdown("Real-time monitoring and analytics for the patient-trial matching pipeline")

    # THE CLINICAL-USE FRAMING, ONCE PER PAGE.
    #
    # WHY HERE AND NOT IN A TAB. Five of the nine tabs render eligibility
    # verdicts or match scores -- Overview, Match Quality, Patient Explorer,
    # Trial Explorer and Reproducibility -- and a person reading this dashboard
    # moves between them without the page reloading. One caption per tab would
    # be five copies of the same sentence competing for the same screen; one
    # caption per ROW would be thousands. This is the only page, so "once per
    # page" is one call, and it sits above the tab strip where it is on screen
    # whichever tab is selected.
    #
    # WHY ABOVE THE DATA LOAD. main() returns early twice below -- no data, and
    # no data matching the filters -- and both of those returns are reached by a
    # reader who is about to add data or widen a filter and then look at
    # verdicts. Placing the caption after either guard would make the framing
    # conditional on the page having something to frame.
    #
    # st.caption IS the muted style: streamlit renders it small and grey, which
    # is what a standing caveat should look like beside a title. It is NOT
    # st.warning -- an amber box that never goes away is read as an error the
    # operator has failed to clear, and stops being read at all.
    st.caption(NOT_FOR_CLINICAL_USE)

    # THE SCHEMA PREFLIGHT RUNS BEFORE ANYTHING IS LOADED (the dashboard-fixes
    # pass), and the ordering is the whole mechanism rather than a preference.
    # The failure it removes is raised INSIDE a tab, from a column read, and
    # there is no handler between a tab and here -- so by the time `df` exists
    # it is already too late to ask the question, and by the time the first tab
    # asks it the page is gone. See `dashboard_schema_readiness` for why the
    # version stamp is evidence and never the gate.
    #
    # AN EMPTY CURRENT-ERA DATABASE IS READY AND FALLS THROUGH TO THE MESSAGE
    # BELOW. "This database has not been run yet" and "this database is not one
    # this build can read" are different findings with different remedies, and
    # collapsing them is what a single "no data" message would do.
    _readiness = dashboard_schema_readiness()
    if _readiness["state"] != SCHEMA_READY:
        _render_schema_diagnosis(_readiness)
        return

    df = load_inferences_data()
    
    if df is None or df.empty:
        st.error("No data available. Please run some inferences first.")
        return
    
    # ENRICH BEFORE THE SIDEBAR (the repair pass), AND THE ORDER IS A
    # CORRECTNESS PROPERTY RATHER THAN A PREFERENCE.
    #
    # The sidebar's match-status filter used to read `eligible_matches`, which
    # was a THIRD definition of "Any Match" beside the two the Part 4 pass had
    # already made one -- and the one that decides what every tab sees. It
    # reads `match_tier` now, through the same owner, and `match_tier` is what
    # `enrich_match_tiers` adds; a sidebar handed the raw frame has no such
    # column to filter on.
    #
    # ENRICHING THE WHOLE FRAME AND THEN FILTERING IS VALUE-IDENTICAL TO
    # FILTERING AND THEN ENRICHING, measured rather than assumed:
    # `enrich_match_tiers` merges per-`id` counts out of `trial_matches`, so a
    # row's three counts and its tier are a function of that row and its trial
    # rows alone. The surviving rows carry the same values either way; what
    # changes is that the counts exist in time for the filter to use them.
    #
    # The cost is enriching rows the filter then drops -- three left merges
    # over the unfiltered frame instead of the filtered one, on a table this
    # dashboard already reads whole.
    trial_matches = load_trial_matches_data()
    df = enrich_match_tiers(df, trial_matches)

    filtered_df = render_sidebar(df)
    
    if filtered_df.empty:
        st.warning("No data matches the current filters.")
        return
    
    # THE TAB STRIP: A SMALLER FOOTPRINT AND A VISIBLE SCROLL AFFORDANCE
    # (the dashboard-fixes pass).
    #
    # WHAT WAS MEASURED, AND IT REFUTES THE OBVIOUS FIX. The ten labels are
    # read out of the list below and their advance widths measured against the
    # real font at the declared pixel size. At 1440px with the sidebar open
    # (21rem = 336px) and the wide-layout block padding, the usable strip is
    # about 1072px, and the TEXT ALONE -- before a single pixel of button
    # padding or flex gap -- measures:
    #
    #     20px bold  (what this CSS used to declare)   1721 px
    #     16px 600   (what it declares now)            1353 px
    #     14px normal (streamlit's own default, i.e.
    #                  deleting this block entirely)   1143 px
    #
    # So the strip OVERFLOWS AT EVERY SETTING, including with no CSS at all.
    # "Make the tabs fit by shrinking the label" is not available: ten labels
    # of these lengths do not fit in that width, and the only levers that would
    # make them fit are shortening the labels (a change to what every reader
    # sees) or dropping a tab.
    #
    # SO BOTH THINGS ARE DONE AND ONLY ONE OF THEM IS THE FIX. The size comes
    # down from 20px bold to 16px/600 -- still deliberately larger and heavier
    # than streamlit's default, so the customisation is kept rather than
    # deleted, and 368px (21%) of text comes off the strip, which is four to
    # five more tabs on screen before anything has to be scrolled. The
    # AFFORDANCE is what actually answers the defect: baseweb's tab-list
    # already scrolls and streamlit hides its scrollbar, so a reader at 1440px
    # had no indication that four tabs existed to the right of Performance.
    # The rules below re-enable a thin, always-visible scrollbar on that
    # element and nothing else.
    st.markdown("""
        <style>
        .stTabs [data-baseweb="tab-list"] button [data-testid="stMarkdownContainer"] p {
            font-size: 16px;
            font-weight: 600;
        }
        /* THE AFFORDANCE. Streamlit sets scrollbar-width:none and hides the
           webkit scrollbar on this element, so the strip scrolled silently.
           Both are reversed here, scoped to the tab list alone. */
        .stTabs [data-baseweb="tab-list"] {
            overflow-x: auto;
            scrollbar-width: thin;
        }
        .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar {
            display: block;
            height: 6px;
        }
        .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar-thumb {
            background: rgba(128, 128, 128, 0.45);
            border-radius: 3px;
        }
        .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar-track {
            background: rgba(128, 128, 128, 0.12);
        }
        </style>
    """, unsafe_allow_html=True)
    
    # Tab navigation
    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10 = st.tabs([
        "📊  Overview",
        "🔍  Match Quality",
        "🔎  Patient Explorer",
        "🧬  Trial Explorer",
        "👥  Patient Demographics",
        "⚡  Performance",
        "💰  Cost & Tokens",
        "🔬  Drift Detection",
        "🔁  Reproducibility",
        "🩺  Run Health"
    ])
    
    with tab1:
        render_overview_tab(filtered_df)
    
    with tab2:
        render_match_quality_tab(filtered_df)
    
    with tab3:
        render_patient_explorer_tab(filtered_df)
    
    with tab4:
        render_trial_explorer_tab(filtered_df)
    
    with tab5:
        render_patient_demographics_tab(filtered_df)
    
    with tab6:
        render_performance_tab(filtered_df)
    
    with tab7:
        render_cost_tokens_tab(filtered_df)
    
    with tab8:
        render_drift_detection_tab(filtered_df)

    with tab9:
        render_reproducibility_tab(filtered_df)

    with tab10:
        render_run_health_tab(filtered_df)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
