---
name: write
description: Write, review, schedule and publish LinkedIn posts in the user's voice. Drafts a post from a planned idea or any topic, saves it as a draft, can add a title-card image, and schedules or publishes it once the user approves. Use whenever the user wants to write, edit, schedule or publish a LinkedIn post.
---

# Write a LinkedIn post

## Workflow

1. **Pick the source.**
   - "Today's post", "the next post" or a post id: call `list_posts` with status `idea` and take the earliest one; overdue ideas come first. Read it with `get_post`.
   - A topic or rough notes from the user: write from those.
2. **Write** the post, following the style guide below.
3. **Save** it with `save_post`. Pass `post_id` when the post comes from a planned idea.
4. **Show** the user the exact text inside a plain code block, so nothing gets rendered, along with the word count and planned time.
5. **Image (optional).** If the user wants one, call `make_post_image` with a headline of 10 words or fewer and `post_id` to attach it. To attach the user's own image, use `save_post(image_path=...)`.
6. **Revise** until the user is happy. Every revision goes through `save_post`.
7. **Publish** only after an explicit go-ahead for that exact text:
   - `schedule_post` publishes at the planned time. Use it when the user names a time, or keep the planned one if it is in the future.
   - `publish_post` posts right now.
   Then share the post URL, or confirm when it is scheduled to go out.

Never schedule or publish without explicit approval of the final text. "Looks good" about an earlier version doesn't count once the text has changed.

## Style guide

**Voice**
- First person ("I", "we", "my"): personal and direct.
- Sound like a real professional sharing a genuine insight, not marketing copy.
- Use plain, conversational language. No corporate buzzwords or AI-sounding phrases (e.g. "in today's fast-paced world", "unlock", "delve", "game-changer", "let's dive in").
- Vary the sentence length: mix short, punchy lines with longer ones.
- Show some personality. Be a little bold and honest, even slightly contrarian when it fits the topic.

**Structure**
- 150–220 words.
- Line 1 is the hook: one short, punchy sentence with no intro fluff.
- Put a blank line between paragraphs. Keep paragraphs to 1–3 lines.
- Use "•" bullets only for lists, with no more than 5 bullets.
- End with a direct question to the reader or a call to action, on a line of its own.

**Formatting (LinkedIn renders plain text only)**
- Never use `**`, `*`, `_` or markdown headings. They show up as literal characters.
- For emphasis, use strong word choice, or CAPS on 1–2 words at most.
- Hashtags: 7–9 relevant, niche-specific tags as `#CamelCase` on the last line, separated by spaces. Avoid generic ones like #Business or #Life.
- Write the text exactly as it should appear. The tools escape characters for LinkedIn's API automatically.
