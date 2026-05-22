# Gmail & Calendar MCP — Account Reference

This MCP server manages multiple Google accounts. When the user refers to "personal" or "work" email/calendar, use the mappings below.

## Account Mappings

| Label | Account key | Email |
|-------|-------------|-------|
| personal, my email, my calendar | `personal` | maryaam2209@gmail.com |
| work, work email, work calendar | `work` | maryamworkx@gmail.com |

## How to use

- All MCP tools accept an `account` parameter — always pass the **account key** (`personal` or `work`), not the email.
- To list all calendars within an account: call `calendar_list_calendars` with the account key. Each account can have multiple calendars (primary, shared, etc.).
- To read events from a specific calendar inside an account: call `calendar_list_events` with the account key and the `calendar_id` from the list above.

## Common patterns

- "events from my personal calendar" → `calendar_list_events(account="personal", calendar_id="primary")`
- "events from my work calendar" → `calendar_list_events(account="work", calendar_id="primary")`
- "emails from both accounts" → call `gmail_search` twice, once per account
- "add to my work calendar" → `calendar_create_event(account="work", ...)`
