# thousand-emails

An outbound system where one SDR releases 1,000 tailored emails a day: patch cutting, tiered sequences,
a play-based draft engine with a decision record, a release queue, a sender, and the decision-to-outcome join.
Runs end-to-end on synthetic data; plugs into Salesforce, Outreach, Gong, BigQuery and an enrichment vendor
by flipping `config/sources.yaml`.

Start with `CLAUDE.md` (the project's memory) and `docs/system-map-slides.html` (the architecture).

    cp .env.example .env
    make seed && make up && make load && make schema && make test
