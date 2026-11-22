# News intelligence

A free GitHub Actions RSS/Atom collector for the 80 feeds in [feeds.txt](feeds.txt).

## What it publishes

The scheduled workflow runs daily at 13:17 UTC (08:17 EST / 09:17 EDT) and can also be started from **Actions → Collect News → Run workflow**.

GitHub Pages contains a small landing page, four date-scoped topic pages, article JSON, and an auditable feed coverage report:

- AI research
- Cloud and industry
- Automation and infrastructure
- Business and product strategy
- Per-feed status, HTTP result, entry counts, errors, and warnings

The collection window begins at midnight yesterday in America/New_York and ends when the run starts. Entries without a usable timestamp are retained and explicitly marked **undated**. arXiv abstracts are taken from the feed's description/summary. Duplicate links are collapsed after removing common tracking parameters.

## First run

In **Settings → Pages**, select **GitHub Actions** for the build and deployment source if it is not already selected. Then use **Actions → Collect News → Run workflow**. The workflow uses GitHub's Pages actions and needs Actions and Pages enabled for the repository.

The first daily run publishes the report at the repository's GitHub Pages URL. Scheduled runs may be delayed by GitHub, and scheduled workflows in inactive public repositories can be disabled by GitHub.

## Files

- `collector.py`: feed fetching, parsing, filtering, deduplication, and publishing.
- `feeds.txt`: the 80 source URLs.
- `.github/workflows/collect.yml`: scheduled/manual collection and Pages deployment.
