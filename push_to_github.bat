@echo off
chdir /d "%~dp0"
echo ========================================
echo   Pushing to GitHub...
echo ========================================
echo.
echo 等待网络恢复后，按任意键开始推送...
pause > nul
echo.
echo [1/2] 正在推送...
git push origin main
if %errorlevel% neq 0 (
    echo.
    echo [失败] 推送失败，等待 5 秒后重试...
    timeout /t 5 > nul
    echo.
    echo [2/2] 正在重试...
    git push origin main
    if %errorlevel% neq 0 (
        echo.
        echo [失败] 仍然失败，请检查网络连接
    ) else (
        echo.
        echo [成功] 代码已推送到 GitHub！
    )
) else (
    echo.
    echo [成功] 代码已推送到 GitHub！
)
echo.
echo ========================================
pause
