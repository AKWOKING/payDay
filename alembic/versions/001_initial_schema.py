"""001_initial_schema

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-09-01 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. users
    op.create_table(
        'users',
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('full_name', sa.String(length=120), nullable=False),
        sa.Column('phone_number', sa.String(length=20), nullable=False),
        sa.Column('email', sa.String(length=120), nullable=True),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('pin_hash', sa.String(length=255), nullable=True),
        sa.Column('id_document_no_encrypted', sa.LargeBinary(), nullable=True),
        sa.Column('id_document_type', sa.String(length=30), nullable=False, server_default='NATIONAL_ID'),
        sa.Column('kyc_status', sa.Enum('PENDING', 'VERIFIED', 'REJECTED', name='kycstatus'), nullable=False),
        sa.Column('role', sa.Enum('CUSTOMER', 'ADMIN', 'AUDITOR', name='userrole'), nullable=False),
        sa.Column('status', sa.Enum('ACTIVE', 'SUSPENDED', 'CLOSED', name='userstatus'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('user_id')
    )
    op.create_index('ix_users_phone_number', 'users', ['phone_number'], unique=True)
    op.create_index('ix_users_email', 'users', ['email'], unique=True)

    # 2. wallets
    op.create_table(
        'wallets',
        sa.Column('wallet_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('balance', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('locked_balance', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('status', sa.Enum('ACTIVE', 'FROZEN', 'CLOSED', name='walletstatus'), nullable=False),
        sa.Column('daily_limit', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('monthly_limit', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('wallet_id'),
        sa.CheckConstraint('balance >= 0.00', name='chk_wallet_positive_balance'),
        sa.CheckConstraint('locked_balance >= 0.00', name='chk_wallet_positive_locked_balance')
    )
    op.create_index('ix_wallets_user_id', 'wallets', ['user_id'], unique=True)

    # 3. linked_external_accounts
    op.create_table(
        'linked_external_accounts',
        sa.Column('linked_account_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('provider', sa.Enum('MTN', 'ORANGE', 'UBA', name='channelprovider'), nullable=False),
        sa.Column('account_identifier', sa.String(length=50), nullable=False),
        sa.Column('is_verified', sa.Boolean(), nullable=False),
        sa.Column('is_default', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('linked_account_id'),
        sa.UniqueConstraint('user_id', 'provider', 'account_identifier', name='uq_user_provider_account')
    )

    # 4. transactions
    op.create_table(
        'transactions',
        sa.Column('transaction_id', sa.String(length=36), nullable=False),
        sa.Column('idempotency_key', sa.String(length=100), nullable=False),
        sa.Column('wallet_id', sa.String(length=36), nullable=False),
        sa.Column('linked_account_id', sa.String(length=36), nullable=False),
        sa.Column('type', sa.Enum('DEPOSIT', 'WITHDRAW', name='transactiontype'), nullable=False),
        sa.Column('channel', sa.Enum('MTN', 'ORANGE', 'UBA', name='transactionchannel'), nullable=False),
        sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('fee', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('net_amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'PROCESSING', 'SUCCESS', 'FAILED', 'REVERSED', name='transactionstatus'), nullable=False),
        sa.Column('external_ref', sa.String(length=100), nullable=True),
        sa.Column('failure_reason', sa.String(length=255), nullable=True),
        sa.Column('extra_data', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['wallet_id'], ['wallets.wallet_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['linked_account_id'], ['linked_external_accounts.linked_account_id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('transaction_id'),
        sa.CheckConstraint('amount > 0.00', name='chk_tx_positive_amount'),
        sa.CheckConstraint('fee >= 0.00', name='chk_tx_positive_fee')
    )
    op.create_index('ix_transactions_idempotency_key', 'transactions', ['idempotency_key'], unique=True)

    # 5. notifications
    op.create_table(
        'notifications',
        sa.Column('notification_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('transaction_id', sa.String(length=36), nullable=True),
        sa.Column('channel', sa.Enum('SMS', 'PUSH', name='notificationchannel'), nullable=False),
        sa.Column('recipient', sa.String(length=100), nullable=False),
        sa.Column('message', sa.String(length=500), nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'SENT', 'FAILED', name='notificationstatus'), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.transaction_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('notification_id')
    )

    # 6. audit_logs
    op.create_table(
        'audit_logs',
        sa.Column('log_id', sa.String(length=36), nullable=False),
        sa.Column('actor_id', sa.String(length=36), nullable=True),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('entity_name', sa.String(length=50), nullable=False),
        sa.Column('entity_id', sa.String(length=36), nullable=False),
        sa.Column('old_state', sa.JSON(), nullable=True),
        sa.Column('new_state', sa.JSON(), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['actor_id'], ['users.user_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('log_id')
    )


def downgrade() -> None:
    op.drop_table('audit_logs')
    op.drop_table('notifications')
    op.drop_table('transactions')
    op.drop_table('linked_external_accounts')
    op.drop_table('wallets')
    op.drop_table('users')
