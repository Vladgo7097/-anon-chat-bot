#!/bin/sh
set -eu

install -m 700 /opt/anon-chat-bot/scripts/backup_database.sh /usr/local/sbin/anon-chat-backup

cat > /etc/systemd/system/anon-chat-backup.service <<'EOF'
[Unit]
Description=Verified backup of the anonymous chat database
After=docker.service
Requires=docker.service
OnFailure=anon-chat-backup-alert.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/anon-chat-backup
EOF

cat > /etc/systemd/system/anon-chat-backup-alert.service <<'EOF'
[Unit]
Description=Notify the anonymous chat owner about backup failure
After=docker.service

[Service]
Type=oneshot
ExecStart=/usr/bin/docker exec anon-chat-bot-bot-1 python -m services.backup_alert
EOF

cat > /etc/systemd/system/anon-chat-backup.timer <<'EOF'
[Unit]
Description=Daily anonymous chat database backup

[Timer]
OnCalendar=*-*-* 03:15:00 UTC
Persistent=true
RandomizedDelaySec=10m
Unit=anon-chat-backup.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now anon-chat-backup.timer
