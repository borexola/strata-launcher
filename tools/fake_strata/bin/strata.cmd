@echo off
rem A stand-in for the strata command, for tests and screenshots. It is not Strata and loads no model.
if "%FAKE_STRATA_PYTHON%"=="" (set "FAKE_PY=python") else (set "FAKE_PY=%FAKE_STRATA_PYTHON%")
"%FAKE_PY%" "%~dp0..\fake_strata.py" %*
