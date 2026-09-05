# Issue tracker: Local Markdown

Issues and specs live under `.scratch/`.

- Feature: `.scratch/<feature-slug>/`
- Spec: `.scratch/<feature-slug>/spec.md`
- Ticket: `.scratch/<feature-slug>/issues/<NN>-<slug>.md`
- Ticket state: a `Status:` line near the top
- Dependencies: a `Blocked by: NN, NN` line near the top
- Conversation history: append under `## Comments`

Each implementation ticket is a separate file. When a skill publishes to the
tracker, it creates or updates the corresponding file under the feature
directory. When it fetches a ticket, it reads the referenced path directly.
