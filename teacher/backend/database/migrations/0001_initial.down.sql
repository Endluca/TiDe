BEGIN;

-- 仅用于空环境回滚演练。执行后会删除本系统 tide Schema 内的全部数据。
DROP SCHEMA IF EXISTS tide CASCADE;

COMMIT;
