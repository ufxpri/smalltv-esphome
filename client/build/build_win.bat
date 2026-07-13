@echo off
REM Build the SmallTV Widget into a single windowed .exe (Windows).
REM Run from the client\ directory:  build\build_win.bat
setlocal
cd /d "%~dp0\.."
python -m pip install -r requirements-widget.txt pyinstaller || goto :err
python -m PyInstaller --noconfirm build\smalltv_widget.spec || goto :err
echo.
echo Done. See dist\SmallTVWidget.exe
goto :eof
:err
echo Build failed.
exit /b 1
