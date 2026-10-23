---
name: plan
description: Plan a LinkedIn content calendar. Brainstorms post ideas (title, hook, angle) for a niche and saves them as dated ideas with the LinkedIn plan_posts tool. Use when the user asks for LinkedIn post ideas, a content plan, or a posting calendar.
---

# Plan a LinkedIn content calendar

1. From the request, work out the niche, how many posts and the cadence. Defaults: 30 ideas, one a day, starting tomorrow. If no niche or theme is given, ask for it; it is the one thing you cannot guess.
2. Call `list_posts` first. Don't repeat topics already planned. When the user is extending an existing plan, start the day after the last planned date.
3. Brainstorm the ideas. Each needs:
   - **title**: specific and opinionated. "Why most AI pilots never ship" works; "The future of AI" doesn't.
   - **hook**: the first line of the post, written to stop the scroll. A bold claim, a surprising number or a short story opener.
   - **notes**: 1–2 sentences on the angle: the one point the post makes, plus any example, data or personal experience to use.
4. Vary the formats across the calendar so it doesn't feel repetitive: personal story, contrarian take, how-to or framework, lesson learned, myth vs. fact, tools or resources roundup, behind the scenes, a question to the audience.
5. Save everything in one `plan_posts` call. Pass `start_date` and `every_days` for the cadence rather than computing dates yourself.
6. Show the result as a compact table (date, title) and offer to write the first post (the `write` skill).
