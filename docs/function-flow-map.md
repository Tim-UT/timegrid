# TimeGrid Function Flow Map

This map tracks user-visible functions, the code/API path that powers them, whether the flow is reversible, and the current gap status. The product rule used here: personal workspace operations should preserve user-created data by default; publishing/subscription distribution can be one-way when other users may depend on the link.

## Auth And Account

| Flow | User entry | Backend/API | Reversible? | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| Mastodon sign in/up | Auth page Mastodon button | `/auth/mastodon/start`, `/auth/mastodon/callback` | Yes, session sign out only | Implemented | Production currently exposes only Mastodon auth. |
| Email sign up/sign in | Feature-flagged auth form | `/api/auth/email/signup`, `/api/auth/email/login` | Yes | Implemented but hidden | Hidden until SMTP cost/setup is acceptable. |
| Google/Apple sign in | Feature-flagged auth buttons | `/auth/supabase/oauth/<provider>` | Yes | Implemented but hidden | Hidden in production. |
| Sign out | Top bar button | `/api/logout` | Yes | Implemented | Clears session and returns to auth flow. |
| Account deletion | No visible entry | None | One-way | Missing, skipped | Needs product decision about Mastodon-linked identity, owned published pages, and subscriber impact. |

## Calendar Tabs

| Flow | User entry | Backend/API | Reversible? | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| Create calendar tab | `+` in vertical tab rail | `POST /api/personal/<acct>/calendars` | Yes | Implemented | Duplicate titles are renamed, e.g. `Work 2`, and the new tab loads immediately. |
| Switch calendar tab | Vertical tab rail | `GET /api/personal/<acct>?calendar_id=...`, `GET /api/creator/<acct>?calendar_id=...` | Yes | Implemented | Calendar tab filters subscriptions, owned timelines, preview calendar, and export defaults. |
| Reorder calendar tabs | Drag tab in vertical rail | `PATCH /api/personal/<acct>/calendars/<id>` | Yes | Implemented | Frontend uses optimistic reorder with insertion line; backend persists `position`. |
| Delete calendar tab | `Delete tab` on active non-default tab | `DELETE /api/personal/<acct>/calendars/<id>?target_calendar_id=...` | Mostly yes | Implemented this pass | Archives/hides the tab and moves contained subscriptions/timelines to a selected sibling tab. Default tabs are locked. |
| Restore archived calendar tab | No visible entry | None | Yes | Missing | Because delete now archives instead of hard-deleting, a future calendar archive manager should expose restore. |
| Permanent calendar delete | No visible entry | None | One-way | Skipped | Not added because data loss is risky and restore UI does not exist yet. |

## Timelines And Subscriptions

| Flow | User entry | Backend/API | Reversible? | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| Create editable timeline | `New timeline` button | `POST /api/personal/<acct>/timelines` | Yes | Implemented | Creates timeline plus owned subscription shell in selected calendar. |
| Edit timeline events | Timeline editor | `PATCH /api/personal/<acct>/timelines/<id>` | Mostly yes | Implemented | Manual undo/history is not implemented. Dynamic exports update after save. |
| Import URL/file source | Import menu | `POST /api/personal/<acct>/subscriptions` and import handlers | Yes | Implemented | Adds a subscription into selected calendar. |
| Toggle visibility | Timeline card `Show/Hide` | `PATCH /api/personal/<acct>/subscriptions/<id>` | Yes | Implemented | Affects current render/export inclusion. |
| Reorder timeline cards | Drag card in subscription list | `PATCH /api/personal/<acct>/subscriptions/<id>` | Yes | Implemented | Optimistic frontend move, backend stores `position`; failures roll back and show notification-area banner. |
| Move timeline to another calendar | Drag card onto another tab | `PATCH /api/personal/<acct>/subscriptions/<id>` | Yes | Implemented | Moves owned timeline metadata with the subscription. |
| Move personal/creator/archive workspace | Card action menu | `PATCH /api/personal/<acct>/subscriptions/<id>` | Mostly yes | Implemented | Archive is restricted for published timelines. |
| Trash timeline/subscription | Card `Trash` | `POST /api/personal/<acct>/subscriptions/<id>/trash` | Yes | Implemented | Moves to trash and hides from active workspace. |
| Restore from trash | Trash card `Restore` | `POST /api/personal/<acct>/subscriptions/<id>/restore` | Yes | Implemented | Restores active subscription state. |
| Empty trash | Trash section | `DELETE /api/personal/<acct>/subscriptions/<id>?mode=permanent` | One-way | Implemented | Permanent deletion is intentionally explicit. |
| Detach/delete subscription | Archive/card action | `DELETE /api/personal/<acct>/subscriptions/<id>?mode=detach` | One-way-ish | Implemented | Removes owner management view but keeps referenced public artifacts when needed. |

