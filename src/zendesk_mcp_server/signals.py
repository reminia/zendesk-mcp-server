"""Text preparation and objective service-risk signals for ticket reports."""

import re
from typing import Any, Dict, List


_REPLY_HEADER = re.compile(
    r'(?im)^\s*On .+? wrote:\s*$|^\s*From:\s+.+$|^\s*Sent:\s+.+$'
)
_SIGNATURE = re.compile(r'(?m)^\s*(?:--\s*|_{3,}|-{3,})\s*$')


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text on a word boundary."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    clipped = text[:max_chars].rsplit(' ', 1)[0].rstrip()
    return f"{clipped or text[:max_chars].rstrip()}…"


def clean_comment_body(text: str | None, max_chars: int = 0) -> str:
    """Remove quoted email history and signatures from a Zendesk comment."""
    if not text:
        return ''

    lines = []
    for line in str(text).replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if line.lstrip().startswith('>'):
            continue
        if _REPLY_HEADER.match(line) or _SIGNATURE.match(line):
            break
        lines.append(line)

    cleaned = re.sub(r'\s+', ' ', ' '.join(lines)).strip()
    return _truncate(cleaned, max_chars)


def _select_comments(
        comments: List[Dict[str, Any]],
        max_comments: int
) -> List[Dict[str, Any]]:
    """Retain the first comment plus the newest comments when capped."""
    ordered = sorted(comments, key=lambda item: item.get('created_at') or '')
    if max_comments <= 0 or len(ordered) <= max_comments:
        return ordered
    if max_comments == 1:
        return ordered[:1]
    return [ordered[0], *ordered[-(max_comments - 1):]]


def build_transcript(
        ticket: Dict[str, Any],
        comments: List[Dict[str, Any]],
        users: Dict[int, Dict[str, Any]],
        max_comments: int = 30,
        max_chars: int = 12000,
        include_internal_notes: bool = False,
) -> str:
    """Create a compact, role-labelled ticket conversation."""
    requester_id = ticket.get('requester_id')
    visible = [
        comment for comment in comments
        if include_internal_notes or comment.get('public', False)
    ]
    selected = _select_comments(visible, max_comments)
    lines: List[str] = []

    for comment in selected:
        body = clean_comment_body(comment.get('body'))
        if not body:
            continue
        author_id = comment.get('author_id')
        author = users.get(author_id, {})
        if author_id == requester_id or author.get('role') == 'end-user':
            role = 'Customer'
        elif comment.get('public', False):
            role = 'Agent'
        else:
            role = 'Internal note'
        name = author.get('name') or f"User {author_id}"
        created_at = comment.get('created_at') or 'unknown time'
        lines.append(f"[{created_at}] {role} ({name}): {body}")

    return _truncate('\n'.join(lines), max_chars)


def _metric_number(value: Any) -> float:
    """Extract the calendar value used by Zendesk duration metrics."""
    if isinstance(value, dict):
        value = value.get('calendar', value.get('business', 0))
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0


def score_service_risk(
        ticket: Dict[str, Any],
        metrics: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Score objective service-risk indicators without inferring sentiment."""
    metrics = metrics or {}
    score = 0
    reasons: List[str] = []

    reopens = int(_metric_number(metrics.get('reopens')))
    if reopens:
        score += min(25, reopens * 10)
        reasons.append(f"reopened {reopens} time{'s' if reopens != 1 else ''}")

    group_stations = int(_metric_number(metrics.get('group_stations')))
    if group_stations >= 3:
        score += 10
        reasons.append(f"handed between {group_stations} groups")

    assignee_stations = int(_metric_number(metrics.get('assignee_stations')))
    if assignee_stations >= 3:
        score += 10
        reasons.append(f"handled by {assignee_stations} assignees")

    wait_minutes = _metric_number(
        metrics.get('requester_wait_time_in_minutes')
    )
    if wait_minutes >= 24 * 60:
        score += 15
        wait_days = wait_minutes / (24 * 60)
        reasons.append(f"customer waited {wait_days:.1f} days for replies")

    escalation_tags = {
        'escalated', 'escalation', 'complaint', 'churn_risk', 'churn-risk'
    }
    matched_tags = sorted(
        escalation_tags.intersection(
            str(tag).lower() for tag in ticket.get('tags', [])
        )
    )
    if matched_tags:
        score += 20
        reasons.append(f"escalation tags: {', '.join(matched_tags)}")

    satisfaction = ticket.get('satisfaction_rating') or {}
    if satisfaction.get('score') == 'bad':
        score += 50
        reasons.append("customer submitted a bad satisfaction rating")

    return {
        'risk_score': min(100, score),
        'reasons': reasons,
        'is_sentiment_verdict': False,
    }
