-- Keep third-party extension objects outside the exposed public API schema.
-- Existing citext columns continue to work after the extension schema changes.

create schema if not exists extensions;
alter extension citext set schema extensions;
