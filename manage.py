#!/usr/bin/env python
import os
import sys


def main():
    from decouple import config

    # Order: DJANGO_SETTINGS_MODULE from the shell, then from .env, then
    # production on Vercel (VERCEL=1) and development everywhere else.
    fallback = "config.settings.production" if os.environ.get("VERCEL") else "config.settings.development"
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", config("DJANGO_SETTINGS_MODULE", default=fallback))
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
