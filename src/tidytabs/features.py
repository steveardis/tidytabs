"""Turn a tab into a category and a set of features that the scorer learns from."""

import re
from urllib.parse import urlsplit

from tidytabs.models import Tab

STOP_WORDS = {
    "with", "from", "this", "that", "your", "have", "into", "about", "what",
    "when", "where", "which", "will", "jira", "google", "chrome", "brave",
}

# (category, test). The first rule that matches wins.
_RULES = [
    ("New Tab", lambda host, path, title: host in {"chrome-newtab", "about-blank"}),
    ("Jira", lambda host, path, title: host.endswith("atlassian.net")
        and (path.startswith("/browse/") or path.startswith("/jira/"))),
    ("Confluence", lambda host, path, title: host.endswith("atlassian.net") and path.startswith("/wiki")),
    ("Bitbucket", lambda host, path, title: host == "bitbucket.org"),
    ("GitHub", lambda host, path, title: host == "github.com"),
    ("Jenkins", lambda host, path, title: title.endswith("- Jenkins") or "jenkins" in host),
    ("Search", lambda host, path, title: path.startswith("/search") and ("google." in host or host == "search.brave.com")),
    ("Google Docs", lambda host, path, title: host == "docs.google.com"),
    ("Slack", lambda host, path, title: host.endswith("slack.com")),
    ("AWS Console", lambda host, path, title: host.endswith("console.aws.amazon.com")
        or host.endswith("awsapps.com")),
    ("Localhost", lambda host, path, title: host in {"localhost", "127.0.0.1"}),
]


def domain(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme in {"chrome", "brave", "about", "edge"}:
        name = parts.netloc or parts.path
        return f"{parts.scheme}-{name.strip('/')}" if name else parts.scheme
    host = (parts.hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def category(tab: Tab) -> str:
    host = domain(tab.url)
    path = urlsplit(tab.url).path
    for name, test in _RULES:
        if test(host, path, tab.title):
            return name
    return host or "other"


def age_bucket(age_days: float) -> str:
    if age_days < 1:
        return "age:new"
    if age_days < 7:
        return "age:days"
    if age_days < 30:
        return "age:week+"
    return "age:month+"


def title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z][a-z0-9]+", title.lower())
    return {w for w in words if len(w) >= 4 and w not in STOP_WORDS}


def extract_features(tab: Tab, age_days: float | None = None) -> set[str]:
    host = domain(tab.url)
    features = {f"cat:{category(tab)}", f"domain:{host}"}
    segments = [s for s in urlsplit(tab.url).path.split("/") if s]
    if segments and re.fullmatch(r"[a-z][a-z-]{1,30}", segments[0]):
        features.add(f"path:{segments[0]}")
    features |= {f"tok:{t}" for t in title_tokens(tab.title)}
    if age_days is not None:
        features.add(age_bucket(age_days))
    return features
