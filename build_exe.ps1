# Builds dist\LoLWinOverlay.exe: one file with the art, model and build data inside.
#   py -m pip install -r requirements.txt pyinstaller
#   .\build_exe.ps1
$here = $PSScriptRoot
py -m PyInstaller --noconfirm --onefile --noconsole --name LoLWinOverlay `
    --distpath "$here\dist" --workpath "$here\build" --specpath "$here\build" --paths "$here" `
    --add-data "$here\assets;assets" --add-data "$here\win_model.json;." `
    --add-data "$here\data\builds.json;data" --hidden-import pystray._win32 `
    "$here\lol_overlay.py"
