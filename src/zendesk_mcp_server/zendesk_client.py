import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import requests as _requests
from cachetools.func import ttl_cache

from zenpy import Zenpy

from zendesk_mcp_server.signals import (
    build_transcript,
    clean_comment_body,
    score_service_risk,
)


class ZendeskClient:
    def __init__(self, subdomain: str, email: str, token: str):
        """
        Initialize the Zendesk client using zenpy lib and direct API.
        """
        self.client = Zenpy(
            subdomain=subdomain,
            email=email,
            token=token
        )

        # For direct API calls
        self.subdomain = subdomain
        self.email = email
        self.token = token
        self.base_url = f"https://{subdomain}.zendesk.com/api/v2"
        # Create basic auth header
        credentials = f"{email}/token:{token}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode('ascii')
        self.auth_header = f"Basic {encoded_credentials}"

    def _request_json(
            self,
            path: str,
            params: Dict[str, Any] | None = None
    ) -> Dict[str, Any]:
        """Make an authenticated GET request to the Zendesk API."""
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{query}"

        request = urllib.request.Request(url)
        request.add_header('Authorization', self.auth_header)
        request.add_header('Accept', 'application/json')

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors='replace') if exc.fp else "No response body"
            raise Exception(
                f"Zendesk API request failed: HTTP {exc.code} - {exc.reason}. {body}"
            ) from exc

    @staticmethod
    def _validate_date(value: str, field: str) -> str:
        """Validate an ISO date or UTC datetime accepted by Zendesk search."""
        try:
            if 'T' in value:
                datetime.fromisoformat(value.replace('Z', '+00:00'))
            else:
                datetime.strptime(value, '%Y-%m-%d')
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{field} must be YYYY-MM-DD or an ISO 8601 datetime"
            ) from exc
        return value

    @staticmethod
    def _quote_search_value(value: str) -> str:
        """Quote a user-provided Zendesk search value when necessary."""
        value = str(value).strip()
        if not value:
            raise ValueError("Search filter values cannot be empty")
        if re.search(r'[\s":<>]', value):
            return f'"{value.replace(chr(34), chr(92) + chr(34))}"'
        return value

    @staticmethod
    def _relative_date_range(
            date_range: str,
            now: datetime | None = None
    ) -> tuple[str, str]:
        """Resolve a supported relative range into inclusive start/exclusive end dates."""
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        today = current.date()
        normalized = date_range.strip().lower()

        if normalized == 'this_week':
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=7)
        elif normalized == 'last_7_days':
            start = today - timedelta(days=6)
            end = today + timedelta(days=1)
        elif normalized == 'last_30_days':
            start = today - timedelta(days=29)
            end = today + timedelta(days=1)
        elif normalized == 'this_month':
            start = today.replace(day=1)
            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1)
            else:
                end = start.replace(month=start.month + 1)
        else:
            raise ValueError(
                "date_range must be one of: this_week, last_7_days, "
                "last_30_days, this_month"
            )

        return start.isoformat(), end.isoformat()

    def _build_search_query(
            self,
            *,
            tags: List[str] | None = None,
            match_all_tags: bool = True,
            created_after: str | None = None,
            created_before: str | None = None,
            updated_after: str | None = None,
            updated_before: str | None = None,
            date_range: str | None = None,
            priority: str | None = None,
            priority_operator: str = ':',
            status: str | None = None,
            status_operator: str = ':',
            group: str | int | None = None,
            assignee: str | int | None = None,
            organization: str | int | None = None,
            satisfaction: str | None = None,
            text: str | None = None,
            raw_query: str | None = None,
    ) -> str:
        """Build a Zendesk ticket-search query from validated filters."""
        parts = ['type:ticket']

        if date_range:
            if created_after or created_before:
                raise ValueError(
                    "date_range cannot be combined with created_after/created_before"
                )
            created_after, created_before = self._relative_date_range(date_range)

        if tags:
            normalized_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
            if match_all_tags and len(normalized_tags) > 1:
                joined = ' '.join(normalized_tags).replace('"', '\\"')
                parts.append(f'tags:"{joined}"')
            else:
                parts.extend(
                    f"tags:{self._quote_search_value(tag)}"
                    for tag in normalized_tags
                )

        date_filters = (
            ('created', '>=', created_after),
            ('created', '<', created_before),
            ('updated', '>=', updated_after),
            ('updated', '<', updated_before),
        )
        for field, operator, value in date_filters:
            if value:
                parts.append(
                    f"{field}{operator}{self._validate_date(value, field)}"
                )

        valid_operators = {':', '>', '<', '>=', '<='}
        if priority:
            if priority_operator not in valid_operators:
                raise ValueError("Invalid priority_operator")
            if priority not in {'low', 'normal', 'high', 'urgent'}:
                raise ValueError("priority must be low, normal, high, or urgent")
            parts.append(f"priority{priority_operator}{priority}")

        if status:
            if status_operator not in valid_operators:
                raise ValueError("Invalid status_operator")
            if status not in {'new', 'open', 'pending', 'hold', 'solved', 'closed'}:
                raise ValueError(
                    "status must be new, open, pending, hold, solved, or closed"
                )
            parts.append(f"status{status_operator}{status}")

        for field, value in (
            ('group', group),
            ('assignee', assignee),
            ('organization', organization),
        ):
            if value is not None:
                parts.append(
                    f"{field}:{self._quote_search_value(str(value))}"
                )

        if satisfaction:
            valid_satisfaction = {
                'bad', 'badwithcomment', 'good', 'goodwithcomment', 'offered'
            }
            if satisfaction not in valid_satisfaction:
                raise ValueError(f"Invalid satisfaction value: {satisfaction}")
            parts.append(f"satisfaction:{satisfaction}")

        if text:
            parts.append(self._quote_search_value(text))
        if raw_query:
            parts.append(raw_query.strip())

        return ' '.join(parts)

    def search_tickets(
            self,
            *,
            tags: List[str] | None = None,
            match_all_tags: bool = True,
            created_after: str | None = None,
            created_before: str | None = None,
            updated_after: str | None = None,
            updated_before: str | None = None,
            date_range: str | None = None,
            priority: str | None = None,
            priority_operator: str = ':',
            status: str | None = None,
            status_operator: str = ':',
            group: str | int | None = None,
            assignee: str | int | None = None,
            organization: str | int | None = None,
            satisfaction: str | None = None,
            text: str | None = None,
            raw_query: str | None = None,
            sort_by: str = 'created_at',
            sort_order: str = 'desc',
            page: int = 1,
            per_page: int = 25,
            count_only: bool = False,
    ) -> Dict[str, Any]:
        """Search tickets using Zendesk's native search syntax."""
        query = self._build_search_query(
            tags=tags,
            match_all_tags=match_all_tags,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            updated_before=updated_before,
            date_range=date_range,
            priority=priority,
            priority_operator=priority_operator,
            status=status,
            status_operator=status_operator,
            group=group,
            assignee=assignee,
            organization=organization,
            satisfaction=satisfaction,
            text=text,
            raw_query=raw_query,
        )

        if count_only:
            data = self._request_json('search/count.json', {'query': query})
            return {'query': query, 'count': data.get('count', 0)}

        if sort_by not in {
            'updated_at', 'created_at', 'priority', 'status', 'ticket_type'
        }:
            raise ValueError("Invalid sort_by value")
        if sort_order not in {'asc', 'desc'}:
            raise ValueError("sort_order must be asc or desc")
        page = max(1, int(page))
        per_page = min(max(1, int(per_page)), 100)
        result_offset = (page - 1) * per_page
        if result_offset >= 1000:
            raise ValueError(
                "Zendesk Search API exposes only the first 1,000 results. "
                "Narrow the filters or use the Search Export API."
            )

        data = self._request_json(
            'search.json',
            {
                'query': query,
                'sort_by': sort_by,
                'sort_order': sort_order,
                'page': page,
                'per_page': per_page,
            }
        )
        tickets = [
            {
                'id': ticket.get('id'),
                'url': ticket.get('url'),
                'subject': ticket.get('subject'),
                'description': ticket.get('description'),
                'status': ticket.get('status'),
                'priority': ticket.get('priority'),
                'tags': ticket.get('tags', []),
                'created_at': ticket.get('created_at'),
                'updated_at': ticket.get('updated_at'),
                'requester_id': ticket.get('requester_id'),
                'assignee_id': ticket.get('assignee_id'),
                'group_id': ticket.get('group_id'),
                'organization_id': ticket.get('organization_id'),
                'satisfaction_rating': ticket.get('satisfaction_rating'),
            }
            for ticket in data.get('results', [])
            if ticket.get('result_type', 'ticket') == 'ticket'
        ]
        has_more = (
            data.get('next_page') is not None
            and result_offset + len(tickets) < 1000
        )
        return {
            'query': query,
            'count': data.get('count', len(tickets)),
            'tickets': tickets,
            'page': page,
            'per_page': per_page,
            'result_ceiling': 1000,
            'has_more': has_more,
            'next_page': page + 1 if has_more else None,
        }

    @ttl_cache(ttl=3600)
    def list_popular_tags(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Return the account's most-used recent tags with ticket counts."""
        limit = min(max(1, int(limit)), 1000)
        tags: List[Dict[str, Any]] = []
        page = 1
        while len(tags) < limit:
            data = self._request_json(
                'tags.json',
                {'page': page, 'per_page': min(100, limit - len(tags))}
            )
            page_tags = data.get('tags', [])
            tags.extend(
                {
                    'name': item.get('name'),
                    'ticket_count': item.get('count', 0),
                }
                for item in page_tags
                if item.get('name')
            )
            if not data.get('next_page') or not page_tags:
                break
            page += 1
        return tags[:limit]

    def autocomplete_tags(
            self,
            term: str,
            limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Find real account tags matching a term and attach usage counts."""
        term = term.strip()
        if len(term) < 2:
            raise ValueError("Tag discovery term must contain at least 2 characters")
        limit = min(max(1, int(limit)), 15)
        data = self._request_json(
            'autocomplete/tags.json',
            {'name': term, 'per_page': limit}
        )
        names = data.get('tags', [])[:limit]
        popular = {
            item['name'].lower(): item['ticket_count']
            for item in self.list_popular_tags(200)
        }
        results = []
        for name in names:
            count = popular.get(name.lower())
            if count is None:
                count = self.search_tickets(tags=[name], count_only=True)['count']
            results.append({'name': name, 'ticket_count': count})
        return sorted(
            results,
            key=lambda item: (
                item['name'].lower() != term.lower(),
                -item['ticket_count'],
                item['name'].lower(),
            )
        )

    def autocomplete_organizations(
            self,
            term: str,
            limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Find organizations by a partial name."""
        term = term.strip()
        if len(term) < 2:
            raise ValueError(
                "Organization discovery term must contain at least 2 characters"
            )
        data = self._request_json(
            'organizations/autocomplete.json',
            {'name': term}
        )
        return [
            {
                'id': organization.get('id'),
                'name': organization.get('name'),
            }
            for organization in data.get('organizations', [])[:limit]
        ]

    @ttl_cache(ttl=3600)
    def list_groups(self) -> List[Dict[str, Any]]:
        """List active Zendesk groups for filter resolution."""
        groups: List[Dict[str, Any]] = []
        page = 1
        while True:
            data = self._request_json(
                'groups.json',
                {'page': page, 'per_page': 100}
            )
            page_groups = data.get('groups', [])
            groups.extend(
                {
                    'id': group.get('id'),
                    'name': group.get('name'),
                    'deleted': group.get('deleted', False),
                }
                for group in page_groups
                if group.get('name') and not group.get('deleted', False)
            )
            if not data.get('next_page') or not page_groups:
                break
            page += 1
        return groups

    def discover_filters(
            self,
            term: str,
            dimensions: List[str] | None = None,
            limit: int = 10
    ) -> Dict[str, Any]:
        """Discover account-backed filter values for a vague user term."""
        limit = min(max(1, int(limit)), 15)
        requested = set(dimensions or ['tags', 'organizations', 'groups', 'severity'])
        allowed = {'tags', 'organizations', 'groups', 'severity', 'priority', 'status'}
        unknown = requested - allowed
        if unknown:
            raise ValueError(
                f"Unknown discovery dimensions: {', '.join(sorted(unknown))}"
            )

        result: Dict[str, Any] = {
            'term': term,
            'priority_values': ['urgent', 'high', 'normal', 'low'],
            'status_values': ['new', 'open', 'pending', 'hold', 'solved', 'closed'],
        }
        if 'tags' in requested:
            result['tags'] = self.autocomplete_tags(term, limit)
        if 'organizations' in requested:
            result['organizations'] = self.autocomplete_organizations(term, limit)
        if 'groups' in requested:
            lowered = term.lower()
            result['groups'] = [
                group for group in self.list_groups()
                if lowered in group['name'].lower()
            ][:limit]
        if 'severity' in requested:
            severity_tags: Dict[str, Dict[str, Any]] = {}
            prefixes = [
                'sev', 'severity',
                *(f'p{level}' for level in range(1, 6)),
                *(f's{level}' for level in range(1, 6)),
            ]
            for prefix in prefixes:
                for item in self.autocomplete_tags(prefix, limit):
                    name = item['name'].lower()
                    if re.search(r'(?:^|[_-])(sev(?:erity)?|p|s)[_-]?[0-5](?:$|[_-])', name):
                        severity_tags[name] = item
            result['severity_tag_candidates'] = sorted(
                severity_tags.values(),
                key=lambda item: (-item['ticket_count'], item['name'])
            )[:limit]
        return result

    def get_filter_vocabulary(self, tag_limit: int = 200) -> Dict[str, Any]:
        """Return a compact vocabulary suitable for the MCP resource."""
        tags = self.list_popular_tags(tag_limit)
        severity_pattern = re.compile(
            r'(?:^|[_-])(sev(?:erity)?|p|s)[_-]?[0-5](?:$|[_-])'
        )
        return {
            'tags': tags,
            'groups': self.list_groups(),
            'priority_values': ['urgent', 'high', 'normal', 'low'],
            'status_values': ['new', 'open', 'pending', 'hold', 'solved', 'closed'],
            'severity_tag_candidates': [
                item for item in tags
                if severity_pattern.search(item['name'].lower())
            ],
        }

    def get_ticket(self, ticket_id: int) -> Dict[str, Any]:
        """
        Query a ticket by its ID
        """
        try:
            ticket = self.client.tickets(id=ticket_id)
            return {
                'id': ticket.id,
                'subject': ticket.subject,
                'description': ticket.description,
                'status': ticket.status,
                'priority': ticket.priority,
                'created_at': str(ticket.created_at),
                'updated_at': str(ticket.updated_at),
                'requester_id': ticket.requester_id,
                'assignee_id': ticket.assignee_id,
                'organization_id': ticket.organization_id
            }
        except Exception as e:
            raise Exception(f"Failed to get ticket {ticket_id}: {str(e)}")

    def get_ticket_comments(self, ticket_id: int) -> List[Dict[str, Any]]:
        """
        Get all comments for a specific ticket, including attachment metadata.
        """
        try:
            comments = self.client.tickets.comments(ticket=ticket_id)
            result = []
            for comment in comments:
                attachments = []
                for a in getattr(comment, 'attachments', []) or []:
                    attachments.append({
                        'id': a.id,
                        'file_name': a.file_name,
                        'content_url': a.content_url,
                        'content_type': a.content_type,
                        'size': a.size,
                    })
                result.append({
                    'id': comment.id,
                    'author_id': comment.author_id,
                    'body': comment.body,
                    'html_body': comment.html_body,
                    'public': comment.public,
                    'created_at': str(comment.created_at),
                    'attachments': attachments,
                })
            return result
        except Exception as e:
            raise Exception(f"Failed to get comments for ticket {ticket_id}: {str(e)}")

    # Allowed image MIME types. SVG is excluded — it can contain active XML/JS content.
    _ALLOWED_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}

    # Magic bytes (file signatures) for each allowed type.
    _MAGIC_BYTES: Dict[str, List[bytes]] = {
        'image/jpeg': [b'\xff\xd8\xff'],
        'image/png':  [b'\x89PNG\r\n\x1a\n'],
        'image/gif':  [b'GIF87a', b'GIF89a'],
        'image/webp': [b'RIFF'],  # RIFF....WEBP — checked further below
    }

    # 10 MB hard cap to guard against image bombs and token budget blowout.
    _MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

    def get_ticket_attachment(self, content_url: str) -> Dict[str, Any]:
        """
        Fetch an image attachment and return base64-encoded data.

        Security measures applied:
        - Allowlist of safe image MIME types (no SVG or arbitrary binary).
        - Magic byte validation so the file header must match the declared type.
        - 10 MB size cap to prevent image bombs and excessive token usage.

        Zendesk attachment URLs redirect to zdusercontent.com (Zendesk's CDN).
        requests strips the Authorization header on cross-origin redirects,
        which is required — the CDN returns 403 if it receives an auth header.
        """
        try:
            response = _requests.get(
                content_url,
                headers={'Authorization': self.auth_header},
                timeout=30,
                stream=True,
            )
            response.raise_for_status()

            content_type = response.headers.get('Content-Type', '').split(';')[0].strip().lower()

            if content_type not in self._ALLOWED_IMAGE_TYPES:
                raise ValueError(
                    f"Attachment type '{content_type}' is not allowed. "
                    f"Supported types: {sorted(self._ALLOWED_IMAGE_TYPES)}"
                )

            # Read with size cap — stops download as soon as limit is exceeded.
            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=65536):
                total += len(chunk)
                if total > self._MAX_ATTACHMENT_BYTES:
                    raise ValueError(
                        f"Attachment exceeds the {self._MAX_ATTACHMENT_BYTES // (1024*1024)} MB size limit."
                    )
                chunks.append(chunk)
            content = b''.join(chunks)

            # Validate magic bytes to catch MIME type spoofing.
            magic_signatures = self._MAGIC_BYTES.get(content_type, [])
            if magic_signatures and not any(content.startswith(sig) for sig in magic_signatures):
                raise ValueError(
                    f"File header does not match declared content type '{content_type}'. "
                    "The attachment may be spoofed."
                )
            # Extra check for WebP: bytes 8–12 must be b'WEBP'.
            if content_type == 'image/webp' and content[8:12] != b'WEBP':
                raise ValueError("File header does not match declared content type 'image/webp'.")

            return {
                'data': base64.b64encode(content).decode('ascii'),
                'content_type': content_type,
            }
        except (ValueError, _requests.HTTPError):
            raise
        except Exception as e:
            raise Exception(f"Failed to fetch attachment from {content_url}: {str(e)}")

    def get_tickets_with_metrics(
            self,
            ticket_ids: List[int]
    ) -> Dict[str, Any]:
        """Fetch up to 100 tickets with related users, groups, and metrics."""
        normalized_ids = list(dict.fromkeys(int(ticket_id) for ticket_id in ticket_ids))
        if not normalized_ids:
            raise ValueError("At least one ticket_id is required")
        if len(normalized_ids) > 100:
            raise ValueError("Zendesk show_many supports at most 100 ticket IDs")

        data = self._request_json(
            'tickets/show_many.json',
            {
                'ids': ','.join(str(ticket_id) for ticket_id in normalized_ids),
                'include': 'metric_sets,users,groups,organizations',
            }
        )
        metric_sets = (
            data.get('ticket_metric_sets')
            or data.get('metric_sets')
            or []
        )
        return {
            'tickets': data.get('tickets', []),
            'metrics': {
                item.get('ticket_id'): item
                for item in metric_sets
                if item.get('ticket_id') is not None
            },
            'users': {
                item.get('id'): item
                for item in data.get('users', [])
                if item.get('id') is not None
            },
            'groups': {
                item.get('id'): item
                for item in data.get('groups', [])
                if item.get('id') is not None
            },
            'organizations': {
                item.get('id'): item
                for item in data.get('organizations', [])
                if item.get('id') is not None
            },
        }

    def _get_users(self, user_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """Fetch users in batches for transcript role and name attribution."""
        unique_ids = list(dict.fromkeys(
            int(user_id) for user_id in user_ids if user_id is not None
        ))
        users: Dict[int, Dict[str, Any]] = {}
        for index in range(0, len(unique_ids), 100):
            batch = unique_ids[index:index + 100]
            data = self._request_json(
                'users/show_many.json',
                {'ids': ','.join(str(user_id) for user_id in batch)}
            )
            users.update({
                user.get('id'): user
                for user in data.get('users', [])
                if user.get('id') is not None
            })
        return users

    @staticmethod
    def _parse_utc(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None

    @staticmethod
    def _named_entity(
            entity_id: int | None,
            entities: Dict[int, Dict[str, Any]]
    ) -> Dict[str, Any] | None:
        if entity_id is None:
            return None
        entity = entities.get(entity_id, {})
        return {'id': entity_id, 'name': entity.get('name')}

    def get_ticket_digests(
            self,
            ticket_ids: List[int],
            detail: str = 'summary',
            max_chars: int = 800,
            max_comments: int = 30,
            include_internal_notes: bool = False,
    ) -> Dict[str, Any]:
        """Return compact ticket narratives and optional clean transcripts."""
        normalized_ids = list(dict.fromkeys(int(ticket_id) for ticket_id in ticket_ids))
        if detail not in {'summary', 'transcript'}:
            raise ValueError("detail must be summary or transcript")
        maximum = 10 if detail == 'transcript' else 25
        if not normalized_ids:
            raise ValueError("At least one ticket_id is required")
        if len(normalized_ids) > maximum:
            raise ValueError(
                f"detail={detail} supports at most {maximum} ticket IDs"
            )
        max_chars = min(max(100, int(max_chars)), 50000)
        max_comments = min(max(1, int(max_comments)), 100)

        related = self.get_tickets_with_metrics(normalized_ids)
        tickets_by_id = {
            ticket.get('id'): ticket
            for ticket in related['tickets']
            if ticket.get('id') is not None
        }

        comments_by_ticket: Dict[int, List[Dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=min(5, len(normalized_ids))) as executor:
            futures = {
                executor.submit(self.get_ticket_comments, ticket_id): ticket_id
                for ticket_id in normalized_ids
            }
            for future in as_completed(futures):
                ticket_id = futures[future]
                comments_by_ticket[ticket_id] = future.result()

        users = dict(related['users'])
        missing_user_ids = {
            comment.get('author_id')
            for comments in comments_by_ticket.values()
            for comment in comments
            if comment.get('author_id') not in users
        }
        users.update(self._get_users(list(missing_user_ids)))

        now = datetime.now(timezone.utc)
        digests = []
        for ticket_id in normalized_ids:
            ticket = tickets_by_id.get(ticket_id)
            if not ticket:
                continue
            comments = sorted(
                comments_by_ticket.get(ticket_id, []),
                key=lambda item: item.get('created_at') or ''
            )
            public_comments = [
                item for item in comments if item.get('public', False)
            ]
            requester_id = ticket.get('requester_id')
            customer_comments = [
                item for item in public_comments
                if item.get('author_id') == requester_id
                or users.get(item.get('author_id'), {}).get('role') == 'end-user'
            ]
            agent_comments = [
                item for item in public_comments
                if item not in customer_comments
            ]

            def comment_summary(
                    comment: Dict[str, Any] | None
            ) -> Dict[str, Any] | None:
                if not comment:
                    return None
                author = users.get(comment.get('author_id'), {})
                return {
                    'text': clean_comment_body(comment.get('body'), max_chars),
                    'author': author.get('name'),
                    'created_at': comment.get('created_at'),
                }

            created_at = self._parse_utc(ticket.get('created_at'))
            updated_at = self._parse_utc(ticket.get('updated_at'))
            metrics = related['metrics'].get(ticket_id, {})
            digest: Dict[str, Any] = {
                'id': ticket_id,
                'url': (
                    f"https://{self.subdomain}.zendesk.com/agent/tickets/{ticket_id}"
                ),
                'subject': ticket.get('subject'),
                'status': ticket.get('status'),
                'priority': ticket.get('priority'),
                'tags': ticket.get('tags', []),
                'created_at': ticket.get('created_at'),
                'updated_at': ticket.get('updated_at'),
                'age_days': (
                    round((now - created_at).total_seconds() / 86400, 1)
                    if created_at else None
                ),
                'days_since_update': (
                    round((now - updated_at).total_seconds() / 86400, 1)
                    if updated_at else None
                ),
                'requester': self._named_entity(requester_id, users),
                'assignee': self._named_entity(
                    ticket.get('assignee_id'), users
                ),
                'group': self._named_entity(
                    ticket.get('group_id'), related['groups']
                ),
                'organization': self._named_entity(
                    ticket.get('organization_id'), related['organizations']
                ),
                'reported': comment_summary(
                    public_comments[0] if public_comments else None
                ),
                'latest_customer_message': comment_summary(
                    customer_comments[-1] if customer_comments else None
                ),
                'latest_agent_reply': comment_summary(
                    agent_comments[-1] if agent_comments else None
                ),
                'comment_stats': {
                    'total': len(comments),
                    'public': len(public_comments),
                    'from_requester': len(customer_comments),
                    'from_agents': len(agent_comments),
                },
                'metrics': {
                    key: metrics.get(key)
                    for key in (
                        'reopens',
                        'replies',
                        'group_stations',
                        'assignee_stations',
                        'requester_wait_time_in_minutes',
                        'first_resolution_time_in_minutes',
                        'full_resolution_time_in_minutes',
                    )
                },
                'service_signals': score_service_risk(ticket, metrics),
            }
            if detail == 'transcript':
                digest['transcript'] = build_transcript(
                    ticket,
                    comments,
                    users,
                    max_comments=max_comments,
                    max_chars=max_chars,
                    include_internal_notes=include_internal_notes,
                )
            digests.append(digest)

        return {
            'detail': detail,
            'count': len(digests),
            'digests': digests,
        }

    def get_tickets(self, page: int = 1, per_page: int = 25, sort_by: str = 'created_at', sort_order: str = 'desc') -> Dict[str, Any]:
        """
        Get the latest tickets with proper pagination support using direct API calls.

        Args:
            page: Page number (1-based)
            per_page: Number of tickets per page (max 100)
            sort_by: Field to sort by (created_at, updated_at, priority, status)
            sort_order: Sort order (asc or desc)

        Returns:
            Dict containing tickets and pagination info
        """
        try:
            # Cap at reasonable limit
            per_page = min(per_page, 100)

            # Build URL with parameters for offset pagination
            params = {
                'page': str(page),
                'per_page': str(per_page),
                'sort_by': sort_by,
                'sort_order': sort_order
            }
            query_string = urllib.parse.urlencode(params)
            url = f"{self.base_url}/tickets.json?{query_string}"

            # Create request with auth header
            req = urllib.request.Request(url)
            req.add_header('Authorization', self.auth_header)
            req.add_header('Content-Type', 'application/json')

            # Make the API request
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())

            tickets_data = data.get('tickets', [])

            # Process tickets to return only essential fields
            ticket_list = []
            for ticket in tickets_data:
                ticket_list.append({
                    'id': ticket.get('id'),
                    'subject': ticket.get('subject'),
                    'status': ticket.get('status'),
                    'priority': ticket.get('priority'),
                    'description': ticket.get('description'),
                    'created_at': ticket.get('created_at'),
                    'updated_at': ticket.get('updated_at'),
                    'requester_id': ticket.get('requester_id'),
                    'assignee_id': ticket.get('assignee_id')
                })

            return {
                'tickets': ticket_list,
                'page': page,
                'per_page': per_page,
                'count': len(ticket_list),
                'sort_by': sort_by,
                'sort_order': sort_order,
                'has_more': data.get('next_page') is not None,
                'next_page': page + 1 if data.get('next_page') else None,
                'previous_page': page - 1 if data.get('previous_page') and page > 1 else None
            }
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else "No response body"
            raise Exception(f"Failed to get latest tickets: HTTP {e.code} - {e.reason}. {error_body}")
        except Exception as e:
            raise Exception(f"Failed to get latest tickets: {str(e)}")

    def get_all_articles(self) -> Dict[str, Any]:
        """
        Fetch help center articles as knowledge base.
        Returns a Dict of section -> [article].
        """
        try:
            # Get all sections
            sections = self.client.help_center.sections()

            # Get articles for each section
            kb = {}
            for section in sections:
                articles = self.client.help_center.sections.articles(section.id)
                kb[section.name] = {
                    'section_id': section.id,
                    'description': section.description,
                    'articles': [{
                        'id': article.id,
                        'title': article.title,
                        'body': article.body,
                        'updated_at': str(article.updated_at),
                        'url': article.html_url
                    } for article in articles]
                }

            return kb
        except Exception as e:
            raise Exception(f"Failed to fetch knowledge base: {str(e)}")