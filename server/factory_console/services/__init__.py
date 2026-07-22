"""Application service layer — request logic between the HTTP edge and the adapter.

Services hold the per-endpoint domain logic (filtering, joining, not-found
mapping) so the HTTP handlers stay thin. This package is intentionally a bare
marker with no re-exports: sibling tracks add their own modules here, and
consumers import each service by its full path (e.g.
``from factory_console.services.ticket_service import TicketService``).
"""
