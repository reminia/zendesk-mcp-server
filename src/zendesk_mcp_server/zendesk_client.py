from typing import Dict, Any, List
import logging
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed

from zenpy import Zenpy
from zenpy.lib.api_objects import Comment

logger = logging.getLogger(__name__)


class ZendeskClient:
    def __init__(self, subdomain: str, email: str, token: str, timeout: int = 30):
        """
        Initialize the Zendesk client using zenpy lib.

        Args:
            subdomain: Zendesk subdomain
            email: Zendesk account email
            token: Zendesk API token
            timeout: Request timeout in seconds
        """

        self.client = Zenpy(
            subdomain=subdomain,
            email=email,
            token=token,
            timeout=timeout,
        )

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

    def get_multiple_tickets(self, ticket_ids: List[int], include_comments: bool = False) -> List[Dict[str, Any]]:
        """
        Query multiple tickets by their IDs (up to 100 tickets).
        Ref: https://developer.zendesk.com/api-reference/ticketing/tickets/tickets/#show-multiple-tickets

        Args:
            ticket_ids: List of ticket IDs to retrieve
            include_comments: Whether to include comments for each ticket (default: False)

        Returns:
            List of ticket dictionaries with full ticket data
        """
        try:
            if not ticket_ids:
                raise ValueError("ticket_ids list cannot be empty")

            if len(ticket_ids) > 100:
                raise ValueError("Cannot retrieve more than 100 tickets at once")

            # Convert list to comma-separated string
            ids_param = ','.join(str(tid) for tid in ticket_ids)

            # Construct the URL using the show_many endpoint
            url = f"https://{self.client.tickets.base_url}/api/v2/tickets/show_many.json?ids={ids_param}"

            # Use the session to make the request
            response = self.client.tickets.session.get(url, timeout=self.client.tickets.timeout)
            response.raise_for_status()
            data = response.json()

            # Build ticket dictionaries
            tickets = []
            for ticket in data.get('tickets', []):
                # Use same simplified fields as get_ticket() for consistency
                ticket_dict = {
                    'id': ticket.get('id'),
                    'subject': ticket.get('subject'),
                    'description': ticket.get('description'),
                    'status': ticket.get('status'),
                    'priority': ticket.get('priority'),
                    'created_at': ticket.get('created_at'),
                    'updated_at': ticket.get('updated_at'),
                    'requester_id': ticket.get('requester_id'),
                    'assignee_id': ticket.get('assignee_id'),
                    'organization_id': ticket.get('organization_id')
                }
                tickets.append(ticket_dict)

            # Optionally fetch comments for each ticket in parallel
            if include_comments:
                def fetch_comments_for_ticket(ticket_dict):
                    """Helper function to fetch comments for a single ticket"""
                    ticket_id = ticket_dict['id']
                    try:
                        comments = self.get_ticket_comments(ticket_id, include_inline_images=False)
                        ticket_dict['comments'] = comments
                    except Exception as e:
                        logger.warning(f"Failed to fetch comments for ticket {ticket_id}: {str(e)}")
                        ticket_dict['comments'] = []
                    return ticket_dict

                # Use ThreadPoolExecutor with max 10 workers for parallel fetching
                with ThreadPoolExecutor(max_workers=10) as executor:
                    # Submit all tasks
                    future_to_ticket = {executor.submit(fetch_comments_for_ticket, ticket): ticket for ticket in tickets}

                    # Wait for all to complete and update tickets list
                    tickets = []
                    for future in as_completed(future_to_ticket):
                        try:
                            ticket_with_comments = future.result()
                            tickets.append(ticket_with_comments)
                        except Exception as e:
                            # This shouldn't happen since we catch errors in fetch_comments_for_ticket
                            original_ticket = future_to_ticket[future]
                            logger.error(f"Unexpected error fetching comments for ticket {original_ticket['id']}: {str(e)}")
                            original_ticket['comments'] = []
                            tickets.append(original_ticket)

            logger.info(f"Retrieved {len(tickets)} tickets from IDs: {ticket_ids} (include_comments={include_comments})")
            return tickets
        except ValueError as ve:
            raise ve
        except Exception as e:
            logger.error(f"Failed to get multiple tickets {ticket_ids}: {str(e)}")
            raise Exception(f"Failed to get multiple tickets: {str(e)}")

    def get_ticket_comments(self, ticket_id: int, include_inline_images: bool = False) -> List[Dict[str, Any]]:
        """
        Get all comments for a specific ticket.

        Args:
            ticket_id: ID of the ticket
            include_inline_images: Whether to include inline image attachments (default: False)
        """
        try:
            # Fetch comments with optional inline images
            if include_inline_images:
                comments = self.client.tickets.comments(
                    ticket=ticket_id,
                    include_inline_images=True
                )
            else:
                comments = self.client.tickets.comments(ticket=ticket_id)

            return [{
                'id': comment.id,
                'author_id': comment.author_id,
                'body': comment.body,
                'html_body': comment.html_body,
                'public': comment.public,
                'created_at': str(comment.created_at),
                'attachments': [
                    {
                        'id': att.id,
                        'file_name': att.file_name,
                        'content_type': att.content_type,
                        'content_url': att.content_url,
                        'size': att.size,
                        'is_image': att.content_type.startswith('image/') if att.content_type else False
                    }
                    for att in (comment.attachments or [])
                ] if comment.attachments else []
            } for comment in comments]
        except Exception as e:
            raise Exception(f"Failed to get comments for ticket {ticket_id}: {str(e)}")

    def post_comment(self, ticket_id: int, comment: str, public: bool = True) -> str:
        """
        Post a comment to an existing ticket.
        """
        try:
            ticket = self.client.tickets(id=ticket_id)
            ticket.comment = Comment(
                html_body=comment,
                public=public
            )
            self.client.tickets.update(ticket)
            return comment
        except Exception as e:
            raise Exception(f"Failed to post comment on ticket {ticket_id}: {str(e)}")

    def search_articles(self, query: str, limit: int = 10, locale: str = 'en-us') -> List[Dict[str, Any]]:
        """
        Search help center articles by query.

        Args:
            query: Search query string
            limit: Maximum number of articles to return (default: 10)
            locale: Language locale for articles (default: 'en-us')
                    Examples: 'en-us', 'zh-cn', 'zh-tw', 'ja', 'ko', 'de', 'es', 'fr', 'it', 'ru', 'tr'
        """
        try:
            results = self.client.help_center.articles.search(query=query, locale=locale)
            articles = []
            for i, article in enumerate(results):
                if i >= limit:
                    break
                articles.append({
                    'id': article.id,
                    'title': article.title,
                    'body': article.body[:1000] if len(article.body) > 1000 else article.body,
                    'section_id': article.section_id,
                    # 'locale': article.locale,
                    'updated_at': str(article.updated_at),
                    'url': article.html_url
                })
            logger.info(f"Found {len(articles)} articles for query: {query} (locale: {locale})")
            return articles
        except Exception as e:
            logger.error(f"Failed to search articles: {str(e)}")
            raise Exception(f"Failed to search articles: {str(e)}")

    def get_article(self, article_id: int, locale: str = 'en-us') -> Dict[str, Any]:
        """
        Get a specific help center article by ID.

        Args:
            article_id: The ID of the article to retrieve
            locale: Language locale for the article (default: 'en-us')
                    Examples: 'en-us', 'zh-cn', 'zh-tw', 'ja', 'ko', 'de', 'es', 'fr', 'it', 'ru', 'tr'
        """
        try:
            article = self.client.help_center.articles(id=article_id, locale=locale)


            return {
                'id': article.id,
                'title': article.title,
                'body': article.body,
                'section_id': article.section_id,
                'author_id': article.author_id,
                # 'locale': article.locale,
                'updated_at': str(article.updated_at),
                'url': article.html_url,
                'vote_sum': article.vote_sum,
                'vote_count': article.vote_count
            }
        except Exception as e:
            logger.error(f"Failed to get article {article_id}: {str(e)}")
            raise Exception(f"Failed to get article {article_id}: {str(e)}")

    def list_sections(self) -> List[Dict[str, Any]]:
        """
        List all help center sections (lightweight, no articles).
        """
        try:
            sections = self.client.help_center.sections()
            return [{
                'id': section.id,
                'name': section.name,
                'description': section.description,
                'category_id': section.category_id,
                'position': section.position,
                'updated_at': str(section.updated_at)
            } for section in sections]
        except Exception as e:
            logger.error(f"Failed to list sections: {str(e)}")
            raise Exception(f"Failed to list sections: {str(e)}")

    def get_section_articles(self, section_id: int, limit: int = 20, locale: str = 'en-us') -> List[Dict[str, Any]]:
        """
        Get articles for a specific section.

        Args:
            section_id: The ID of the section
            limit: Maximum number of articles to return (default: 20)
            locale: Language locale for articles (default: 'en-us')
                    Examples: 'en-us', 'zh-cn', 'zh-tw', 'ja', 'ko', 'de', 'es', 'fr', 'it', 'ru', 'tr'
        """
        try:
            articles = self.client.help_center.sections.articles(section_id=section_id, locale=locale)
            result = []
            for i, article in enumerate(articles):
                if i >= limit:
                    break
                result.append({
                    'id': article.id,
                    'title': article.title,
                    'body': article.body[:1000] if len(article.body) > 1000 else article.body,
                    'updated_at': str(article.updated_at),
                    'url': article.html_url
                })
            logger.info(f"Found {len(result)} articles in section {section_id} (locale: {locale})")
            return result
        except Exception as e:
            logger.error(f"Failed to get section articles: {str(e)}")
            raise Exception(f"Failed to get section articles: {str(e)}")

    def get_attachment(self, attachment_id: int | str) -> Dict[str, Any]:
        """
        Download and return an attachment by ID.

        Args:
            attachment_id: The ID of the attachment to download

        Returns:
            Dictionary with attachment metadata and base64-encoded data
        """
        try:
            # Convert to int if string
            attachment_id = int(attachment_id) if isinstance(attachment_id, str) else attachment_id

            # Get attachment metadata first
            attachment = self.client.attachments(id=attachment_id)

            # Download attachment content to BytesIO
            content_stream = self.client.attachments.download(attachment_id)

            # Encode as base64
            base64_data = base64.b64encode(content_stream.getvalue()).decode('utf-8')

            logger.info(f"Downloaded attachment {attachment_id}: {attachment.file_name} ({attachment.size} bytes)")
            return {
                'id': attachment.id,
                'file_name': attachment.file_name,
                'content_type': attachment.content_type,
                'size': attachment.size,
                'data': base64_data
            }
        except Exception as e:
            logger.error(f"Failed to download attachment {attachment_id}: {str(e)}")
            raise Exception(f"Failed to download attachment {attachment_id}: {str(e)}")

    def search_macros(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search macros by query string.

        Args:
            query: Search query to match macro titles
            limit: Maximum number of macros to return

        Returns:
            List of macro dictionaries with metadata
        """
        try:
            # Construct the full search URL manually
            import urllib.parse
            encoded_query = urllib.parse.quote(query)
            url = f"https://{self.client.macros.base_url}/api/v2/macros/search.json?query={encoded_query}"

            # Use the session to make the request
            response = self.client.macros.session.get(url, timeout=self.client.macros.timeout)
            response.raise_for_status()
            data = response.json()

            macros = []
            for i, macro in enumerate(data.get('macros', [])):
                if i >= limit:
                    break

                # Truncate actions if too large (similar to article body pattern)
                actions = macro.get('actions', [])
                if len(actions) > 10:
                    actions = actions[:10]

                macros.append({
                    'id': macro.get('id'),
                    'title': macro.get('title'),
                    'description': macro.get('description'),
                    'actions': actions,
                    'active': macro.get('active'),
                    'restriction': macro.get('restriction'),
                    'created_at': str(macro.get('created_at', '')),
                    'updated_at': str(macro.get('updated_at', '')),
                    'url': macro.get('url')
                })

            logger.info(f"Found {len(macros)} macros for query: {query}")
            return macros
        except Exception as e:
            logger.error(f"Failed to search macros: {str(e)}")
            raise Exception(f"Failed to search macros: {str(e)}")

    def get_macro(self, macro_id: int) -> Dict[str, Any]:
        """
        Get a specific macro by ID.

        Args:
            macro_id: The ID of the macro to retrieve

        Returns:
            Dictionary with complete macro data
        """
        try:
            # Construct the URL directly to avoid pagination issues
            url = f"https://{self.client.macros.base_url}/api/v2/macros/{macro_id}.json"

            # Use the session to make the request
            response = self.client.macros.session.get(url, timeout=self.client.macros.timeout)
            response.raise_for_status()
            data = response.json()

            macro = data.get('macro', {})
            return {
                'id': macro.get('id'),
                'title': macro.get('title'),
                'description': macro.get('description'),
                'actions': macro.get('actions', []),
                'active': macro.get('active'),
                'position': macro.get('position'),
                'restriction': macro.get('restriction'),
                'created_at': str(macro.get('created_at', '')),
                'updated_at': str(macro.get('updated_at', '')),
                'url': macro.get('url')
            }
        except Exception as e:
            logger.error(f"Failed to get macro {macro_id}: {str(e)}")
            raise Exception(f"Failed to get macro {macro_id}: {str(e)}")

    def apply_macro_to_ticket(self, ticket_id: int, macro_id: int) -> Dict[str, Any]:
        """
        Apply a macro to a ticket.

        This performs a two-step process:
        1. Preview the macro changes using show_macro_effect
        2. Apply the changes by updating the ticket

        Args:
            ticket_id: The ID of the ticket to apply the macro to
            macro_id: The ID of the macro to apply

        Returns:
            Dictionary with operation status and updated ticket info
        """
        try:
            logger.info(f"Applying macro {macro_id} to ticket {ticket_id}")

            # Step 1: Preview the macro effect
            macro_result = self.client.tickets.show_macro_effect(ticket_id, macro_id)
            logger.info(f"Successfully previewed macro {macro_id} effect on ticket {ticket_id}")

            # Step 2: Apply the changes by updating the ticket
            # update() returns a TicketAudit object, which contains the updated ticket
            ticket_audit = self.client.tickets.update(macro_result.ticket)
            logger.info(f"Successfully applied macro {macro_id} to ticket {ticket_id}")

            # Extract the ticket from the audit
            updated_ticket = ticket_audit.ticket

            return {
                'success': True,
                'ticket_id': ticket_id,
                'macro_id': macro_id,
                'message': f'Macro {macro_id} successfully applied to ticket {ticket_id}',
                'updated_ticket': {
                    'id': updated_ticket.id,
                    'subject': updated_ticket.subject,
                    'status': updated_ticket.status,
                    'priority': updated_ticket.priority,
                    'updated_at': str(updated_ticket.updated_at)
                }
            }
        except Exception as e:
            logger.error(f"Failed to apply macro {macro_id} to ticket {ticket_id}: {str(e)}")
            raise Exception(f"Failed to apply macro {macro_id} to ticket {ticket_id}: {str(e)}")
