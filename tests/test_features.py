from tidytabs.features import category, domain, extract_features, age_bucket
from tidytabs.models import Tab


def tab(url, title="t"):
    return Tab(window_id="w1", tab_id="1", index=0, title=title, url=url)


def test_domain_strips_www_and_port():
    assert domain("https://www.example.com:8443/a?b=1") == "example.com"


def test_domain_for_internal_pages():
    assert domain("chrome://newtab/") == "chrome-newtab"
    assert domain("about:blank") == "about-blank"


def test_category_jira_issue():
    assert category(tab("https://acme.atlassian.net/browse/CAP3-123")) == "Jira"


def test_category_jira_board_and_search():
    assert category(tab("https://acme.atlassian.net/jira/software/projects/X/boards/1")) == "Jira"


def test_category_confluence():
    assert category(tab("https://acme.atlassian.net/wiki/spaces/ENG/pages/1")) == "Confluence"


def test_category_bitbucket_pull_request():
    assert category(tab("https://bitbucket.org/acme/repo/pull-requests/42")) == "Bitbucket"


def test_category_jenkins_from_title():
    t = tab("https://ci.acme.io/job/x/1/", "feature/x [build] - Jenkins")
    assert category(t) == "Jenkins"


def test_category_new_tab():
    assert category(tab("chrome://newtab/")) == "New Tab"


def test_category_google_search():
    assert category(tab("https://www.google.com/search?q=x")) == "Search"


def test_category_falls_back_to_domain():
    assert category(tab("https://news.ycombinator.com/item?id=1")) == "news.ycombinator.com"


def test_extract_features_contains_domain_category_and_title_tokens():
    t = tab("https://acme.atlassian.net/browse/CAP3-123", "CAP3-123 Fix login timeout - Jira")
    f = extract_features(t)
    assert "cat:Jira" in f
    assert "domain:acme.atlassian.net" in f
    assert "path:browse" in f
    assert "tok:login" in f
    assert "tok:timeout" in f
    # stop words and very short tokens are dropped
    assert "tok:-" not in f
    assert "tok:fix" not in f


def test_extract_features_adds_age_bucket_when_known():
    f = extract_features(tab("https://x.com/"), age_days=10)
    assert "age:week+" in f


def test_age_buckets():
    assert age_bucket(0.2) == "age:new"
    assert age_bucket(2) == "age:days"
    assert age_bucket(8) == "age:week+"
    assert age_bucket(40) == "age:month+"


def test_category_brave_search():
    assert category(tab("https://search.brave.com/search?q=x")) == "Search"
