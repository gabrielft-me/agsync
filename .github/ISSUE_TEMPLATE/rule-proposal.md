---
name: Rule proposal
about: Propose a new integrity check
labels: rule
---

**What silently breaks today?**
Describe the failure as it appeared in a real repo, not in the abstract.

**Why can't a human catch it by reading?**
The best rules catch things that look fine to a careful reader.

**Minimal failing example**
The smallest memory files that reproduce it.

**Proposed severity**
`error` (the memory is now wrong) or `warn` (the memory is fragile).
