"""Add provider connections, independent mock records and normalized projections."""
from alembic import op
import sqlalchemy as sa

revision = "f42a8b610c34"
down_revision = "a81c30d6008d"
branch_labels = None
depends_on = None


def upgrade():
    common = lambda: [sa.Column("id", sa.String(64), primary_key=True), sa.Column("tenant_id", sa.String(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("location_id", sa.String(64), sa.ForeignKey("locations.id"), nullable=False), sa.Column("provider", sa.String(30), nullable=False)]
    op.create_table("integration_connections", *common(), sa.Column("mode", sa.String(30), nullable=False), sa.Column("status", sa.String(30), nullable=False), sa.Column("capabilities", sa.JSON(), nullable=False), sa.Column("config", sa.JSON(), nullable=False), sa.Column("last_sync_at", sa.DateTime(timezone=True)), sa.Column("last_attempt_at", sa.DateTime(timezone=True)), sa.Column("error_code", sa.String(80)), sa.UniqueConstraint("location_id", "provider"))
    for table in ["mock_provider_records", "operational_projections"]:
        extra = [sa.Column("received_at", sa.DateTime(timezone=True), nullable=False)] if table == "operational_projections" else []
        op.create_table(table, *common(), sa.Column("kind", sa.String(30), nullable=False), sa.Column("external_id", sa.String(100), nullable=False), sa.Column("data", sa.JSON(), nullable=False), sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False), *extra, sa.UniqueConstraint("location_id", "provider", "kind", "external_id"))
    for table in ["integration_connections", "mock_provider_records", "operational_projections"]:
        for column in ["location_id", "tenant_id"]:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    for table in ["operational_projections", "mock_provider_records", "integration_connections"]:
        op.drop_table(table)
