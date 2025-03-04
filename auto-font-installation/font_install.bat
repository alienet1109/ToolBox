@echo off
setlocal enabledelayedexpansion

rem Get the script's folder path as the source folder
set "source_folder=%~dp0"
set "font_folder=%source_folder%\font_files"

echo Installing fonts from %source_folder%

rem Process .zip files
for /r "%source_folder%" %%F in (*.zip) do (
    call :install_font "%%F"
)

echo Font installation completed!
pause
exit /b

:install_font
set "font_file=%~1"
echo Processing font archive: %~nx1

rem Create temporary folder
mkdir "%source_folder%\temp_fonts"

rem Extract using PowerShell
powershell -command "Expand-Archive -Path '%font_file%' -DestinationPath '%source_folder%\temp_fonts'"

rem Recursively copy all font files
for /r "%source_folder%\temp_fonts" %%G in (*.ttf;*.otf;*.fon;*.fnt) do (
    copy "%%G" "%font_folder%"
    echo Installed font: %%~nxG
)

rem Clean up temporary folder
rmdir /s /q "%source_folder%\temp_fonts"
exit /b