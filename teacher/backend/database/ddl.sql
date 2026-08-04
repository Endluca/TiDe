\set ON_ERROR_STOP on

-- 本地空环境 DDL 入口。共享 public 表 fixture 不得用于生产。
\ir fixtures/0001_shared_contract.sql
\ir migrations/0001_initial.up.sql
\ir migrations/0002_shared_database_exchange.up.sql
\ir migrations/0003_file_upload_intents.up.sql
\ir migrations/0004_task_command_receipts.up.sql
\ir migrations/0005_faq_message_commands.up.sql
\ir migrations/0006_teacher_profile_g01_support.up.sql
\ir migrations/0007_shared_task_assignment_links.up.sql
\ir migrations/0008_remove_legacy_task_exchange.up.sql
\ir migrations/0009_task_view_command.up.sql
\ir migrations/0010_current_task_execution.up.sql
\ir migrations/0011_system_notification_delivery.up.sql
\ir migrations/0012_system_notification_publication_guards.up.sql
\ir migrations/0013_system_notification_owner_maintenance.up.sql
\ir migrations/0014_teacher_photo_processing.up.sql
\ir migrations/0015_teacher_photo_filter_strength.up.sql
\ir migrations/0016_database_quiz_banks.up.sql
\ir migrations/0017_task_assignment_teacher_response.up.sql
\ir migrations/0018_remove_task_assignment_teacher_response.up.sql
\ir migrations/0019_growth_stage_notification_state.up.sql
\ir migrations/0020_product_analytics.up.sql
\ir migrations/0021_teacher_support_tickets.up.sql
\ir migrations/0022_performance_job_leases.up.sql
\ir migrations/0023_teacher_support_operator_atomicity.up.sql
\ir migrations/0024_support_ticket_cas_and_function_owner.up.sql
\ir migrations/0025_fixed_task_semantic_alignment.up.sql
\ir migrations/0026_kuozhi_course_syncs.up.sql
