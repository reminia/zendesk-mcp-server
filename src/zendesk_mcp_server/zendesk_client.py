from typing import Dict, Any, List
import json
import urllib.request
import urllib.parse
import base64
import requests as _requests

from markdown_it import MarkdownIt
from zenpy import Zenpy
from zenpy.lib.api_objects import Comment
from zenpy.lib.api_objects import Ticket as ZenpyTicket

# CommonMark renderer used for ticket comments.
#   breaks=True -> a single newline becomes <br>, so plain text and
#                  soft-wrapped prose keep their line breaks.
#   html=True   -> raw HTML embedded in the input is passed through rather
#                  than escaped, so a caller can still hand-write HTML.
#                  Zendesk sanitizes html_body server-side (stripping scripts,
#                  event handlers, etc.), which is the safety boundary.
# table/strikethrough are enabled because agents commonly use them.
_MD = MarkdownIt("commonmark", {"breaks": True, "html": True}).enable(["table", "strikethrough"])


def markdown_to_html(text: str) -> str:
    """
    Render Markdown (or plain text) to the HTML that Zendesk stores as html_body.

    Zendesk renders a comment's html_body as HTML, which collapses runs of
    whitespace, so plain text posted verbatim loses all its newlines and any
    Markdown appears as literal characters. Rendering here keeps both intact:
    newlines survive (breaks=True) and Markdown syntax (bold, lists, links,
    code, tables) becomes real HTML. Raw HTML in the input is preserved and
    left for Zendesk to sanitize.
    """
    return _MD.render(text or "")


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

    def post_comment(self, ticket_id: int, comment: str, public: bool = True) -> str:
        """
        Post a comment to an existing ticket.

        The comment is treated as Markdown (plain text is valid Markdown) and
        rendered to HTML so newlines and formatting survive in Zendesk.
        """
        try:
            ticket = self.client.tickets(id=ticket_id)
            ticket.comment = Comment(
                html_body=markdown_to_html(comment),
                public=public
            )
            self.client.tickets.update(ticket)
            return comment
        except Exception as e:
            raise Exception(f"Failed to post comment on ticket {ticket_id}: {str(e)}")

    def _search_tickets_by_status(self, status: str, page: int = 1, per_page: int = 25, sort_by: str = 'created_at', sort_order: str = 'desc') -> Dict[str, Any]:
        """
        Search tickets by status using the Zendesk Search API.

        Args:
            status: Status to filter by (new, open, pending, hold, solved, closed)
            page: Page number (1-based)
            per_page: Number of tickets per page (max 100)
            sort_by: Field to sort by
            sort_order: Sort order (asc or desc)

        Returns:
            Dict containing tickets and pagination info
        """
        # Build search query
        query = f"type:ticket status:{status}"

        params = {
            'query': query,
            'page': str(page),
            'per_page': str(per_page),
            'sort_by': sort_by,
            'sort_order': sort_order
        }
        query_string = urllib.parse.urlencode(params)
        url = f"{self.base_url}/search.json?{query_string}"

        req = urllib.request.Request(url)
        req.add_header('Authorization', self.auth_header)
        req.add_header('Content-Type', 'application/json')

        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())

        results = data.get('results', [])

        ticket_list = []
        for ticket in results:
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
            'total_count': data.get('count', len(ticket_list)),
            'status_filter': status,
            'sort_by': sort_by,
            'sort_order': sort_order,
            'has_more': data.get('next_page') is not None,
            'next_page': page + 1 if data.get('next_page') else None,
            'previous_page': page - 1 if data.get('previous_page') and page > 1 else None
        }

    def get_tickets(self, page: int = 1, per_page: int = 25, sort_by: str = 'created_at', sort_order: str = 'desc', view_id: int | None = None, status: str | None = None) -> Dict[str, Any]:
        """
        Get the latest tickets with proper pagination support using direct API calls.

        Args:
            page: Page number (1-based)
            per_page: Number of tickets per page (max 100)
            sort_by: Field to sort by (created_at, updated_at, priority, status)
            sort_order: Sort order (asc or desc)
            view_id: Optional view ID to filter tickets by a specific view
            status: Optional status filter (new, open, pending, hold, solved, closed)

        Returns:
            Dict containing tickets and pagination info
        """
        try:
            # Cap at reasonable limit
            per_page = min(per_page, 100)

            # Use Search API if status filter is provided (and no view_id)
            if status and not view_id:
                return self._search_tickets_by_status(
                    status=status,
                    page=page,
                    per_page=per_page,
                    sort_by=sort_by,
                    sort_order=sort_order
                )

            # Build URL with parameters for offset pagination
            params = {
                'page': str(page),
                'per_page': str(per_page),
                'sort_by': sort_by,
                'sort_order': sort_order
            }
            query_string = urllib.parse.urlencode(params)

            if view_id:
                url = f"{self.base_url}/views/{view_id}/tickets.json?{query_string}"
            else:
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
            }
        except Exception as e:
            raise Exception(f"Failed to create ticket: {str(e)}")

    def get_views(self, page: int = 1, per_page: int = 25) -> Dict[str, Any]:
        """
        Get the list of views with pagination support using direct API calls.

        Args:
            page: Page number (1-based)
            per_page: Number of views per page (max 100)

        Returns:
            Dict containing views and pagination info
        """
        try:
            per_page = min(per_page, 100)

            params = {
                'page': str(page),
                'per_page': str(per_page)
            }
            query_string = urllib.parse.urlencode(params)
            url = f"{self.base_url}/views.json?{query_string}"

            req = urllib.request.Request(url)
            req.add_header('Authorization', self.auth_header)
            req.add_header('Content-Type', 'application/json')

            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())

            views_data = data.get('views', [])

            view_list = []
            for view in views_data:
                view_list.append({
                    'id': view.get('id'),
                    'title': view.get('title'),
                    'active': view.get('active'),
                    'position': view.get('position'),
                    'restriction': view.get('restriction'),
                    'created_at': view.get('created_at'),
                    'updated_at': view.get('updated_at'),
                })

            return {
                'views': view_list,
                'page': page,
                'per_page': per_page,
                'count': len(view_list),
                'has_more': data.get('next_page') is not None,
                'next_page': page + 1 if data.get('next_page') else None,
                'previous_page': page - 1 if data.get('previous_page') and page > 1 else None
            }
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else "No response body"
            raise Exception(f"Failed to get views: HTTP {e.code} - {e.reason}. {error_body}")
        except Exception as e:
            raise Exception(f"Failed to get views: {str(e)}")

    def get_users(self, role: str | None = None, page: int = 1, per_page: int = 25) -> Dict[str, Any]:
        """
        Get users with optional role filter and pagination.

        Args:
            role: Optional role filter (agent, admin, end-user)
            page: Page number (1-based)
            per_page: Number of users per page (max 100)

        Returns:
            Dict containing users and pagination info
        """
        try:
            per_page = min(per_page, 100)

            params = {
                'page': str(page),
                'per_page': str(per_page)
            }
            if role:
                params['role'] = role

            query_string = urllib.parse.urlencode(params)
            url = f"{self.base_url}/users.json?{query_string}"

            req = urllib.request.Request(url)
            req.add_header('Authorization', self.auth_header)
            req.add_header('Content-Type', 'application/json')

            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())

            users_data = data.get('users', [])

            user_list = []
            for user in users_data:
                user_list.append({
                    'id': user.get('id'),
                    'name': user.get('name'),
                    'email': user.get('email'),
                    'role': user.get('role'),
                    'active': user.get('active'),
                    'created_at': user.get('created_at'),
                    'updated_at': user.get('updated_at'),
                })

            return {
                'users': user_list,
                'page': page,
                'per_page': per_page,
                'count': len(user_list),
                'role_filter': role,
                'has_more': data.get('next_page') is not None,
                'next_page': page + 1 if data.get('next_page') else None,
                'previous_page': page - 1 if data.get('previous_page') and page > 1 else None
            }
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else "No response body"
            raise Exception(f"Failed to get users: HTTP {e.code} - {e.reason}. {error_body}")
        except Exception as e:
            raise Exception(f"Failed to get users: {str(e)}")

    def get_ticket_fields(self) -> Dict[str, Any]:
        """
        Get all ticket fields (system and custom) with their options.

        Returns:
            Dict containing ticket fields and their options
        """
        try:
            url = f"{self.base_url}/ticket_fields.json"

            req = urllib.request.Request(url)
            req.add_header('Authorization', self.auth_header)
            req.add_header('Content-Type', 'application/json')

            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())

            fields_data = data.get('ticket_fields', [])

            field_list = []
            for field in fields_data:
                field_list.append({
                    'id': field.get('id'),
                    'type': field.get('type'),
                    'title': field.get('title'),
                    'description': field.get('description'),
                    'active': field.get('active'),
                    'required': field.get('required'),
                    'custom_field_options': field.get('custom_field_options'),
                    'system_field_options': field.get('system_field_options'),
                })

            return {
                'ticket_fields': field_list,
                'count': len(field_list)
            }
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else "No response body"
            raise Exception(f"Failed to get ticket fields: HTTP {e.code} - {e.reason}. {error_body}")
        except Exception as e:
            raise Exception(f"Failed to get ticket fields: {str(e)}")

    def get_user_fields(self) -> Dict[str, Any]:
        """
        Get all custom user fields with their options.

        Returns:
            Dict containing user fields and their options
        """
        try:
            url = f"{self.base_url}/user_fields.json"

            req = urllib.request.Request(url)
            req.add_header('Authorization', self.auth_header)
            req.add_header('Content-Type', 'application/json')

            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())

            fields_data = data.get('user_fields', [])

            field_list = []
            for field in fields_data:
                field_list.append({
                    'id': field.get('id'),
                    'key': field.get('key'),
                    'type': field.get('type'),
                    'title': field.get('title'),
                    'description': field.get('description'),
                    'active': field.get('active'),
                    'custom_field_options': field.get('custom_field_options'),
                })

            return {
                'user_fields': field_list,
                'count': len(field_list)
            }
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else "No response body"
            raise Exception(f"Failed to get user fields: HTTP {e.code} - {e.reason}. {error_body}")
        except Exception as e:
            raise Exception(f"Failed to get user fields: {str(e)}")

    def get_organization_fields(self) -> Dict[str, Any]:
        """
        Get all custom organization fields with their options.

        Returns:
            Dict containing organization fields and their options
        """
        try:
            url = f"{self.base_url}/organization_fields.json"

            req = urllib.request.Request(url)
            req.add_header('Authorization', self.auth_header)
            req.add_header('Content-Type', 'application/json')

            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())

            fields_data = data.get('organization_fields', [])

            field_list = []
            for field in fields_data:
                field_list.append({
                    'id': field.get('id'),
                    'key': field.get('key'),
                    'type': field.get('type'),
                    'title': field.get('title'),
                    'description': field.get('description'),
                    'active': field.get('active'),
                    'custom_field_options': field.get('custom_field_options'),
                })

            return {
                'organization_fields': field_list,
                'count': len(field_list)
            }
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else "No response body"
            raise Exception(f"Failed to get organization fields: HTTP {e.code} - {e.reason}. {error_body}")
        except Exception as e:
            raise Exception(f"Failed to get organization fields: {str(e)}")

    def update_ticket(self, ticket_id: int, **fields: Any) -> Dict[str, Any]:
        """
        Update a Zendesk ticket with provided fields using Zenpy.

        Supported fields include common ticket attributes like:
        subject, status, priority, type, assignee_id, requester_id,
        tags (list[str]), custom_fields (list[dict]), due_at, etc.
        """
        try:
            # Load the ticket, mutate fields directly, and update
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
            }
        except Exception as e:
            raise Exception(f"Failed to update ticket {ticket_id}: {str(e)}")