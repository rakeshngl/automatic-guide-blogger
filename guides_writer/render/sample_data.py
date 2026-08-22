from guides_writer.render.schema import (
    ChecklistRow,
    ClosingAlert,
    CodeBlock,
    DiagramEdge,
    DiagramGroup,
    DiagramNode,
    DiagramSpec,
    Guide,
    GuideMeta,
    Phase,
    WarningBox,
)

SAMPLE_GUIDE = Guide(
    meta=GuideMeta(
        title="Umami Analytics",
        tagline=(
            "Self-host a privacy-first, cookieless web analytics dashboard for your sites "
            "using Docker, PostgreSQL, and a free open-source stack — no data leaves your VPS."
        ),
        navbar_badge="web analytics",
        hero_paragraph="Self-hosted analytics with zero tracking cookies.",
    ),
    intro=[
        (
            "Welcome to another official **UVF IT** enterprise deployment blueprint. "
            "Third-party analytics scripts slow your site down and leak visitor data to "
            "ad networks — this guide replaces all of that with one small container."
        ),
        (
            "Today we deploy **Umami**, an open-source alternative to Google Analytics. "
            "It stores every pageview in your own PostgreSQL database, respects GDPR by default, "
            "and gives you a clean dashboard at `https://analytics.yoursite.com`."
        ),
    ],
    warning_box=WarningBox(
        heading="\u26a0 Read this before you pick this architecture",
        bullets=[
            "**Stays on your machine:** all pageview records, the PostgreSQL database, and the Umami UI.",
            "**Leaves your machine:** nothing during normal operation — visitors only talk to your server.",
            "**Want zero-maintenance backups?** Add the scheduled `pg_dump` cron from Phase 4 before going live.",
        ],
    ),
    diagram=DiagramSpec(
        title="System Architecture Blueprint: Self-Hosted Analytics Pipeline",
        groups=[
            DiagramGroup(id="local", label="Local Docker — private network", style="local"),
            DiagramGroup(id="cloud", label="Cloud DNS (public entry point)", style="cloud"),
        ],
        nodes=[
            DiagramNode(id="visitor", label="Visitor Browser", sublabel="no cookies stored"),
            DiagramNode(id="proxy", label="Caddy Reverse Proxy", sublabel="auto HTTPS", group="local", style="tech"),
            DiagramNode(id="umami", label="Umami :3000", sublabel="Next.js app", group="local"),
            DiagramNode(id="db", label="PostgreSQL :5432", sublabel="persistent volume", group="local"),
            DiagramNode(id="dns", label="analytics.site.com", sublabel="A record \u2192 VPS IP", group="cloud", style="danger"),
        ],
        edges=[
            DiagramEdge(**{"from": "visitor", "to": "dns", "label": "1. page request"}),
            DiagramEdge(**{"from": "dns", "to": "proxy", "bidirectional": True}),
            DiagramEdge(**{"from": "proxy", "to": "umami", "label": "2. proxy_pass"}),
            DiagramEdge(**{"from": "umami", "to": "db", "bidirectional": True, "label": "3. SQL"}),
        ],
    ),
    phases=[
        Phase(
            title="Phase 1: Prepare the VPS",
            prose=[
                "Any 1 vCPU / 1 GB Ubuntu 22.04 box works. Install Docker and create a workspace:",
            ],
            code_blocks=[
                CodeBlock(
                    lang="bash",
                    filename=None,
                    code="""sudo apt update && sudo apt install -y docker.io docker-compose-plugin
# workspace for the stack
mkdir -p /opt/umami && cd /opt/umami""",
                )
            ],
        ),
        Phase(
            title="Phase 2: The Docker Compose Stack",
            prose=["Create `docker-compose.yml` with three services wired on a private network:"],
            code_blocks=[
                CodeBlock(
                    lang="yaml",
                    filename="docker-compose.yml",
                    code="""services:
  umami:
    image: ghcr.io/umami-software/umami:postgresql-latest
    environment:
      # the DB URL uses the service name 'db' as hostname
      DATABASE_URL: postgresql://umami:strongpass@db:5432/umami
      HASH_SALT: change-me-to-random-string
    depends_on: [db]
    restart: unless-stopped
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: umami
      POSTGRES_PASSWORD: strongpass
      POSTGRES_DB: umami
    volumes:
      # named volume survives restarts and redeploys
      - pg_data:/var/lib/postgresql/data
    restart: unless-stopped
volumes:
  pg_data:""",
                ),
                CodeBlock(lang="bash", filename=None, code="docker compose up -d"),
            ],
        ),
        Phase(
            title="Phase 3: First Login and Site Registration",
            prose=[
                "Browse to port 3000, sign in with the default account, and add your first website.",
                "The tracker snippet looks like this — paste it before `</head>` on your site:",
            ],
            code_blocks=[
                CodeBlock(
                    lang="html",
                    filename=None,
                    code='<script defer src="https://analytics.yoursite.com/script.js" data-website-id="YOUR-ID"></script>',
                )
            ],
        ),
        Phase(
            title="Phase 4: Nightly Backups",
            prose=["Schedule a compressed dump so a lost volume never means lost history:"],
            code_blocks=[
                CodeBlock(
                    lang="bash",
                    filename="/etc/cron.d/umami-backup",
                    code="0 2 * * * root docker exec umami-db-1 pg_dump -U umami umami | gzip > /opt/backups/umami-$(date +\\%F).sql.gz",
                )
            ],
        ),
    ],
    checklist=[
        ChecklistRow(
            check="Stack is actually running",
            command_why="`docker compose ps` — both services must show `Up`, not `Restarting`.",
        ),
        ChecklistRow(
            check="Database is persisting",
            command_why="Restart containers, then confirm yesterday's pageviews still appear — proves the named volume works.",
        ),
        ChecklistRow(
            check="Port isn't public",
            command_why="From another machine, `curl http://<vps-ip>:5432` should fail — PostgreSQL stays internal to the compose network.",
        ),
        ChecklistRow(
            check="Backups exist",
            command_why="`ls /opt/backups` after 02:00 — a `.sql.gz` file must be present and non-empty.",
        ),
    ],
    closing_alert=ClosingAlert(
        heading="What's Actually Private Here",
        body=(
            "Every analytics event lives inside your own PostgreSQL volume; Umami sets no tracking "
            "cookies and never phones home. Your only external dependency is the public DNS record "
            "pointing visitors at your VPS — everything else is yours."
        ),
    ),
)
