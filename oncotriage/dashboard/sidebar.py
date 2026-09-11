"""
Dashboard sidebar: filters, the refresh button and the CSV export.

``render_sidebar(df)`` returns the filtered frame every tab is then handed.
Moved verbatim out of "21- Streamlit Dashboard.py" in pass 20c-3c-1.

The refresh button calls ``st.cache_data.clear()``, which clears BY CACHE, not
by function -- it empties every ``@st.cache_data`` entry in the process,
including the three loaders in ``oncotriage.dashboard.data``, which now live in
a different module than this one. That still works, and check 6b of
"tests/test_package_invariants.py" is what says so.
"""

from datetime import datetime

import streamlit as st

from oncotriage.dashboard.tiers import (ANY_MATCH_TIERS, MATCH_TIER_NO_MATCH,
                                        any_match_series)


MATCH_FILTER_ALL = "All"


def _tier_phrase(tiers):
    """``('a', 'b', 'c')`` -> ``"a, b or c"``; total over any non-empty tuple.

    Written as a function rather than a one-line join so the two- and
    one-member cases are not a ``[:-1]``/``[-1]`` slice quietly producing
    ", or x" -- ``MATCH_TIERS`` is a list somebody can edit, and a label that
    renders as punctuation is a label nobody can read.
    """
    words = [t.replace(" Match", "") for t in tiers]
    if len(words) == 1:
        return words[0]
    return ", ".join(words[:-1]) + " or " + words[-1]


MATCH_FILTER_ANY = f"Any Match ({_tier_phrase(ANY_MATCH_TIERS)})"
"""The label states the DEFINITION, and it is DERIVED FROM IT rather than typed.

IT USED TO READ "Any Match (Full + Partial)" AND THE PARENTHESIS WAS FALSE IN
BOTH DIRECTIONS. The filter beneath it was ``eligible_matches > 0``, which
counts an Unconfirmed patient -- so the label excluded a group the filter
included -- and the charts every tab draws beside it count Unconfirmed too,
through ``tiers.any_match_series``. A reader narrowing to "Full + Partial" got
a selection containing neither definition's population.

A HAND-WRITTEN LABEL IS HOW THAT HAPPENED, AND REPLACING ONE CORRECT LITERAL
WITH ANOTHER WOULD LEAVE THE MECHANISM IN PLACE. The text comes out of
``ANY_MATCH_TIERS`` -- the same tuple ``any_match_series`` selects on -- so a
fifth tier, or a tier moved out of the Any Match set, rewrites this label
rather than leaving it describing the previous definition."""

MATCH_FILTER_NONE = f"{MATCH_TIER_NO_MATCH} Only"
"""The complement of the above under the SAME owner, which is what makes the
two a partition, and named from the same constant for the same reason.

``eligible_matches == 0`` was NOT a complement: a row whose
``eligible_matches`` is NULL satisfies neither ``> 0`` nor ``== 0`` -- NaN
compares False to both -- so such a patient vanished from the page under EITHER
selection, and appeared under "All". ``match_tier`` has no such third state:
``enrich_match_tiers`` assigns one of four values to every row."""

MATCH_FILTER_OPTIONS = (MATCH_FILTER_ALL, MATCH_FILTER_ANY, MATCH_FILTER_NONE)
"""Every option the match-status filter offers. CLOSED: the branch below is
exhaustive over it, and an unlisted value would fall through and filter
nothing while the widget said it had."""

MATCH_FILTER_UNAVAILABLE = (
    "Match status filter unavailable: this frame has no `match_tier` column, "
    "so there is nothing to filter on. `oncotriage/dashboard/app.py:main()` "
    "enriches before it calls this function; a caller that does not has not "
    "been through `enrich_match_tiers`."
)
"""What the sidebar says instead of offering a filter it cannot apply.

NOT A FALLBACK TO ``eligible_matches``. That column is the THIRD definition
this repair removes, and reaching for it here -- in the one place that decides
what every tab sees -- would put it back exactly where it does the most damage.
NOT A RAISE EITHER: this function is reached before any tab renders, so a raise
here is a blank page for a caller-ordering mistake. The filter is simply not
offered, and the reason is on screen."""


