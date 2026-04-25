#!/usr/bin/env python3
"""Generate the Deployment & Operations Guide PDF for the Telegram Game Platform."""

import os, sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch, mm
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle,
    KeepTogether, CondPageBreak, ListFlowable, ListItem,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily

# ── Output path ──────────────────────────────────────────────
OUTPUT = "/home/z/my-project/download/telegram-game-platform/Deployment_and_Operations_Guide.pdf"

# ── Palette (from pdf.py palette.generate) ──────────────────
ACCENT       = colors.HexColor('#298eaf')
TEXT_PRIMARY  = colors.HexColor('#191b1c')
TEXT_MUTED    = colors.HexColor('#81878d')
BG_SURFACE   = colors.HexColor('#e0e4e7')
BG_PAGE      = colors.HexColor('#eceef0')
TABLE_HEADER_COLOR = ACCENT
TABLE_HEADER_TEXT  = colors.white
TABLE_ROW_EVEN     = colors.white
TABLE_ROW_ODD      = BG_SURFACE

# ── Font Registration ────────────────────────────────────────
pdfmetrics.registerFont(TTFont('DejaVuSerif', '/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf'))
pdfmetrics.registerFont(TTFont('DejaVuSerifBold', '/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf'))
pdfmetrics.registerFont(TTFont('DejaVuSans', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
pdfmetrics.registerFont(TTFont('DejaVuSansBold', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
pdfmetrics.registerFont(TTFont('DejaVuMono', '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'))
registerFontFamily('DejaVuSerif', normal='DejaVuSerif', bold='DejaVuSerifBold')
registerFontFamily('DejaVuSans', normal='DejaVuSans', bold='DejaVuSansBold')

# ── Styles ───────────────────────────────────────────────────
BODY_FONT = 'DejaVuSerif'
HEADING_FONT = 'DejaVuSerif'

styles = getSampleStyleSheet()

title_style = ParagraphStyle(
    'CustomTitle', fontName=HEADING_FONT, fontSize=28, leading=34,
    textColor=ACCENT, alignment=TA_CENTER, spaceAfter=12,
)
subtitle_style = ParagraphStyle(
    'CustomSubtitle', fontName=BODY_FONT, fontSize=14, leading=20,
    textColor=TEXT_MUTED, alignment=TA_CENTER, spaceAfter=30,
)
h1_style = ParagraphStyle(
    'H1', fontName=HEADING_FONT, fontSize=20, leading=26,
    textColor=ACCENT, spaceBefore=24, spaceAfter=12,
)
h2_style = ParagraphStyle(
    'H2', fontName=HEADING_FONT, fontSize=15, leading=20,
    textColor=TEXT_PRIMARY, spaceBefore=18, spaceAfter=8,
)
h3_style = ParagraphStyle(
    'H3', fontName=HEADING_FONT, fontSize=12, leading=16,
    textColor=TEXT_PRIMARY, spaceBefore=12, spaceAfter=6,
)
body_style = ParagraphStyle(
    'Body', fontName=BODY_FONT, fontSize=10.5, leading=17,
    textColor=TEXT_PRIMARY, alignment=TA_JUSTIFY, spaceAfter=8,
    wordWrap='CJK',
)
body_left = ParagraphStyle(
    'BodyLeft', fontName=BODY_FONT, fontSize=10.5, leading=17,
    textColor=TEXT_PRIMARY, alignment=TA_LEFT, spaceAfter=8,
)
bullet_style = ParagraphStyle(
    'Bullet', fontName=BODY_FONT, fontSize=10.5, leading=17,
    textColor=TEXT_PRIMARY, alignment=TA_LEFT, spaceAfter=4,
    leftIndent=20, bulletIndent=8,
)
code_style = ParagraphStyle(
    'Code', fontName='DejaVuMono', fontSize=8.5, leading=13,
    textColor=colors.HexColor('#1a1a2e'), alignment=TA_LEFT,
    spaceAfter=4, spaceBefore=4,
    backColor=colors.HexColor('#f4f5f7'),
    leftIndent=12, rightIndent=12,
    borderPadding=6,
)
caption_style = ParagraphStyle(
    'Caption', fontName=BODY_FONT, fontSize=9, leading=13,
    textColor=TEXT_MUTED, alignment=TA_CENTER, spaceAfter=6,
)
header_cell_style = ParagraphStyle(
    'HeaderCell', fontName=BODY_FONT, fontSize=10, leading=14,
    textColor=TABLE_HEADER_TEXT, alignment=TA_CENTER,
)
cell_style = ParagraphStyle(
    'Cell', fontName=BODY_FONT, fontSize=9.5, leading=14,
    textColor=TEXT_PRIMARY, alignment=TA_LEFT,
)
cell_center = ParagraphStyle(
    'CellCenter', fontName=BODY_FONT, fontSize=9.5, leading=14,
    textColor=TEXT_PRIMARY, alignment=TA_CENTER,
)

# ── Helpers ──────────────────────────────────────────────────
def P(text, style=body_style):
    return Paragraph(text, style)

def H1(text):
    return Paragraph(text, h1_style)

def H2(text):
    return Paragraph(text, h2_style)

def H3(text):
    return Paragraph(text, h3_style)

def bullet(text):
    return Paragraph(text, bullet_style)

def code(text):
    return Paragraph(text, code_style)

def make_table(headers, rows, col_widths=None):
    """Create a styled table."""
    data = [[Paragraph(f'<b>{h}</b>', header_cell_style) for h in headers]]
    for row in rows:
        data.append([Paragraph(str(c), cell_style) for c in row])

    if col_widths is None:
        col_widths = [460 / len(headers)] * len(headers)

    t = Table(data, colWidths=col_widths, hAlign='CENTER')
    style_cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), TABLE_HEADER_COLOR),
        ('TEXTCOLOR', (0, 0), (-1, 0), TABLE_HEADER_TEXT),
        ('GRID', (0, 0), (-1, -1), 0.5, TEXT_MUTED),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]
    for i in range(1, len(data)):
        bg = TABLE_ROW_EVEN if i % 2 == 1 else TABLE_ROW_ODD
        style_cmds.append(('BACKGROUND', (0, i), (-1, i), bg))
    t.setStyle(TableStyle(style_cmds))
    return t

def spacer(pts=12):
    return Spacer(1, pts)

# ── Build Document ───────────────────────────────────────────
doc = SimpleDocTemplate(
    OUTPUT,
    pagesize=A4,
    leftMargin=1.0*inch,
    rightMargin=1.0*inch,
    topMargin=1.0*inch,
    bottomMargin=1.0*inch,
)

story = []

# ═══════════════════════════════════════════════════════════════
# TITLE PAGE
# ═══════════════════════════════════════════════════════════════
story.append(Spacer(1, 100))
story.append(P('<b>Deployment and Operations Guide</b>', title_style))
story.append(spacer(12))
story.append(P('Telegram Multiplayer Game Platform', subtitle_style))
story.append(spacer(20))
story.append(P('Complete guide for deploying, configuring, and running the platform 24/7 on cloud infrastructure.', ParagraphStyle(
    'SubDesc', fontName=BODY_FONT, fontSize=11, leading=17,
    textColor=TEXT_MUTED, alignment=TA_CENTER, spaceAfter=20,
)))
story.append(spacer(40))
story.append(P('Version 1.0 | April 2026', ParagraphStyle(
    'Version', fontName=BODY_FONT, fontSize=10, leading=14,
    textColor=TEXT_MUTED, alignment=TA_CENTER,
)))

story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════
# TABLE OF CONTENTS (manual for SimpleDocTemplate)
# ═══════════════════════════════════════════════════════════════
story.append(P('<b>Table of Contents</b>', h1_style))
story.append(spacer(8))

toc_entries = [
    ("1.", "Vercel vs Render: The Definitive Answer"),
    ("2.", "Recommended Deployment Platforms"),
    ("3.", "Step-by-Step: Deploy to Render"),
    ("4.", "Step-by-Step: Deploy with Docker"),
    ("5.", "Step-by-Step: Deploy to Oracle Cloud (Always Free)"),
    ("6.", "Improvements Added for Production"),
    ("7.", "Architecture Additions for Reliability"),
    ("8.", "Security Hardening Checklist"),
    ("9.", "Monitoring and Alerting Setup"),
    ("10.", "Backup and Recovery Strategy"),
    ("11.", "Troubleshooting Common Issues"),
    ("12.", "Environment Variables Reference"),
]

for num, title in toc_entries:
    story.append(P(f'{num}  {title}', ParagraphStyle(
        'TOCEntry', fontName=BODY_FONT, fontSize=11, leading=18,
        textColor=TEXT_PRIMARY, leftIndent=20,
    )))

story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════
# SECTION 1: VERCEL VS RENDER
# ═══════════════════════════════════════════════════════════════
story.append(H1('1. Vercel vs Render: The Definitive Answer'))
story.append(spacer(6))

story.append(P(
    '<b>You CANNOT deploy this Telegram bot on Vercel.</b> Vercel is designed for serverless functions '
    'that respond to HTTP requests and terminate within seconds. Your bot is a long-running Python process '
    'that maintains persistent connections to the Telegram API via polling. It must run continuously, '
    '24 hours a day, 7 days a week, without ever stopping. Vercel serverless functions have a maximum '
    'execution time of 10 seconds on the free tier and 60 seconds on the Pro tier, after which the '
    'function is forcefully terminated. This makes Vercel fundamentally incompatible with long-running '
    'bot processes.'
))
story.append(P(
    'Render, on the other hand, supports <b>Background Workers</b> which are long-running processes '
    'that stay alive indefinitely. This is exactly what a Telegram bot needs. Render also provides '
    'health checks, automatic restarts on crashes, persistent disk storage on paid plans, and a '
    'generous free tier for experimentation. The key distinction is that Render treats your bot as a '
    'first-class long-running service, while Vercel treats it as a disposable request handler.'
))

story.append(spacer(8))
story.append(make_table(
    ['Feature', 'Vercel', 'Render (Worker)'],
    [
        ['Long-running processes', 'No (serverless, 10-60s max)', 'Yes (indefinite)'],
        ['Telegram polling', 'Impossible (killed after timeout)', 'Fully supported'],
        ['WebSocket support', 'Limited', 'Full support'],
        ['Persistent storage', 'No (ephemeral filesystem)', 'Yes (persistent disk on paid)'],
        ['Health checks', 'Via /api/ functions only', 'Native HTTP health checks'],
        ['Auto-restart on crash', 'No (serverless auto-scales new)', 'Yes (automatic)'],
        ['Custom domain', 'Yes', 'Yes (paid plans)'],
        ['Free tier', '100 GB bandwidth/month', '750 hours/month (spins down)'],
        ['Paid tier starts at', '$20/month (Pro)', '$7/month (Starter)'],
        ['Python .py file support', 'Serverless functions only', 'Full process support'],
    ],
    col_widths=[130, 165, 165]
))
story.append(spacer(4))
story.append(P('<b>Table 1:</b> Vercel vs Render comparison for Telegram bot deployment', caption_style))

story.append(spacer(8))
story.append(P(
    '<b>Important note about Render free tier:</b> The free tier spins down your service after 15 minutes '
    'of inactivity. For a Telegram bot, "inactivity" means no incoming messages, but the polling loop itself '
    'counts as activity. In practice, most bots stay active on the free tier, but if your bot sees zero '
    'traffic for 15 minutes, it may spin down and miss messages until a new request wakes it. For guaranteed '
    '24/7 uptime, use the Starter plan ($7/month) which never spins down.'
))

# ═══════════════════════════════════════════════════════════════
# SECTION 2: RECOMMENDED PLATFORMS
# ═══════════════════════════════════════════════════════════════
story.append(H1('2. Recommended Deployment Platforms'))
story.append(spacer(6))

story.append(P(
    'While Render is the recommended platform for most users, there are several alternatives worth considering '
    'depending on your budget, technical requirements, and expected traffic volume. Each platform has distinct '
    'trade-offs between cost, reliability, ease of use, and feature set. The table below provides a comprehensive '
    'comparison to help you make an informed decision.'
))

story.append(spacer(8))
story.append(make_table(
    ['Platform', 'Type', 'Free Tier', 'Paid Start', 'Best For'],
    [
        ['Render', 'PaaS (Worker)', '750 hrs/mo (spins down)', '$7/mo', 'Easiest setup, great DX'],
        ['Railway', 'PaaS', '$5 credit/mo', '$5/mo usage', 'Simple, generous free credit'],
        ['Fly.io', 'Container PaaS', '3 shared VMs', '$1.94/mo/VM', 'Global edge deployment'],
        ['Oracle Cloud', 'IaaS (VM)', 'Always Free (4 ARM VMs)', 'Pay-as-you-go', 'Maximum free resources'],
        ['DigitalOcean', 'IaaS (Droplet)', 'None', '$4/mo', 'Full control, simple pricing'],
        ['Hetzner', 'IaaS (VPS)', 'None', '3.29 EUR/mo', 'Cheapest full VPS option'],
    ],
    col_widths=[80, 80, 105, 80, 115]
))
story.append(spacer(4))
story.append(P('<b>Table 2:</b> Deployment platform comparison', caption_style))

story.append(spacer(8))
story.append(H2('2.1 Recommendation Summary'))
story.append(P(
    'For beginners and those who want the fastest path to production, <b>Render</b> is the clear winner. '
    'It provides automatic builds from Git, native health checks, one-click environment variable management, '
    'and built-in log streaming. The Starter plan at $7/month provides guaranteed 24/7 uptime with no spin-down, '
    'which is essential for a polling-based Telegram bot. If you have a larger user base or need more resources, '
    'upgrading to higher Render plans is seamless.'
))
story.append(P(
    'For users who need maximum free resources and are comfortable with Linux server administration, '
    '<b>Oracle Cloud Always Free tier</b> provides 4 ARM-based Ampere cores, 24 GB RAM, and 200 GB block '
    'storage at zero cost forever. This is significantly more powerful than any other free option, but requires '
    'manual server setup, SSH access, and self-managed monitoring.'
))

# ═══════════════════════════════════════════════════════════════
# SECTION 3: RENDER DEPLOYMENT
# ═══════════════════════════════════════════════════════════════
story.append(H1('3. Step-by-Step: Deploy to Render'))
story.append(spacer(6))

story.append(P(
    'This section walks you through the complete process of deploying the Telegram Game Platform to Render, '
    'from initial account setup to verifying your bots are running in production. Render provides the simplest '
    'deployment experience for Python background workers, with automatic Git-based builds, native environment '
    'variable management, and built-in health check support.'
))

story.append(H2('3.1 Prerequisites'))
story.append(P('Before you begin, ensure you have the following:'))
prereqs = [
    'A GitHub or GitLab account with your project repository pushed',
    'Both Telegram bot tokens (from @BotFather)',
    'Your Telegram admin user ID(s) (from @userinfobot)',
    'A Render account (sign up at render.com with your GitHub account)',
]
for item in prereqs:
    story.append(bullet(f'&#8226;  {item}'))

story.append(H2('3.2 Push Your Code to GitHub'))
story.append(P(
    'If your code is not already on GitHub, create a new repository and push it. Render integrates directly '
    'with GitHub and GitLab to automatically build and deploy your application on every push. Make sure '
    'to add a .gitignore file that excludes the .env file, data/ directory, and __pycache__ directories. '
    'The .env.example file should be committed as a template, but your actual .env with real tokens must '
    'never be pushed to a public repository.'
))
story.append(code('git init<br/>'
                  'echo ".env" >> .gitignore<br/>'
                  'echo "data/" >> .gitignore<br/>'
                  'echo "__pycache__/" >> .gitignore<br/>'
                  'git add .<br/>'
                  'git commit -m "Initial commit"<br/>'
                  'git remote add origin https://github.com/YOUR_USERNAME/telegram-game-platform.git<br/>'
                  'git push -u origin main'))

story.append(H2('3.3 Create a Render Background Worker'))
story.append(P(
    'Log in to your Render dashboard and follow these steps to create a new background worker service. '
    'A background worker is a long-running process that does not receive HTTP traffic from the internet, '
    'which is exactly what a polling-based Telegram bot needs. The health check server we added runs on '
    'an internal port for Render to monitor, not for public access.'
))

steps_render = [
    ('1', 'Click "New" and select "Background Worker"'),
    ('2', 'Connect your GitHub/GitLab repository'),
    ('3', 'Set the service name (e.g., "game-platform")'),
    ('4', 'Set the Runtime to "Python 3"'),
    ('5', 'Set the Build Command to: pip install -r requirements.txt'),
    ('6', 'Set the Start Command to: python run.py'),
    ('7', 'Select the instance type (Starter recommended for 24/7)'),
    ('8', 'Click "Advanced" and add the environment variables (see below)'),
]
for num, step in steps_render:
    story.append(bullet(f'{num}.  {step}'))

story.append(H2('3.4 Set Environment Variables'))
story.append(P(
    'In the Render dashboard, navigate to your service settings and add the following environment variables. '
    'These are encrypted at rest and never exposed in logs. This is the most secure way to provide your '
    'bot tokens and configuration to the application.'
))
story.append(spacer(4))
story.append(make_table(
    ['Variable', 'Required', 'Example Value', 'Description'],
    [
        ['GAME_BOT_TOKEN', 'Yes', '123456:ABC-DEF...', 'Token from @BotFather for Game Bot'],
        ['ADMIN_BOT_TOKEN', 'Yes', '789012:GHI-JKL...', 'Token from @BotFather for Admin Bot'],
        ['ADMIN_IDS', 'Yes', '123456789', 'Comma-separated Telegram user IDs'],
        ['PORT', 'No', '10000', 'Health check server port (auto-set by Render)'],
        ['AUTO_RESTART', 'No', 'true', 'Enable auto-restart on fatal errors'],
    ],
    col_widths=[110, 55, 125, 170]
))

story.append(H2('3.5 Verify Deployment'))
story.append(P(
    'After deploying, you can verify your bots are running correctly through several methods. '
    'First, check the Render dashboard logs for the startup messages: you should see "Both bots are running! '
    'Platform is ready." and "Background tasks started". Second, check the health endpoint by visiting '
    'your service URL at /health (Render assigns a URL like game-platform-xxxx.onrender.com). Third, '
    'open Telegram and send /start to your Game Bot and Admin Bot to confirm they respond. Finally, '
    'check the /status endpoint for detailed service status including uptime and component health.'
))

# ═══════════════════════════════════════════════════════════════
# SECTION 4: DOCKER DEPLOYMENT
# ═══════════════════════════════════════════════════════════════
story.append(H1('4. Step-by-Step: Deploy with Docker'))
story.append(spacer(6))

story.append(P(
    'Docker provides a consistent, reproducible deployment environment that works identically on any host. '
    'The project includes a production-ready Dockerfile and docker-compose.yml that handle Python dependencies, '
    'health checks, and data persistence automatically. This approach is recommended for users deploying on '
    'VPS providers like DigitalOcean, Hetzner, or Oracle Cloud, where you have full control over the server.'
))

story.append(H2('4.1 Quick Start with Docker Compose'))
story.append(P(
    'The simplest way to deploy with Docker is using docker-compose, which handles building the image, '
    'setting environment variables, configuring health checks, and persisting data. Follow these steps '
    'on any server with Docker installed:'
))
story.append(code('# 1. Clone or upload your project to the server<br/>'
                  'git clone https://github.com/YOUR_USERNAME/telegram-game-platform.git<br/>'
                  'cd telegram-game-platform<br/><br/>'
                  '# 2. Create your .env file from the template<br/>'
                  'cp .env.example .env<br/><br/>'
                  '# 3. Edit .env and fill in your real values<br/>'
                  'nano .env<br/><br/>'
                  '# 4. Start the platform<br/>'
                  'docker compose up -d<br/><br/>'
                  '# 5. Check logs<br/>'
                  'docker compose logs -f<br/><br/>'
                  '# 6. Check health<br/>'
                  'curl http://localhost:10000/health<br/>'
                  'curl http://localhost:10000/status'))

story.append(H2('4.2 Manual Docker Build and Run'))
story.append(P(
    'If you prefer more control over the Docker build and run process, you can build and run the container '
    'manually. This gives you fine-grained control over resource limits, network configuration, and restart '
    'policies. The following commands build the image and run it with automatic restart and a named volume '
    'for data persistence:'
))
story.append(code('# Build the image<br/>'
                  'docker build -t game-platform .<br/><br/>'
                  '# Run with auto-restart and persistent data<br/>'
                  'docker run -d \\<br/>'
                  '  --name game-platform \\<br/>'
                  '  --restart unless-stopped \\<br/>'
                  '  -e GAME_BOT_TOKEN="your_token" \\<br/>'
                  '  -e ADMIN_BOT_TOKEN="your_token" \\<br/>'
                  '  -e ADMIN_IDS="123456789" \\<br/>'
                  '  -p 10000:10000 \\<br/>'
                  '  -v game-data:/app/data \\<br/>'
                  '  game-platform'))

story.append(H2('4.3 Docker Restart Policy'))
story.append(P(
    'The <b>--restart unless-stopped</b> flag is critical for 24/7 operation. It ensures Docker automatically '
    'restarts the container if it crashes, if the Docker daemon restarts, or if the server reboots. The only '
    'time the container stays stopped is if you explicitly run <b>docker stop</b>. Combined with the application-level '
    'auto-restart feature in run.py (which handles Python-level crashes), this provides a two-layer safety net: '
    'the application restarts itself for recoverable errors, and Docker restarts the entire container for '
    'unrecoverable crashes. This dual restart mechanism ensures maximum uptime.'
))

# ═══════════════════════════════════════════════════════════════
# SECTION 5: ORACLE CLOUD
# ═══════════════════════════════════════════════════════════════
story.append(H1('5. Step-by-Step: Deploy to Oracle Cloud (Always Free)'))
story.append(spacer(6))

story.append(P(
    'Oracle Cloud Infrastructure (OCI) offers an Always Free tier that includes 4 Ampere A1 ARM cores, '
    '24 GB of RAM, and 200 GB of block storage. This is by far the most generous free cloud offering available, '
    'and it is powerful enough to run the Telegram Game Platform with plenty of headroom. The trade-off is that '
    'you must manage the server yourself, including OS updates, firewall configuration, and process supervision.'
))

story.append(H2('5.1 Create an OCI Account and VM'))
story.append(P(
    'Sign up at cloud.oracle.com with a valid credit card (required for verification, but you will not be '
    'charged if you stay within Always Free limits). After signing in, create a new Compute instance with '
    'the following configuration: Shape: Ampere A1 (4 OCPU, 24 GB RAM), Image: Canonical Ubuntu 22.04, '
    'Boot volume: 50 GB, and add your SSH public key for access. Once the instance is created, note the '
    'public IP address and SSH into it.'
))

story.append(H2('5.2 Server Setup'))
story.append(code('# SSH into your instance<br/>'
                  'ssh ubuntu@YOUR_PUBLIC_IP<br/><br/>'
                  '# Update system packages<br/>'
                  'sudo apt update && sudo apt upgrade -y<br/><br/>'
                  '# Install Python 3.11 and pip<br/>'
                  'sudo apt install -y python3.11 python3.11-venv python3-pip<br/><br/>'
                  '# Install Docker (alternative approach)<br/>'
                  'curl -fsSL https://get.docker.com | sudo sh<br/>'
                  'sudo usermod -aG docker ubuntu<br/><br/>'
                  '# Open port 10000 for health checks<br/>'
                  'sudo iptables -I INPUT -p tcp --dport 10000 -j ACCEPT<br/>'
                  'sudo netfilter-persistent save'))

story.append(H2('5.3 Set Up systemd Service'))
story.append(P(
    'For 24/7 operation on a VPS, systemd is the recommended process manager. It provides automatic restarts, '
    'log management, and startup ordering. Create the following service file to manage your bot as a system service:'
))
story.append(code('# /etc/systemd/system/game-platform.service<br/>'
                  '[Unit]<br/>'
                  'Description=Telegram Game Platform<br/>'
                  'After=network.target<br/><br/>'
                  '[Service]<br/>'
                  'Type=simple<br/>'
                  'User=ubuntu<br/>'
                  'WorkingDirectory=/home/ubuntu/telegram-game-platform<br/>'
                  'EnvironmentFile=/home/ubuntu/telegram-game-platform/.env<br/>'
                  'ExecStart=/usr/bin/python3.11 run.py<br/>'
                  'Restart=always<br/>'
                  'RestartSec=10<br/>'
                  'StandardOutput=journal<br/>'
                  'StandardError=journal<br/><br/>'
                  '[Install]<br/>'
                  'WantedBy=multi-user.target'))

story.append(code('# Enable and start the service<br/>'
                  'sudo systemctl daemon-reload<br/>'
                  'sudo systemctl enable game-platform<br/>'
                  'sudo systemctl start game-platform<br/><br/>'
                  '# Check status<br/>'
                  'sudo systemctl status game-platform<br/><br/>'
                  '# View logs<br/>'
                  'sudo journalctl -u game-platform -f'))

story.append(P(
    'The systemd service ensures your bot starts automatically on server boot and restarts within 10 seconds '
    'of a crash. Combined with the application-level auto-restart in run.py, this provides three layers of '
    'restart protection: Python-level recovery, systemd-level process restart, and Docker-level container restart '
    '(if using Docker on the VPS). This layered approach guarantees near-zero downtime.'
))

# ═══════════════════════════════════════════════════════════════
# SECTION 6: IMPROVEMENTS ADDED
# ═══════════════════════════════════════════════════════════════
story.append(H1('6. Improvements Added for Production'))
story.append(spacer(6))

story.append(P(
    'The project has been enhanced with several production-grade features that are essential for reliable 24/7 '
    'deployment. These improvements address the gaps identified in the original codebase and ensure the platform '
    'operates securely and reliably in a cloud environment. Each improvement is described below with its purpose '
    'and technical implementation details.'
))

improvements = [
    ('Health Check HTTP Server', 'health_server.py',
     'A lightweight HTTP server running on port 10000 provides three endpoints: /health (liveness probe), '
     '/ready (readiness probe checking both bots and database), and /status (detailed JSON with uptime, '
     'component status, and last error). This is essential for deployment platforms like Render that require '
     'health check endpoints to monitor service health and trigger automatic restarts when the service becomes '
     'unresponsive. The server runs in a daemon thread so it does not interfere with the async bot loop.'),

    ('SIGTERM Signal Handling', 'run.py',
     'Containerized environments (Docker, Render, Kubernetes) send SIGTERM to gracefully shut down processes. '
     'The updated run.py registers signal handlers for both SIGTERM and SIGINT that set a shutdown event, '
     'allowing the application to cleanly stop all components in order: background tasks, bot updaters, '
     'bot applications, database connections, and the health server. Without SIGTERM handling, Docker would '
     'force-kill the process after a timeout, potentially corrupting the SQLite database.'),

    ('Log Rotation', 'run.py + config.py',
     'The logging system now uses RotatingFileHandler with configurable maximum file size (default 10 MB) '
     'and backup count (default 5 files). This prevents the log file from growing indefinitely and consuming '
     'all available disk space, which would eventually crash the application. The configuration is controlled '
     'via LOG_MAX_BYTES and LOG_BACKUP_COUNT environment variables.'),

    ('python-dotenv Integration', 'config.py + requirements.txt',
     'The config.py module now automatically loads environment variables from a .env file if python-dotenv '
     'is installed. This eliminates the need to manually export variables before running the bot and provides '
     'a convenient local development workflow: copy .env.example to .env, fill in your values, and run python '
     'run.py directly. In production, environment variables from Render or Docker take precedence over .env.'),

    ('Database Cleanup on Shutdown', 'run.py',
     'The close_db() function is now called during shutdown to properly close all SQLite connections. Previously, '
     'connections could be left open during forced shutdowns, potentially leaving WAL lock files that prevent '
     'the next startup from accessing the database. The cleanup runs as the final step in the shutdown sequence, '
     'after all bot and background task operations have completed.'),

    ('Auto-Restart Supervisor', 'run.py',
     'A built-in supervisor mode wraps the main async loop with automatic restart logic. If the application '
     'crashes with an unhandled exception, it is automatically restarted up to 5 times with exponential backoff '
     '(starting at 10 seconds, doubling up to 120 seconds). This provides an extra safety net beyond the '
     'platform-level restart (Render/Docker/systemd). Controlled by the AUTO_RESTART environment variable.'),

    ('Docker Support', 'Dockerfile + docker-compose.yml',
     'A production-ready Dockerfile using Python 3.11-slim with multi-stage optimization, health check '
     'integration via HEALTHCHECK instruction, and curl for the health probe. The docker-compose.yml provides '
     'one-command deployment with automatic restart policies, volume persistence for the SQLite database, '
     'and health check configuration.'),

    ('Render Blueprint', 'render.yaml',
     'A Render Blueprint configuration file that automates service creation. Simply push your code to GitHub, '
     'import the repository into Render, and the blueprint automatically creates the Background Worker service '
     'with the correct build and start commands. Environment variables for tokens are marked as secrets that '
     'must be set manually in the Render dashboard.'),
]

for title, files, desc in improvements:
    story.append(H2(f'6.{improvements.index((title, files, desc)) + 1} {title}'))
    story.append(P(f'<b>Files:</b> {files}'))
    story.append(P(desc))

# ═══════════════════════════════════════════════════════════════
# SECTION 7: ARCHITECTURE ADDITIONS
# ═══════════════════════════════════════════════════════════════
story.append(H1('7. Architecture Additions for Reliability'))
story.append(spacer(6))

story.append(P(
    'Beyond the immediate deployment improvements, the following architectural enhancements are recommended '
    'for ensuring long-term reliability and scalability of the platform. These additions address the most '
    'common failure modes for production Telegram bots and provide the monitoring, alerting, and recovery '
    'capabilities needed for genuine 24/7 operation.'
))

story.append(H2('7.1 Three-Layer Restart Protection'))
story.append(P(
    'The platform now implements three layers of restart protection, each designed to handle different '
    'failure scenarios. Layer 1 (Application Level) handles Python exceptions in the main loop, restarting '
    'the async runtime with exponential backoff. Layer 2 (Process Level, via systemd or Docker) handles '
    'complete process crashes, including segfaults and out-of-memory kills. Layer 3 (Platform Level, via '
    'Render or Docker restart policy) handles host-level failures and reboots. This layered approach means '
    'that even if one restart mechanism fails, the next one catches the failure.'
))

story.append(H2('7.2 Graceful Shutdown Sequence'))
story.append(P(
    'When a shutdown signal is received, the platform executes a carefully ordered shutdown sequence to '
    'prevent data loss and ensure clean state. The sequence is: (1) Stop accepting new background tasks, '
    '(2) Cancel all running background tasks and wait for them to finish, (3) Stop the polling updaters '
    'for both bots, (4) Stop the bot applications, (5) Shutdown the bot application instances, (6) Close '
    'all database connections, and (7) Stop the health check server. Each step is wrapped in try/except '
    'to ensure the shutdown continues even if one component fails to stop cleanly.'
))

story.append(H2('7.3 Connection Resilience'))
story.append(P(
    'The database manager already implements stale connection detection and automatic reconnection. For the '
    'Telegram API connection, python-telegram-bot handles network errors and automatic retries internally. '
    'However, if the Telegram API is down for an extended period, the polling loop will eventually fail. '
    'The application-level auto-restart supervisor handles this case by restarting the entire process, which '
    're-establishes all connections from scratch. For databases, the WAL mode and busy timeout settings '
    'ensure that short lock contention does not cause connection failures.'
))

story.append(H2('7.4 Background Task Resilience'))
story.append(P(
    'Each of the 12 background tasks runs in its own asyncio task with a try/except wrapper around the entire '
    'loop body. If a single task iteration fails (for example, due to a database error or unexpected data), '
    'the error is logged and the task continues to the next iteration after the configured sleep interval. '
    'This ensures that a bug in one cleanup task (such as promotion rotation) does not crash the entire '
    'platform. The BackgroundTaskManager.stop() method cancels all tasks and awaits them with return_exceptions=True, '
    'ensuring a clean shutdown even if some tasks are in error states.'
))

# ═══════════════════════════════════════════════════════════════
# SECTION 8: SECURITY
# ═══════════════════════════════════════════════════════════════
story.append(H1('8. Security Hardening Checklist'))
story.append(spacer(6))

story.append(P(
    'Security is critical for a platform that handles user data, virtual currency, and financial transactions. '
    'The following checklist covers the essential security measures that should be verified and implemented '
    'before deploying to production. Each item is categorized by priority level and includes specific '
    'implementation guidance.'
))

security_items = [
    ('HIGH', 'Never commit bot tokens to Git',
     'The .env file is excluded via .gitignore. The .env.example file now uses placeholder values instead of '
     'real tokens. Always use environment variables for secrets, never hardcode them in source code. If tokens '
     'are accidentally pushed, revoke them immediately via @BotFather and generate new ones.'),
    ('HIGH', 'Use environment variables for all secrets',
     'Render and Docker provide encrypted environment variable storage. Never pass tokens as command-line '
     'arguments (visible in process listings) or write them to config files. The config.py module reads all '
     'secrets from os.getenv(), which is the correct approach.'),
    ('HIGH', 'SQL injection prevention',
     'All database queries use parameterized statements (the ? placeholder syntax in SQLite). Never use '
     'string formatting or f-strings to build SQL queries. Review all handler files to ensure user input '
     'is never directly interpolated into SQL.'),
    ('HIGH', 'Admin bot authorization',
     'The admin bot only responds to users whose Telegram ID is in the ADMIN_IDS list. Ensure this list '
     'is accurate and kept up-to-date. Consider implementing two-factor authentication for sensitive '
     'operations like wallet adjustments and withdrawal approvals.'),
    ('MEDIUM', 'Rate limiting',
     'Implement rate limiting on bot commands to prevent abuse. python-telegram-bot provides built-in '
     'rate limiting, but you should also add application-level rate limits on economic operations '
     '(wallet claims, marketplace purchases, withdrawal requests) to prevent rapid exploitation.'),
    ('MEDIUM', 'Input validation',
     'All user input (game names, channel links, withdrawal details) must be validated and sanitized '
     'before processing. Reject overly long inputs, special characters that could break formatting, '
     'and malformed URLs. The builder module already implements validation; extend this pattern to all handlers.'),
    ('MEDIUM', 'Database file permissions',
     'Ensure the data/game_platform.db file is readable and writable only by the application user. '
     'On Linux, use chmod 600. In Docker, the file is inside the container filesystem which provides '
     'isolation by default.'),
    ('LOW', 'HTTPS for webhook mode',
     'If you switch to webhook mode in the future, ensure the webhook URL uses HTTPS with a valid '
     'SSL certificate. Telegram requires HTTPS for webhook endpoints. Render provides automatic SSL '
     'for all services.'),
]

for priority, title, desc in security_items:
    color_map = {'HIGH': '#cc0000', 'MEDIUM': '#cc7700', 'LOW': '#007733'}
    story.append(H3(f'[{priority}] {title}'))
    story.append(P(desc))

# ═══════════════════════════════════════════════════════════════
# SECTION 9: MONITORING
# ═══════════════════════════════════════════════════════════════
story.append(H1('9. Monitoring and Alerting Setup'))
story.append(spacer(6))

story.append(P(
    'Effective monitoring is essential for maintaining 24/7 uptime. The health check server provides the '
    'foundation, but additional monitoring and alerting tools should be configured to detect problems before '
    'they affect users. This section describes how to set up monitoring at each layer of the deployment.'
))

story.append(H2('9.1 Render Built-in Monitoring'))
story.append(P(
    'Render provides built-in monitoring for all services, including CPU usage, memory usage, and service '
    'status. The Background Worker type supports health check URLs, which Render polls at regular intervals. '
    'If the health check fails, Render automatically restarts the service. Configure the health check in the '
    'Render dashboard by setting the Health Check Path to /health and the Health Check Protocol to HTTP. '
    'Render will check this endpoint every 30 seconds and restart the service if it fails 3 consecutive checks.'
))

story.append(H2('9.2 Log-based Alerting'))
story.append(P(
    'The application logs all errors, warnings, and critical events to both stdout and the rotating log file. '
    'On Render, logs are available in real-time via the dashboard and can be streamed to external services '
    'like Datadog, Papertrail, or Sentry for alerting. Set up alerts for the following log patterns: '
    '"CRITICAL" level messages (indicate fatal errors), "Fatal error" (application crash), and repeated '
    '"error" messages from background tasks (may indicate systemic issues).'
))

story.append(H2('9.3 Telegram Admin Alerts'))
story.append(P(
    'The game bot already sends error notifications to all admin IDs when an unhandled exception occurs. '
    'You can extend this by adding periodic health summary messages. For example, every hour the bot could '
    'send a message to admins with: number of active sessions, total users, pending withdrawals, and any '
    'errors in the last hour. This proactive monitoring ensures you are always aware of the platform state.'
))

# ═══════════════════════════════════════════════════════════════
# SECTION 10: BACKUP
# ═══════════════════════════════════════════════════════════════
story.append(H1('10. Backup and Recovery Strategy'))
story.append(spacer(6))

story.append(P(
    'The SQLite database is the single most critical piece of data in the platform. It contains all user '
    'wallets, transaction history, game sessions, marketplace items, and administrative logs. Losing this '
    'file means losing all user data and financial records. A robust backup strategy is essential.'
))

story.append(H2('10.1 Automated SQLite Backups'))
story.append(P(
    'SQLite provides a safe backup mechanism via the .backup command or the online_backup API. The recommended '
    'approach is to create a cron job that runs every hour and creates a timestamped copy of the database. '
    'Because SQLite uses WAL mode, the backup will be consistent even if the database is being written to '
    'during the backup. Use the following script on your server or inside the Docker container:'
))
story.append(code('#!/bin/bash<br/>'
                  '# backup_db.sh - Run via cron every hour<br/>'
                  'DB_PATH="/app/data/game_platform.db"<br/>'
                  'BACKUP_DIR="/app/data/backups"<br/>'
                  'TIMESTAMP=$(date +%Y%m%d_%H%M%S)<br/><br/>'
                  'mkdir -p $BACKUP_DIR<br/><br/>'
                  '# Use sqlite3 backup for consistency<br/>'
                  'sqlite3 $DB_PATH ".backup $BACKUP_DIR/db_$TIMESTAMP.db"<br/><br/>'
                  '# Compress the backup<br/>'
                  'gzip $BACKUP_DIR/db_$TIMESTAMP.db<br/><br/>'
                  '# Keep only last 72 hourly backups (3 days)<br/>'
                  'find $BACKUP_DIR -name "*.gz" -mtime +3 -delete'))

story.append(H2('10.2 Off-Site Backup'))
story.append(P(
    'Local backups protect against database corruption but not against server failure. Upload backups to '
    'cloud storage (AWS S3, Google Cloud Storage, or Backblaze B2) using the s3cmd or rclone tools. A daily '
    'upload of the compressed backup ensures you can recover from a complete server loss. Most cloud storage '
    'providers offer free tiers that are more than sufficient for database backups (typically under 100 MB '
    'per backup file).'
))

story.append(H2('10.3 Recovery Procedure'))
story.append(P(
    'To recover from a database loss: (1) Stop the application (docker compose down or systemctl stop), '
    '(2) Download the latest backup from cloud storage, (3) Decompress and copy to the data directory as '
    'game_platform.db, (4) Verify the file integrity with "sqlite3 game_platform.db PRAGMA integrity_check", '
    '(5) Restart the application. The entire recovery should take under 5 minutes. Practice this procedure '
    'before you need it to ensure you are familiar with the steps.'
))

# ═══════════════════════════════════════════════════════════════
# SECTION 11: TROUBLESHOOTING
# ═══════════════════════════════════════════════════════════════
story.append(H1('11. Troubleshooting Common Issues'))
story.append(spacer(6))

issues = [
    ('Bot stops responding after some time',
     'This is typically caused by the Render free tier spinning down after 15 minutes of inactivity. '
     'Upgrade to the Starter plan ($7/month) which never spins down. Alternatively, set up a cron job '
     'that pings the /health endpoint every 5 minutes from an external service like UptimeRobot (free) '
     'to keep the service active.'),
    ('Database is locked errors',
     'SQLite WAL mode and busy_timeout should prevent this, but if it still occurs, increase DB_TIMEOUT '
     'in config.py. Also verify that no other process is accessing the database file directly (such as a '
     'SQLite browser tool left open on your local machine). The busy_timeout of 30 seconds should be '
     'sufficient for all normal operations.'),
    ('Bot token invalid error on startup',
     'Verify that the environment variables are set correctly. Common mistakes include: extra spaces around '
     'the token, missing the colon between the bot ID and token hash, and using the wrong token for the '
     'wrong bot. Check that GAME_BOT_TOKEN and ADMIN_BOT_TOKEN match the tokens from @BotFather.'),
    ('High memory usage',
     'The SessionManager keeps active game sessions in memory. If you have many concurrent games, memory '
     'usage will grow. The session cleanup background task automatically ends stale sessions after 1 hour, '
     'and stale waiting rooms after 2 hours. If you need longer sessions, increase SESSION_TIMEOUT_SECONDS '
     'and STALE_ROOM_TIMEOUT_SECONDS in config.py.'),
    ('Admin bot not responding',
     'Ensure your Telegram user ID is correctly listed in ADMIN_IDS. The IDs must be numeric (not username). '
     'Get your ID by messaging @userinfobot on Telegram. Also verify that ADMIN_BOT_TOKEN is for the correct '
     'bot and that the bot has not been blocked by you.'),
    ('Log file growing too large',
     'The log rotation system now limits each log file to 10 MB and keeps 5 backup files (50 MB total). '
     'Adjust LOG_MAX_BYTES and LOG_BACKUP_COUNT environment variables if you need different limits. For '
     'Docker deployments, consider using the journald log driver instead of file-based logging.'),
]

for title, desc in issues:
    story.append(H3(title))
    story.append(P(desc))

# ═══════════════════════════════════════════════════════════════
# SECTION 12: ENV VARIABLES
# ═══════════════════════════════════════════════════════════════
story.append(H1('12. Environment Variables Reference'))
story.append(spacer(6))

story.append(P(
    'The following table provides a complete reference of all environment variables supported by the platform. '
    'Required variables must be set for the application to start. Optional variables have sensible defaults '
    'and only need to be set when you want to override the default behavior.'
))

story.append(spacer(8))
story.append(make_table(
    ['Variable', 'Required', 'Default', 'Description'],
    [
        ['GAME_BOT_TOKEN', 'Yes', '-', 'Telegram bot token for Game Bot (from @BotFather)'],
        ['ADMIN_BOT_TOKEN', 'Yes', '-', 'Telegram bot token for Admin Bot (from @BotFather)'],
        ['ADMIN_IDS', 'Yes', '-', 'Comma-separated admin Telegram user IDs'],
        ['PORT', 'No', '10000', 'Health check server port (auto-set by Render)'],
        ['AUTO_RESTART', 'No', 'true', 'Enable auto-restart on fatal errors'],
        ['LOG_MAX_BYTES', 'No', '10485760', 'Max log file size in bytes (10 MB)'],
        ['LOG_BACKUP_COUNT', 'No', '5', 'Number of rotated log file backups'],
        ['WEBHOOK_URL', 'No', '(empty)', 'Webhook URL (leave empty for polling mode)'],
    ],
    col_widths=[110, 55, 80, 215]
))
story.append(spacer(4))
story.append(P('<b>Table 3:</b> Complete environment variables reference', caption_style))

# ═══════════════════════════════════════════════════════════════
# BUILD PDF
# ═══════════════════════════════════════════════════════════════
doc.build(story)
print(f"PDF generated: {OUTPUT}")
