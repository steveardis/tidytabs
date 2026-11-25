from tidytabs.scorer import CloseScorer


def test_untrained_scorer_is_neutral():
    s = CloseScorer({}, total_close=0, total_keep=0)
    assert s.probability({"cat:Jira"}) == 0.5


def test_feature_seen_closed_scores_high():
    s = CloseScorer({"cat:Jira": (20, 0)}, total_close=20, total_keep=20)
    assert s.probability({"cat:Jira"}) > 0.8


def test_feature_seen_kept_scores_low():
    s = CloseScorer({"cat:Slack": (0, 20)}, total_close=20, total_keep=20)
    assert s.probability({"cat:Slack"}) < 0.2


def test_unknown_features_are_ignored():
    s = CloseScorer({"cat:Jira": (20, 0)}, total_close=20, total_keep=20)
    assert s.probability({"cat:Jira", "tok:never-seen"}) == s.probability({"cat:Jira"})


def test_reasons_lists_strongest_features_first():
    s = CloseScorer(
        {"cat:Jira": (20, 0), "tok:timeout": (3, 2)},
        total_close=20,
        total_keep=20,
    )
    reasons = s.reasons({"cat:Jira", "tok:timeout"}, limit=1)
    assert reasons == ["cat:Jira"]


def test_unknown_tab_stays_neutral_when_history_is_one_sided():
    s = CloseScorer({"cat:Jira": (50, 0)}, total_close=50, total_keep=0)
    assert s.probability({"cat:Other"}) == 0.5


def test_title_words_alone_cannot_reach_the_close_threshold():
    # real case: "canopy" was in 6 closed Jenkins and AWS tabs, and made Jira tabs score 96-100%
    s = CloseScorer({"tok:canopy": (6, 0), "tok:board": (4, 0)}, total_close=221, total_keep=359)
    assert s.probability({"tok:canopy", "tok:board", "cat:Jira"}) < 0.7


def test_title_words_still_move_the_score():
    s = CloseScorer({"tok:canopy": (6, 0)}, total_close=221, total_keep=359)
    assert s.probability({"tok:canopy"}) > 0.55


def test_category_evidence_is_not_capped():
    s = CloseScorer({"cat:Search": (20, 0), "tok:kafka": (0, 5)}, total_close=221, total_keep=359)
    assert s.probability({"cat:Search", "tok:kafka"}) > 0.9