def render_sidebar(df):
    """Render sidebar with filters and data refresh controls.

    THE MATCH-STATUS FILTER READS ``match_tier``, SO ``df`` MUST ALREADY BE
    ENRICHED. ``main()`` calls ``enrich_match_tiers`` above this call for that
    reason; the ordering is a correctness property rather than a convenience,
    and moving it is what let this filter stop being a third definition of
    "Any Match". A frame without the column still renders every other filter --
    see ``MATCH_FILTER_UNAVAILABLE``.
    """
    
    st.sidebar.header("⚙️ Filters")
    
    # Refresh button
    if st.sidebar.button("🔄 Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    
    st.sidebar.markdown("---")
    
    # Date range filter
    st.sidebar.subheader("Date Range")
    valid_timestamps = df['timestamp'].dropna()
    if valid_timestamps.empty:
        st.sidebar.info("No valid timestamps available.")
        return df
    min_date = valid_timestamps.min().date()
    max_date = valid_timestamps.max().date()
    date_range = st.sidebar.date_input(
        "Select date range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date
    )
    
    # Age range filter
    st.sidebar.subheader("Age Range")
    valid_ages = df['age'].dropna()
    min_age = int(valid_ages.min()) if not valid_ages.empty else 0
    max_age = int(valid_ages.max()) if not valid_ages.empty else 100
    if min_age == max_age:
        max_age = min_age + 1
    age_range = st.sidebar.slider(
        "Select age range",
        min_value=min_age,
        max_value=max_age,
        value=(min_age, max_age)
    )
    
    # Sex filter
    st.sidebar.subheader("Sex")
    sex_values = df['sex'].dropna().unique().tolist()
    sex_options = ['All'] + sorted(sex_values)
    selected_sex_option = st.sidebar.selectbox(
        "Select sex",
        options=sex_options,
        index=0
    )
    
    # Convert to list for filtering logic
    if selected_sex_option == 'All':
        selected_sex = sex_values if sex_values else df['sex'].unique().tolist()
    else:
        selected_sex = [selected_sex_option]
    
    # Condition count filter
    st.sidebar.subheader("Condition Count")
    valid_conditions = df['condition_count'].dropna()
    min_conditions = int(valid_conditions.min()) if not valid_conditions.empty else 0
    max_conditions = int(valid_conditions.max()) if not valid_conditions.empty else 50
    if min_conditions == max_conditions:
        max_conditions = min_conditions + 1
    condition_range = st.sidebar.slider(
        "Select condition count range",
        min_value=min_conditions,
        max_value=max_conditions,
        value=(min_conditions, max_conditions)
    )
    
    # Medication count filter
    st.sidebar.subheader("Medication Count")
    valid_meds = df['medication_count'].dropna()
    min_meds = int(valid_meds.min()) if not valid_meds.empty else 0
    max_meds = int(valid_meds.max()) if not valid_meds.empty else 50
    if min_meds == max_meds:
        max_meds = min_meds + 1
    medication_range = st.sidebar.slider(
        "Select medication count range",
        min_value=min_meds,
        max_value=max_meds,
        value=(min_meds, max_meds)
    )
    
    # Match Status filter
    st.sidebar.subheader("Match Status")
    match_tier_available = 'match_tier' in df.columns
    if match_tier_available:
        match_status_option = st.sidebar.selectbox(
            "Select match status",
            options=list(MATCH_FILTER_OPTIONS),
            index=0,
            help="Derived from match_tier -- the SAME owner the Overview and "
                 "Demographics tiles and the demographics charts use, so a "
                 "selection here and the figures it produces are the same "
                 "question asked once."
        )
    else:
        match_status_option = MATCH_FILTER_ALL
        st.sidebar.caption(MATCH_FILTER_UNAVAILABLE)
    
    # Apply filters
    filtered_df = df.copy()
    
    if len(date_range) == 2:
        start_date, end_date = date_range
        filtered_df = filtered_df[
            (filtered_df['timestamp'].dt.date >= start_date) &
            (filtered_df['timestamp'].dt.date <= end_date)
        ]
    
    filtered_df = filtered_df[
        (filtered_df['age'] >= age_range[0]) &
        (filtered_df['age'] <= age_range[1])
    ]
    
    if selected_sex:
        filtered_df = filtered_df[filtered_df['sex'].isin(selected_sex)]
    
    filtered_df = filtered_df[
        (filtered_df['condition_count'] >= condition_range[0]) &
        (filtered_df['condition_count'] <= condition_range[1])
    ]
    
    filtered_df = filtered_df[
        (filtered_df['medication_count'] >= medication_range[0]) &
        (filtered_df['medication_count'] <= medication_range[1])
    ]
    
    # ONE OWNER, BOTH SELECTIONS (the repair pass). This read
    #
    #     filtered_df['eligible_matches'] > 0        and        == 0
    #
    # which is a THIRD definition of "Any Match" -- beside the Overview tile's
    # and the Demographics tab's, both of which the Part 4 pass had already
    # made one -- and it is the one that decides what EVERY tab sees. A reader
    # narrowing to "Any Match" and then reading an Any Match percentage was
    # reading a figure computed over a population selected by a different rule.
    #
    # THE COMPLEMENT IS TAKEN FROM THE SAME MASK RATHER THAN WRITTEN OUT, so
    # the two selections partition by construction. Writing the second as its
    # own predicate is how the pair came to have a hole: NaN is neither `> 0`
    # nor `== 0`.
    if match_tier_available and match_status_option != MATCH_FILTER_ALL:
        _any_match = any_match_series(filtered_df)
        filtered_df = filtered_df[
            _any_match if match_status_option == MATCH_FILTER_ANY
            else ~_any_match]
    
    # Show filter stats
    st.sidebar.markdown("---")
    st.sidebar.metric(
        "Showing", 
        f"{len(filtered_df):,} inferences",
        delta=f"{len(filtered_df) - len(df):+,}" if len(filtered_df) != len(df) else None,
        help="Pipeline runs matching current filters"
    )
    
    # Export section
    st.sidebar.markdown("---")
    st.sidebar.subheader("📥 Export Data")
    
    export_all = st.sidebar.checkbox("Export all columns", value=True)
    
    if export_all:
        selected_columns = filtered_df.columns.tolist()
    else:
        with st.sidebar.expander("Choose columns"):
            selected_columns = st.multiselect(
                "Select columns",
                options=filtered_df.columns.tolist(),
                default=filtered_df.columns.tolist(),
                label_visibility="collapsed"
            )
    
    if selected_columns:
        csv = filtered_df[selected_columns].to_csv(index=False, quoting=1)
        st.sidebar.download_button(
            label=f"Download CSV ({len(selected_columns)} cols)",
            data=csv,
            file_name=f"trialmatch_data_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            use_container_width=True
        )
    
    return filtered_df


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