## Merge And Bundle

| Flow | User entry | Backend/API | Reversible? | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| Merge multiple subscriptions | Merge tool modal | `POST /api/personal/<acct>/merge` | Yes | Implemented | Children are grouped under a bundle. |
| Separate bundle children | Bundle action menu | `POST /api/personal/<acct>/subscriptions/<id>/separate` | Yes | Implemented | Can restore selected child subscriptions and optionally trash original bundle. |
| Edit bundle metadata | Card/editor controls | `PATCH /api/personal/<acct>/subscriptions/<id>` | Yes | Implemented | Some advanced source metadata is still API-first. |

## Publish, Community, And Archive

| Flow | User entry | Backend/API | Reversible? | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| Publish creator calendar/bundle | Creator publish modal | `POST /api/personal/<acct>/published` | Partly | Implemented | Public/invited/private settings are editable. |
| Edit published settings | Published management modal | `PATCH /api/personal/<acct>/published/<slug>` | Yes | Implemented | Visibility, invite list, hashtags, and listing state can change. |
| Archive published bundle | Manage action | `DELETE /api/personal/<acct>/published/<slug>?mode=archive` | Partly | Implemented | Keeps existing subscribers working, removes active public listing, and appears in archive workspace. |
| Remove listing | Manage action | `DELETE /api/personal/<acct>/published/<slug>?mode=remove` | Yes | Implemented | Keeps owner record but removes listing. |
| Permanent detach published owner | Manage action/API | `DELETE /api/personal/<acct>/published/<slug>` | One-way | Implemented | Intentionally not reversible because public subscribers may already hold links. |
| Browse community/published pages | Top navigation | `/api/community`, `/api/published` | Read-only | Implemented | Data loads through public browsing endpoints. |
| Subscribe to public bundle | Published detail action | `/api/published/<slug>/subscribe` style flow | Partly | Implemented | Subscriber can remove their local subscription later; publisher cannot force-delete subscriber copies. |

## Import And Export

| Flow | User entry | Backend/API | Reversible? | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| Dynamic export link | Export modal | `POST /api/personal/<acct>/exports` | Yes | Implemented | Link follows selected calendar and updates after timeline changes. |
| Static export link | Export modal | `POST /api/personal/<acct>/exports` | One-way snapshot | Implemented | Snapshot intentionally does not change after edits. |
| Current CSV/ICS export | Export buttons | `/api/personal/<acct>/exports/current.*` | Read-only | Implemented | Filtered by active calendar. |
| Import CSV/ICS | Import menu/file input | Import endpoints/client parsing | Mostly yes | Implemented | Imported entries can be edited or trashed afterward. |

## Notifications And Errors

| Flow | User entry | Backend/API | Reversible? | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| Notification center | Bell button | `/api/notifications`, `/api/notifications/read` | Yes | Implemented | Notifications live in the top notification area. |
| Error banner/toast | Automatic on failure | local notice + optional `/api/notifications` | Yes | Implemented | Drag/reorder failures should not leave permanent page banners. |
| Progress for slow operations | Calendar create/delete, workspace load | Client state | Yes | Implemented | Long operations show progress bars instead of silent waiting. |

## Admin And Official

| Flow | User entry | Backend/API | Reversible? | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| Official registry view | Official tab | Official workspace endpoints | Read-only for regular users | Implemented | Admin-only modification flow needs more audit before adding UI. |
| Admin notifications | Admin API path | `/api/notifications` with admin context | Yes | Implemented | Used for system notices. |
| Official source lifecycle | No complete UI map | Mixed admin/source paths | Unknown | Needs audit | Marked uncertain because current project docs do not fully define who can create, revise, archive, or delete official sources. |

## Cleanup Notes

- Calendar deletion is now an archive-and-move flow, not hard deletion.
- Reorder for tabs and timeline cards should stay frontend-optimistic; the database stores only numeric `position`.
- Any flow that can destroy user-created timelines should have a visible intermediate state first: hide, trash, archive, or detach.
- Missing future work: archived calendar restore UI, account deletion policy, and a stricter official/admin lifecycle map.
