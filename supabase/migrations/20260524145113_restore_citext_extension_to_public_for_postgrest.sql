-- Keep citext in public for the current PostgREST/Data API mapping. Moving it
-- after table creation can make generated PostgREST writes fail with
-- type "public.citext" or type "extensions.citext" does not exist until the
-- schema cache and column type metadata agree.

alter extension citext set schema public;
notify pgrst, 'reload schema';
