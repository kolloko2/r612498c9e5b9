# Windows training stand helpers

These scripts supervise the installed WSL services and portable MicroSIP setup used
by the local training stand. Place the portable MicroSIP distribution in a sibling
`MicroSIP` directory before running `Start-Training.cmd` or `Open-Voice-Chat.cmd`.
The executable, account database, call log, generated INI and secrets are intentionally
not committed.

Run `python tools/windows-training/training-control.py check` before starting.
`start` now checks for the configured Ubuntu-24.04 WSL distribution and portable
MicroSIP files before spawning a persistent supervisor. Missing prerequisites
return exit2 without repeated restart attempts or credential changes.
`Start-Training.cmd` uses Python from PATH instead of a machine-specific venv.

On MainUser's PC (2026-09-15), only AlmaLinux-9 and docker-desktop were registered;
Ubuntu-24.04, the training Asterisk services and portable MicroSIP were absent.
The historical installed-stand instructions describe a different computer. A
fresh stand installation or an explicit existing server address is required.
