@echo off
REM Windows verifier entry point -- task.toml sets [environment].os = "windows",
REM and Harbor has no shell there. Grades over COM against a RUNNING, licensed
REM SolidWorks session (pywin32 required): the candidate passed as %1, or the
REM active document when no argument is given. One JSON score envelope from
REM common/harness_base.py finalize() goes to stdout; the readable breakdown
REM goes to stderr, so a caller reading stdout gets the envelope and nothing
REM else.
python "%~dp0task\harness\harness.py" %*
exit /b %errorlevel%
