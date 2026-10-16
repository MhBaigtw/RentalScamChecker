"""check_contacts.source; contacts extracted from listing_text; one row per distinct contact.

Schema:
- check_contacts.source: 'field' (contact form fields) or 'text' (found in listing_text).
- Unique (check_id, kind) becomes (check_id, kind, hmac_hash): a check can now have several
  phones/emails (from the text), but each distinct one only once.

Data (ED-016): rows created before this revision stored listing_text unredacted. The upgrade
runs the same extraction the API now uses on every existing check: contacts found in the text are
hashed into check_contacts (source 'text', skipped if already present from the fields) and the
text is replaced by its redacted form. This needs CONTACT_HASH_PEPPER, like the API.

Downgrade removes the 'text' rows and the column, but cannot restore the redacted text, so a
later re-upgrade finds nothing to extract: the 'text' rows are lost for good.

Revision ID: 0003_contact_source
Revises: 0002_domain_model
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from rentalscamchecker.config import get_settings
from rentalscamchecker.contacts import ContactKind, contact_hash, extract_contacts

revision: str = "0003_contact_source"
down_revision: str | None = "0002_domain_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows all came from the form fields.
    op.add_column(
        "check_contacts",
        sa.Column("source", sa.Text(), server_default="field", nullable=False),
    )
    # No default from now on: the application must say where each contact came from.
    op.alter_column("check_contacts", "source", server_default=None)
    op.create_check_constraint(
        op.f("ck_check_contacts_source"), "check_contacts", "source IN ('field', 'text')"
    )
    op.drop_constraint("uq_check_contacts_check_id_kind", "check_contacts", type_="unique")
    op.create_unique_constraint(
        "uq_check_contacts_check_id_kind_hmac_hash",
        "check_contacts",
        ["check_id", "kind", "hmac_hash"],
    )
    _redact_existing_listing_text()


def _redact_existing_listing_text() -> None:
    settings = get_settings()
    pepper = settings.contact_hash_pepper.get_secret_value().encode()
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, listing_text FROM checks")).all()
    for check_id, text in rows:
        extracted = extract_contacts(text, settings.contact_default_region)
        found: list[tuple[ContactKind, str]] = [("phone", v) for v in extracted.phones]
        found += [("email", v) for v in extracted.emails]
        for kind, value in found:
            conn.execute(
                sa.text(
                    "INSERT INTO check_contacts (id, check_id, kind, source, hmac_hash) "
                    "VALUES (gen_random_uuid(), :check_id, :kind, 'text', :hash) "
                    "ON CONFLICT (check_id, kind, hmac_hash) DO NOTHING"
                ),
                {"check_id": check_id, "kind": kind, "hash": contact_hash(kind, value, pepper)},
            )
        if extracted.redacted != text:
            conn.execute(
                sa.text("UPDATE checks SET listing_text = :text WHERE id = :id"),
                {"text": extracted.redacted, "id": check_id},
            )


def downgrade() -> None:
    op.execute("DELETE FROM check_contacts WHERE source = 'text'")
    op.drop_constraint(
        "uq_check_contacts_check_id_kind_hmac_hash", "check_contacts", type_="unique"
    )
    op.create_unique_constraint(
        "uq_check_contacts_check_id_kind", "check_contacts", ["check_id", "kind"]
    )
    op.drop_constraint(op.f("ck_check_contacts_source"), "check_contacts", type_="check")
    op.drop_column("check_contacts", "source")
