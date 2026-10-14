import os
import subprocess
import sys

for command in [("migrate", "--noinput"), ("collectstatic", "--noinput")]:
    subprocess.run([sys.executable, "manage.py", *command], check=True)
os.execvp(
    "gunicorn",
    [
        "gunicorn",
        "config.wsgi:application",
        "--bind",
        "0.0.0.0:8000",
        "--workers",
        os.environ.get("WEB_CONCURRENCY", "2"),
        "--timeout",
        "60",
        "--access-logformat",
        "%(h)s %(m)s %(s)s %(L)s",
        "--access-logfile",
        "-",
        "--error-logfile",
        "-",
    ],
)
