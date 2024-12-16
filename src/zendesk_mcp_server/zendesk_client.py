from typing import Dict, Any, List
from zenpy import Zenpy
from zenpy.lib.api_objects import Ticket, Comment


class ZendeskClient:
    def __init__(self, subdomain: str, email: str, token: str):
        """
        Initialize the Zendesk client using zenpy lib.
        """
        self.client = Zenpy(
            subdomain=subdomain,
            email=email,
            token=token
        )

    def get_ticket(self, ticket_id: int) -> Ticket:
        """
        Get a ticket by its ID.
        """
        try:
            return self.client.tickets(id=ticket_id)
        except Exception as e:
            raise Exception(f"Failed to get ticket {ticket_id}: {str(e)}")

    def get_ticket_comments(self, ticket_id: int) -> List[Dict[str, Any]]:
        """
        Get all comments for a specific ticket.
        """
        try:
            comments = self.client.tickets.comments(ticket=ticket_id)
            return [{
                'id': comment.id,
                'author_id': comment.author_id,
                'body': comment.body,
                'html_body': comment.html_body,
                'public': comment.public,
                'created_at': str(comment.created_at)
            } for comment in comments]
        except Exception as e:
            raise Exception(f"Failed to get comments for ticket {
                            ticket_id}: {str(e)}")

    def create_ticket_comment(self, ticket_id: int, comment: str, public: bool = True) -> str:
        """
        Create a comment for an existing ticket.
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
            raise Exception(f"Failed to create comment on ticket {ticket_id}: {str(e)}")