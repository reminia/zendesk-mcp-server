from typing import Dict, Any, List
import json
import threading
import urllib.request
import urllib.parse
import base64
import requests as _requests

from zenpy import Zenpy
from zenpy.lib.api_objects import Comment
from zenpy.lib.api_objects import Ticket as ZenpyTicket


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

        self._lock = threading.Lock()

        # For direct API calls
        self.subdomain = subdomain
        self.email = email
        self.token = token
        self.base_url = f"https://{subdomain}.zendesk.com/api/v2"
        # Create basic auth header
        credentials = f"{email}/token:{token}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode('ascii')
        self.auth_header = f"Basic {encoded_credentials}"

    @staticmethod
    def _serialize_custom_fields(custom_fields: Any) -> List[Dict[str, Any]]:
        if not custom_fields:
            return []
        return [
            {'id': getattr(cf, 'id', cf.get('id')), 'value': getattr(cf, 'value', cf.get('value'))}
            if isinstance(cf, dict) else {'id': cf.id, 'value': cf.value}
            for cf in custom_fields
        ]

    def get_ticket(self, ticket_id: int) -> Dict[str, Any]:
        """
        Query a ticket by its ID
        """
        try:
            with self._lock:
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
                'organization_id': ticket.organization_id,
                'custom_fields': self._serialize_custom_fields(
                    getattr(ticket, 'custom_fields', []) or []
                ),
            }
        except Exception as e:
            raise Exception(f"Failed to get ticket {ticket_id}: {str(e)}")

    def get_ticket_comments(self, ticket_id: int) -> List[Dict[str, Any]]:
        """
        Get all comments for a specific ticket, including attachment metadata.
        """
        try:
            with self._lock:
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

    def post_comment(self, ticket_id: int, comment: str, public: bool = True) -> str:
        """
        Post a comment to an existing ticket.
        """
        try:
            with self._lock:
                ticket = self.client.tickets(id=ticket_id)
                ticket.comment = Comment(
                    html_body=comment,
                    public=public
                )
                self.client.tickets.update(ticket)
            return comment
        except Exception as e:
            raise Exception(f"Failed to post comment on ticket {ticket_id}: {str(e)}")

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
                    'assignee_id': ticket.get('assignee_id'),
                    'custom_fields': ticket.get('custom_fields', []),
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

    def get_ticket_fields(self) -> List[Dict[str, Any]]:
        """
        Fetch all ticket fields (system + custom) using direct API.

        Returns each field's id, title, type, description, required status,
        active status, and (for dropdowns) the available custom_field_options.
        """
        try:
            url = f"{self.base_url}/ticket_fields.json"
            req = urllib.request.Request(url)
            req.add_header('Authorization', self.auth_header)
            req.add_header('Content-Type', 'application/json')

            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())

            fields = data.get('ticket_fields', [])
            result = []
            for field in fields:
                entry = {
                    'id': field.get('id'),
                    'title': field.get('title'),
                    'type': field.get('type'),
                    'description': field.get('description', ''),
                    'active': field.get('active', False),
                    'required': field.get('required', False),
                    'position': field.get('position'),
                    'visible_in_portal': field.get('visible_in_portal', False),
                    'agent_can_edit': field.get('agent_can_edit', True),
                }
                # Include dropdown options if present (custom_field_options)
                options = field.get('custom_field_options')
                if options:
                    entry['options'] = [
                        {
                            'id': o.get('id'),
                            'name': o.get('name'),
                            'value': o.get('value'),
                            'position': o.get('position', 0),
                        }
                        for o in options
                    ]
                result.append(entry)
            return result
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else "No response body"
            raise Exception(f"Failed to get ticket fields: HTTP {e.code} - {e.reason}. {error_body}")
        except Exception as e:
            raise Exception(f"Failed to get ticket fields: {str(e)}")

    def get_all_articles(self) -> Dict[str, Any]:
        """
        Fetch help center articles as knowledge base.
        Returns a Dict of section -> [article].
        """
        try:
            with self._lock:
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

    def create_ticket(
        self,
        subject: str,
        description: str,
        requester_id: int | None = None,
        assignee_id: int | None = None,
        priority: str | None = None,
        type: str | None = None,
        tags: List[str] | None = None,
        custom_fields: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        """
        Create a new Zendesk ticket using Zenpy and return essential fields.

        Args:
            subject: Ticket subject
            description: Ticket description (plain text). Will also be used as initial comment.
            requester_id: Optional requester user ID
            assignee_id: Optional assignee user ID
            priority: Optional priority (low, normal, high, urgent)
            type: Optional ticket type (problem, incident, question, task)
            tags: Optional list of tags
            custom_fields: Optional list of dicts: {id: int, value: Any}
        """
        try:
            with self._lock:
                ticket = ZenpyTicket(
                    subject=subject,
                    description=description,
                    requester_id=requester_id,
                    assignee_id=assignee_id,
                    priority=priority,
                    type=type,
                    tags=tags,
                    custom_fields=custom_fields,
                )
                created_audit = self.client.tickets.create(ticket)
                # Fetch created ticket id from audit
                created_ticket_id = getattr(getattr(created_audit, 'ticket', None), 'id', None)
                if created_ticket_id is None:
                    # Fallback: try to read id from audit events
                    created_ticket_id = getattr(created_audit, 'id', None)

                # Fetch full ticket to return consistent data
                created = self.client.tickets(id=created_ticket_id) if created_ticket_id else None

            return {
                'id': getattr(created, 'id', created_ticket_id),
                'subject': getattr(created, 'subject', subject),
                'description': getattr(created, 'description', description),
                'status': getattr(created, 'status', 'new'),
                'priority': getattr(created, 'priority', priority),
                'type': getattr(created, 'type', type),
                'created_at': str(getattr(created, 'created_at', '')),
                'updated_at': str(getattr(created, 'updated_at', '')),
                'requester_id': getattr(created, 'requester_id', requester_id),
                'assignee_id': getattr(created, 'assignee_id', assignee_id),
                'organization_id': getattr(created, 'organization_id', None),
                'tags': list(getattr(created, 'tags', tags or []) or []),
                'custom_fields': self._serialize_custom_fields(
                    getattr(created, 'custom_fields', []) or []
                ),
            }
        except Exception as e:
            raise Exception(f"Failed to create ticket: {str(e)}")

    def update_ticket(self, ticket_id: int, **fields: Any) -> Dict[str, Any]:
        """
        Update a Zendesk ticket with provided fields using Zenpy.

        Supported fields include common ticket attributes like:
        subject, status, priority, type, assignee_id, requester_id,
        tags (list[str]), custom_fields (list[dict]), due_at, etc.
        """
        try:
            with self._lock:
                ticket = self.client.tickets(id=ticket_id)
                for key, value in fields.items():
                    if value is None:
                        continue
                    setattr(ticket, key, value)

                # This call returns a TicketAudit (not a Ticket). Don't read attrs from it.
                self.client.tickets.update(ticket)

                # Fetch the fresh ticket to return consistent data
                refreshed = self.client.tickets(id=ticket_id)

            return {
                'id': refreshed.id,
                'subject': refreshed.subject,
                'description': refreshed.description,
                'status': refreshed.status,
                'priority': refreshed.priority,
                'type': getattr(refreshed, 'type', None),
                'created_at': str(refreshed.created_at),
                'updated_at': str(refreshed.updated_at),
                'requester_id': refreshed.requester_id,
                'assignee_id': refreshed.assignee_id,
                'organization_id': refreshed.organization_id,
                'tags': list(getattr(refreshed, 'tags', []) or []),
                'custom_fields': self._serialize_custom_fields(
                    getattr(refreshed, 'custom_fields', []) or []
                ),
            }
        except Exception as e:
            raise Exception(f"Failed to update ticket {ticket_id}: {str(e)}")